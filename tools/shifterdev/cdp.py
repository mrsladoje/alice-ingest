import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

CHROME = os.environ.get("SHIFTER_CHROME") or next(
    (p for p in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ) if os.path.exists(p)), "")
def _free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


PORT = int(os.environ.get("SHIFTER_CDP_PORT") or _free_port())


class WS:
    def __init__(self, url):
        rest = url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        path = "/" + path
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-5AB0DC85B11").encode()).digest())
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _recv(self, n):
        while len(self.rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, text):
        payload = text.encode()
        mask = os.urandom(4)
        n = len(payload)
        header = b"\x81"
        if n < 126:
            header += struct.pack("!B", 0x80 | n)
        elif n < 65536:
            header += struct.pack("!BH", 0x80 | 126, n)
        else:
            header += struct.pack("!BQ", 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv(self):
        first = self._recv(2)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv(8))[0]
        return self._recv(length).decode("utf-8", "replace")

    def call(self, method, params=None):
        self.next_id += 1
        mid = self.next_id
        self.send(json.dumps({"id": mid, "method": method,
                              "params": params or {}}))
        deadline = time.time() + 40
        while time.time() < deadline:
            msg = json.loads(self.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})
        raise TimeoutError(method)

    def js(self, expression):
        out = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
            "awaitPromise": True})
        return out.get("result", {}).get("value")


def launch(url, profile):
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--window-size=1760,1000",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            data = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/json", timeout=2).read())
            pages = [t for t in data if t.get("type") == "page"
                     and t.get("webSocketDebuggerUrl")]
            if pages:
                return proc, pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    proc.terminate()
    raise SystemExit("chrome never came up")


def shot(ws, path):
    out = ws.call("Page.captureScreenshot", {"format": "png"})
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(out["data"]))
    print("wrote", path)
