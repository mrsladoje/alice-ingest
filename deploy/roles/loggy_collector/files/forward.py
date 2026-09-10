import base64
import gzip
import os
import socket
import struct
import threading
import time

import msgpack

EVENT_TIME_TYPE = 0
CHUNK_OPTION = "chunk"
SIZE_OPTION = "size"
COMPRESSED_OPTION = "compressed"
ACK_KEY = "ack"

MAX_BUFFER_BYTES = 256 * 1024 * 1024
READ_BYTES = 1024 * 1024

ENTRY_HEAD = b"\x92\xd7\x00"
FIXMAP_MAX = 15
MAP16 = b"\xde"
MAP32 = b"\xdf"
ARRAY_HEADS = frozenset([0xdc, 0xdd] + list(range(0x90, 0xa0)))
_MAP_HEADERS = [bytes((0x80 | n,)) if n <= FIXMAP_MAX
                else MAP16 + struct.pack(">H", n) for n in range(1024)]


class ForwardError(Exception):
    pass


class ProtocolError(ForwardError):
    pass


class AckTimeout(ForwardError):
    pass


def _ext_hook(code, data):
    if code == EVENT_TIME_TYPE and len(data) == 8:
        seconds, nanos = struct.unpack(">II", data)
        return seconds * 1000 + nanos // 1000000
    return msgpack.ExtType(code, data)


def event_time(epoch_ms):
    seconds, millis = divmod(int(epoch_ms), 1000)
    return msgpack.ExtType(EVENT_TIME_TYPE,
                           struct.pack(">II", seconds, millis * 1000000))


def _time_ms(value):
    if isinstance(value, (list, tuple)) and value:
        return _time_ms(value[0])
    if isinstance(value, bool):
        raise ProtocolError("a boolean is not an event time")
    if isinstance(value, int):
        return value * 1000 if value < 100000000000 else value
    if isinstance(value, float):
        return int(value * 1000)
    raise ProtocolError("unreadable event time %r" % (value,))


def _entry(item):
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        raise ProtocolError("an entry is [time, record], got %r" % (item,))
    when, record = item
    if not isinstance(record, dict):
        raise ProtocolError("a record is a map, got %r" % (record,))
    return _time_ms(when), record


def _unpacker():
    return msgpack.Unpacker(raw=False, ext_hook=_ext_hook, strict_map_key=False,
                            max_buffer_size=MAX_BUFFER_BYTES)


def _entries_from_bytes(blob, compressed):
    if compressed:
        blob = gzip.decompress(blob)
    unpacker = _unpacker()
    unpacker.feed(blob)
    return [_entry(item) for item in unpacker]


def decode_message(message):
    if not isinstance(message, (list, tuple)) or len(message) < 2:
        raise ProtocolError("a forward message is [tag, ...], got %r"
                            % (message,))
    tag = message[0]
    if not isinstance(tag, str):
        raise ProtocolError("the tag is a string, got %r" % (tag,))
    options = {}
    body = message[1]
    if isinstance(body, (bytes, bytearray)):
        if len(message) > 2 and isinstance(message[2], dict):
            options = message[2]
        compressed = options.get(COMPRESSED_OPTION) == "gzip"
        entries = _entries_from_bytes(bytes(body), compressed)
    elif isinstance(body, (list, tuple)):
        if len(message) > 2 and isinstance(message[2], dict):
            options = message[2]
        entries = [_entry(item) for item in body]
    else:
        if len(message) < 3:
            raise ProtocolError("a message-mode event is [tag, time, record]")
        if len(message) > 3 and isinstance(message[3], dict):
            options = message[3]
        entries = [_entry((body, message[2]))]
    return tag, entries, options


def encode_forward(tag, entries, chunk_id=None):
    body = [[event_time(when), record] for when, record in entries]
    options = {SIZE_OPTION: len(body)}
    if chunk_id is not None:
        options[CHUNK_OPTION] = chunk_id
    return msgpack.packb([tag, body, options], use_bin_type=True)


