#!/usr/bin/env python3
"""A thin InfoLogger tap: accept the socket, append to a file.

Round 1 found InfoLogger losing every record a fifteen-minute outage touched,
while DDS and stdout lost nothing. The reason is not subtle — for the tailed
families the log file *is* the queue, and the `tcp` input has nothing behind
the socket at all.

This is the `s1` arm: the same records arrive on the same port, but they land
in a file first and the collector tails it. It is deliberately the smallest
thing that can be measured — one thread per connection, an append, and a
periodic size-based rotation — because the arm has to price the mechanism, not
this program.

The real system may not need it. `infoLoggerD` already keeps a persistent local
queue until the server acknowledges, and Thanasis's Run 3 collector tailed the
O2 process files and never touched InfoLogger at all. See the plan's
"InfoLogger tap" section.
"""

import argparse
import os
import signal
import socket
import socketserver
import sys
import threading
import time

STATE = {"records": 0, "bytes": 0, "rotations": 0, "connections": 0}


class Appender:
    """One file, one lock, one rotation rule."""

    def __init__(self, directory, name, max_bytes):
        self.directory = directory
        self.name = name
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.index = 0
        self.handle = None
        self.written = 0
        os.makedirs(directory, exist_ok=True)
        self._open()

    def _path(self):
        return os.path.join(self.directory, "%s.%d.log" % (self.name, self.index))

    def _open(self):
        self.handle = open(self._path(), "a", buffering=1024 * 256)
        self.written = self.handle.tell()

    def write(self, payload):
        with self.lock:
            self.handle.write(payload)
            self.written += len(payload)
            STATE["bytes"] += len(payload)
            STATE["records"] += payload.count("\n")
            if self.max_bytes and self.written >= self.max_bytes:
                self.handle.flush()
                self.handle.close()
                self.index += 1
                STATE["rotations"] += 1
                self._open()

    def flush(self):
        with self.lock:
            if self.handle is not None:
                self.handle.flush()

    def close(self):
        with self.lock:
            if self.handle is not None:
                self.handle.flush()
                self.handle.close()
                self.handle = None


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        STATE["connections"] += 1
        appender = self.server.appender
        tail = ""
        while True:
            try:
                chunk = self.connection.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            text = tail + chunk.decode("utf-8", "replace")
            cut = text.rfind("\n")
            if cut < 0:
                tail = text
                continue
            appender.write(text[: cut + 1])
            tail = text[cut + 1:]
        if tail:
            appender.write(tail + "\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def flusher(appender, seconds, stop):
    """The collector tails this file, so a record sitting in a buffer is a
    record that has not arrived. Flush on a timer as well as on rotation."""
    while not stop.wait(seconds):
        appender.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5170)
    parser.add_argument("--dir", default="/var/log/node/infologger")
    parser.add_argument("--name", default="infologger")
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--flush-seconds", type=float, default=0.5)
    parser.add_argument("--stats-file", default="")
    args = parser.parse_args()

    appender = Appender(args.dir, args.name, args.max_bytes)
    stop = threading.Event()
    threading.Thread(target=flusher, args=(appender, args.flush_seconds, stop),
                     daemon=True).start()

    if args.stats_file:
        def report():
            while not stop.wait(1.0):
                try:
                    with open(args.stats_file, "w") as handle:
                        handle.write(str(STATE))
                except OSError:
                    pass
        threading.Thread(target=report, daemon=True).start()

    server = Server(("0.0.0.0", args.port), Handler)
    server.appender = appender

    def shutdown(*_):
        stop.set()
        server.shutdown()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print("appender: listening on %d, writing %s/%s.N.log"
          % (args.port, args.dir, args.name), flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        appender.close()
        print("appender: %d records, %d bytes, %d rotations"
              % (STATE["records"], STATE["bytes"], STATE["rotations"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
