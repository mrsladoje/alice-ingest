import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROLE = os.path.abspath(os.path.join(HERE, "..", "..", "deploy", "roles",
                                    "shifter"))
STATIC_SRC = os.path.join(ROLE, "files", "live")
TEMPLATE = os.path.join(ROLE, "templates", "shifter-index.html.j2")
SERVER = os.path.join(ROLE, "files", "shifter.py")

LANE_PORT = os.environ.get("SHIFTER_PORT", "8092")
LANE_BIND = os.environ.get("SHIFTER_BIND", "127.0.0.1")
MOCK_PORT = os.environ.get("MOCK_OS_PORT", "9209")
CONTROL_PORT = os.environ.get("FEED_CONTROL_PORT", "8093")

ASSETS = ["shifter.js", "shifter.css", "alice-favicon.svg",
          "preact.umd.js", "hooks.umd.js", "preact-shim.js"]

VALUES = {
    "shifter_buffer_rows": "10000",
    "shifter_query_default_rows": "5000",
    "shifter_hidden_grace_seconds": "120",
    "shifter_cockpit_url": "https://opensearch.org/docs/latest/dashboards/",
}


def render_index(target):
    with open(TEMPLATE) as fh:
        text = fh.read()

    def sub(match):
        key = match.group(1).strip()
        if key not in VALUES:
            raise SystemExit(f"the page shell wants an unknown variable: {key}")
        return VALUES[key]

    text = re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", sub, text)
    with open(os.path.join(target, "index.html"), "w") as fh:
        fh.write(text)


def local_addresses():
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127.") and address not in found:
                found.append(address)
    except OSError:
        pass
    if not found:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            found.append(probe.getsockname()[0])
        except OSError:
            pass
        finally:
            probe.close()
    return found


def wait_for(url, label, tries=120):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(0.5)
    print(f"[run] {label} never answered at {url}", flush=True)
    return False


def main():
    static = tempfile.mkdtemp(prefix="shifterdev-")
    for name in ASSETS:
        shutil.copy(os.path.join(STATIC_SRC, name), static)
    render_index(static)

    procs = []

    mock_env = dict(os.environ, MOCK_OS_PORT=MOCK_PORT)
    procs.append(subprocess.Popen(
        [sys.executable, os.path.join(HERE, "mock_opensearch.py")],
        env=mock_env))

    lane_env = dict(
        os.environ,
        SHIFTER_BIND=LANE_BIND,
        SHIFTER_PORT=LANE_PORT,
        SHIFTER_STATIC_DIR=static,
        SHIFTER_OS_URL=f"http://127.0.0.1:{MOCK_PORT}",
        SHIFTER_OS_INDICES="infologger,application-logs-central",
        SHIFTER_QUERY_MAX_ROWS="20000",
        PYTHONUNBUFFERED="1",
    )
    procs.append(subprocess.Popen([sys.executable, SERVER], env=lane_env))

    if not wait_for(f"http://127.0.0.1:{LANE_PORT}/healthz", "the lane"):
        for p in procs:
            p.terminate()
        return 1
    wait_for(f"http://127.0.0.1:{MOCK_PORT}/", "the mock OpenSearch")

    feed_env = dict(
        os.environ,
        FEED_LANE_URL=f"http://127.0.0.1:{LANE_PORT}/ingest",
        FEED_CONTROL_PORT=CONTROL_PORT,
        PYTHONUNBUFFERED="1",
    )
    procs.append(subprocess.Popen(
        [sys.executable, os.path.join(HERE, "feeder.py")], env=feed_env))

    print("", flush=True)
    print(f"  shifter view    http://127.0.0.1:{LANE_PORT}/", flush=True)
    if LANE_BIND == "0.0.0.0":
        for address in local_addresses():
            print(f"  from your phone http://{address}:{LANE_PORT}/", flush=True)
        print("  NOTE: bound to every interface. On the CERN network that "
              "address is routable,", flush=True)
        print("        so anyone on the network can open it. Stop this when "
              "you are done.", flush=True)
    print(f"  force a burst   curl http://127.0.0.1:{CONTROL_PORT}/burst", flush=True)
    print(f"  stop the burst  curl http://127.0.0.1:{CONTROL_PORT}/calm", flush=True)
    print(f"  lane health     curl http://127.0.0.1:{LANE_PORT}/healthz", flush=True)
    print(f"  static page     {static}", flush=True)
    print("", flush=True)
    print("  Ctrl-C stops everything.", flush=True)
    print("", flush=True)

    def stop(_signum, _frame):
        for p in procs:
            p.terminate()
        shutil.rmtree(static, ignore_errors=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    while True:
        for p in procs:
            if p.poll() is not None:
                print(f"[run] a process exited with {p.returncode}; stopping", flush=True)
                stop(None, None)
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