def pack_field(key, value):
    return (msgpack.packb(key, use_bin_type=True)
            + msgpack.packb(value, use_bin_type=True))


def map_header(pairs):
    if pairs < len(_MAP_HEADERS):
        return _MAP_HEADERS[pairs]
    if pairs < 65536:
        return MAP16 + struct.pack(">H", pairs)
    return MAP32 + struct.pack(">I", pairs)


def array_header(items):
    if items < 16:
        return bytes((0x90 | items,))
    if items < 65536:
        return b"\xdc" + struct.pack(">H", items)
    return b"\xdd" + struct.pack(">I", items)


def entry_prefix(epoch_ms):
    seconds, millis = divmod(int(epoch_ms), 1000)
    return ENTRY_HEAD + struct.pack(">II", seconds, millis * 1000000)


def _record_span(blob, start):
    head = blob[start]
    if head == 0xde:
        return start + 3, (blob[start + 1] << 8) | blob[start + 2]
    if head == 0xdf:
        return start + 5, struct.unpack_from(">I", blob, start + 1)[0]
    if 0x80 <= head <= 0x8f:
        return start + 1, head & 0x0f
    raise ProtocolError("a record is a map, got type byte %02x" % head)


def decode_chunk(blob):
    unpacker = _unpacker()
    unpacker.feed(blob)
    fields = unpacker.read_array_header()
    if fields < 2:
        raise ProtocolError("a forward message is [tag, ...], got %d fields"
                            % fields)
    tag = unpacker.unpack()
    if not isinstance(tag, str):
        raise ProtocolError("the tag is a string, got %r" % (tag,))
    if blob[unpacker.tell()] not in ARRAY_HEADS:
        return None
    count = unpacker.read_array_header()
    entries = []
    frames = []
    tell = unpacker.tell
    unpack = unpacker.unpack
    for _ in range(count):
        if unpacker.read_array_header() != 2:
            raise ProtocolError("an entry is [time, record]")
        when = _time_ms(unpack())
        start = tell()
        record = unpack()
        end = tell()
        if not isinstance(record, dict):
            raise ProtocolError("a record is a map, got %r" % (record,))
        cut, pairs = _record_span(blob, start)
        entries.append((when, record))
        frames.append((entry_prefix(when), pairs, blob[cut:end]))
    options = unpack() if fields > 2 else {}
    if not isinstance(options, dict):
        options = {}
    return tag, entries, options, frames


def encode_spliced(tag, frames, tails, chunk_id=None):
    if len(frames) != len(tails):
        raise ProtocolError("%d frames against %d stamps"
                            % (len(frames), len(tails)))
    parts = [b"\x93", msgpack.packb(tag, use_bin_type=True),
             array_header(len(frames))]
    append = parts.append
    headers = _MAP_HEADERS
    limit = len(headers)
    for (prefix, pairs, body), (tail, extra) in zip(frames, tails):
        append(prefix)
        pairs += extra
        append(headers[pairs] if pairs < limit else map_header(pairs))
        append(body)
        append(tail)
    options = {SIZE_OPTION: len(frames)}
    if chunk_id is not None:
        options[CHUNK_OPTION] = chunk_id
    append(msgpack.packb(options, use_bin_type=True))
    return b"".join(parts)


def new_chunk_id():
    return base64.b64encode(os.urandom(16)).decode("ascii")


class ForwardServer(object):

    def __init__(self, path, handler, mode=0o660, backlog=16):
        self.path = path
        self.handler = handler
        self.mode = mode
        self.backlog = backlog
        self.listener = None
        self.threads = []
        self.waiting = 0
        self.serving = threading.Lock()
        self._stop = threading.Event()
        self._accept_thread = None
        self._lock = threading.Lock()

    def open(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.path)
        os.chmod(self.path, self.mode)
        listener.listen(self.backlog)
        listener.settimeout(0.5)
        self.listener = listener
        return self

    def start(self):
        if self.listener is None:
            self.open()
        self._accept_thread = threading.Thread(target=self._accept,
                                               name="forward-accept",
                                               daemon=True)
        self._accept_thread.start()
        return self

    def _accept(self):
        while not self._stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            thread = threading.Thread(target=self._serve, args=(connection,),
                                      name="forward-connection", daemon=True)
            with self._lock:
                self.threads = [t for t in self.threads if t.is_alive()]
                self.threads.append(thread)
            thread.start()

    def _serve(self, connection):
        pending = bytearray()
        connection.settimeout(None)
        try:
            while not self._stop.is_set():
                data = connection.recv(READ_BYTES)
                if not data:
                    return
                pending.extend(data)
                if len(pending) > MAX_BUFFER_BYTES:
                    raise ProtocolError("a forward message exceeded %d bytes"
                                        % MAX_BUFFER_BYTES)
                for blob in self._frame(pending):
                    self._dispatch(connection, blob)
        except (OSError, ForwardError, ValueError):
            return
        except Exception:
            return
        finally:
            try:
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _frame(pending):
        scanner = _unpacker()
        scanner.feed(bytes(pending))
        blobs = []
        pos = 0
        while True:
            try:
                scanner.skip()
            except msgpack.OutOfData:
                break
            end = scanner.tell()
            blobs.append(bytes(pending[pos:end]))
            pos = end
        del pending[:pos]
        return blobs

    def _dispatch(self, connection, blob):
        decoded = decode_chunk(blob)
        if decoded is None:
            tag, entries, options = decode_message(
                msgpack.unpackb(blob, raw=False, ext_hook=_ext_hook,
                                strict_map_key=False))
            frames = None
        else:
            tag, entries, options, frames = decoded
        with self._lock:
            self.waiting += 1
        self.serving.acquire()
        try:
            with self._lock:
                self.waiting -= 1
            self.handler(tag, entries, options, frames)
        finally:
            self.serving.release()
        chunk = options.get(CHUNK_OPTION)
        if chunk is not None:
            connection.sendall(msgpack.packb({ACK_KEY: chunk},
                                             use_bin_type=True))

    def backlog_size(self):
        with self._lock:
            return self.waiting

    def close(self):
        self._stop.set()
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(2.0)
        with self._lock:
            threads = list(self.threads)
        for thread in threads:
            thread.join(2.0)
        try:
            os.unlink(self.path)
        except OSError:
            pass


class ForwardClient(object):

    def __init__(self, path, ack_timeout=30.0, connect_timeout=5.0):
        self.path = path
        self.ack_timeout = ack_timeout
        self.connect_timeout = connect_timeout
        self._socket = None
        self._unpacker = _unpacker()

    def _connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)
        sock.connect(self.path)
        sock.settimeout(self.ack_timeout)
        self._socket = sock
        self._unpacker = _unpacker()

    def close(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None

    def send(self, tag, entries, chunk_id=None):
        chunk = chunk_id or new_chunk_id()
        return self.send_payload(encode_forward(tag, entries, chunk), chunk)

    def send_payload(self, payload, chunk):
        if self._socket is None:
            self._connect()
        try:
            self._socket.sendall(payload)
            deadline = time.monotonic() + self.ack_timeout
            while True:
                for message in self._unpacker:
                    if isinstance(message, dict) and message.get(ACK_KEY) == chunk:
                        return chunk
                    raise ProtocolError("unexpected answer %r while waiting "
                                        "for the acknowledgement of %s"
                                        % (message, chunk))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AckTimeout("no acknowledgement for chunk %s within "
                                     "%.0f seconds" % (chunk, self.ack_timeout))
                self._socket.settimeout(remaining)
                data = self._socket.recv(READ_BYTES)
                if not data:
                    raise ForwardError("the return socket closed before it "
                                       "acknowledged chunk %s" % chunk)
                self._unpacker.feed(data)
        except (OSError, ForwardError):
            self.close()
            raise
