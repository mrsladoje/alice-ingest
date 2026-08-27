#!/usr/bin/env python3
"""Read cgroup and /proc counters for a set of containers, once a second.

Round 1 sampled memory for one container. Round 2 rations processor time, so
every service in the rig has to be read: how much processor time it used, how
much disk traffic it caused, and which cores it was actually allowed.

Docker on this laptop runs inside a Colima virtual machine, so the counters
live in that machine, not on the host. Opening one `colima ssh` per file per
second would cost more than the thing being measured. Instead a single shell
is opened once and held, and each sample is one `grep` across every file of
every container — about one millisecond for the whole rig.

On a Linux host with the files present locally, the same reader works without
the virtual machine.
"""

import os
import subprocess
import threading
import time

CGROUP_PATTERNS = [
    "/sys/fs/cgroup/docker/%s",
    "/sys/fs/cgroup/system.slice/docker-%s.scope",
    "/sys/fs/cgroup/system.slice/containerd-%s.scope",
]
CGROUP_FILES = ["cpu.stat", "io.stat", "io.pressure", "cpu.pressure",
                "memory.current", "memory.peak", "memory.events",
                "cpuset.cpus.effective"]
END = "__soakrec_end__"


def _run(argv, timeout=20):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except Exception:
        return 1, ""
    return proc.returncode, proc.stdout.strip()


def colima_profile():
    code, out = _run(["colima", "list", "--json"])
    if code != 0:
        return ""
    for line in out.split("\n"):
        try:
            import json
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("status") == "Running":
            return entry.get("name", "")
    return ""


class Shell:
    """One long-lived shell, either in the Colima machine or on this host."""

    def __init__(self, prefer_vm=True):
        self.proc = None
        self.where = "none"
        self.lock = threading.Lock()
        if prefer_vm and self._start_vm():
            self.where = "colima"
        elif self._start_local():
            self.where = "local"

    def _spawn(self, argv):
        try:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except Exception:
            self.proc = None
            return False
        return self.ask("echo ready").strip() == "ready"

    def _start_vm(self):
        profile = colima_profile()
        if not profile:
            return False
        argv = ["colima", "ssh"]
        if profile != "default":
            argv += ["--profile", profile]
        argv += ["--", "sh", "-s"]
        return self._spawn(argv)

    def _start_local(self):
        if not os.path.isdir("/sys/fs/cgroup"):
            return False
        return self._spawn(["sh", "-s"])

    def ask(self, script):
        if self.proc is None or self.proc.poll() is not None:
            return ""
        with self.lock:
            try:
                self.proc.stdin.write(script + "\necho " + END + "\n")
                self.proc.stdin.flush()
            except Exception:
                return ""
            lines = []
            for line in self.proc.stdout:
                if line.strip() == END:
                    break
                lines.append(line)
            return "".join(lines)

    def close(self):
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


def find_container(pattern):
    """`compose run` invents its own container name, so the generator cannot
    be watched by an exact name. A leading ~ means "the running container
    whose name contains this"."""
    if not pattern.startswith("~"):
        return pattern
    needle = pattern[1:]
    code, out = _run(["docker", "ps", "--format", "{{.Names}}"])
    if code != 0:
        return ""
    for name in out.split("\n"):
        name = name.strip()
        if needle in name:
            return name
    return ""


class Probe:
    """The counters for one container, plus where to find them.

    A container that is not running yet stays unresolved and is retried, so a
    service that starts late is still measured rather than silently missing.
    """

    def __init__(self, label, container, shell):
        self.label = label
        self.pattern = container
        self.container = container
        self.shell = shell
        self.cgroup = ""
        self.pid = 0
        self.files = []
        self.cpus = 0
        self.resolve()

    def resolve(self):
        name = find_container(self.pattern)
        if not name:
            return False
        self.container = name
        code, ident = _run(["docker", "inspect", "-f", "{{.Id}}", self.container])
        if code != 0 or not ident:
            return False
        code, pid = _run(["docker", "inspect", "-f", "{{.State.Pid}}",
                          self.container])
        self.pid = int(pid) if code == 0 and pid.isdigit() else 0
        for pattern in CGROUP_PATTERNS:
            path = pattern % ident
            if self.shell.ask("test -f %s/cpu.stat && echo yes" % path).strip() == "yes":
                self.cgroup = path
                break
        if not self.cgroup:
            return False
        self.files = ["%s/%s" % (self.cgroup, name) for name in CGROUP_FILES]
        self.cpus = self.count_cpus()
        return True

    def count_cpus(self):
        text = self.shell.ask("cat %s/cpuset.cpus.effective 2>/dev/null"
                              % self.cgroup).strip()
        return cpuset_size(text)


def cpuset_size(text):
    total = 0
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, _, high = part.partition("-")
            try:
                total += int(high) - int(low) + 1
            except ValueError:
                pass
        elif part.isdigit():
            total += 1
    return total


EMPTY = {
    "cpu_usec": 0, "user_usec": 0, "sys_usec": 0, "nr_throttled": 0,
    "throttled_usec": 0, "rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0,
    "io_stall_usec": 0, "cpu_stall_usec": 0, "mem_current": 0, "mem_peak": 0,
    "ev_high": 0, "ev_max": 0, "ev_oom": 0, "ncpus": 0,
}


class Sampler:
    """Samples every probe, and the collector's threads, in one round trip."""

    def __init__(self, probes, thread_label="", clock_ticks=100):
        self.probes = probes
        self.thread_label = thread_label
        self.clock_ticks = clock_ticks or 100
        self.rebuild()

    def rebuild(self):
        self.by_cgroup = {probe.cgroup: probe.label for probe in self.probes
                          if probe.cgroup}
        self.thread_pid = 0
        for probe in self.probes:
            if probe.label == self.thread_label:
                self.thread_pid = probe.pid
        self.command = self._build()

    def refresh(self):
        """Retry anything not yet running. Cheap enough for once every few
        seconds, far too expensive for every tick."""
        changed = False
        for probe in self.probes:
            if not probe.cgroup and probe.resolve():
                changed = True
        if changed:
            self.rebuild()
        return changed

    def _build(self):
        files = []
        for probe in self.probes:
            files.extend(probe.files)
        if self.thread_pid:
            files.append("/proc/%d/task/*/stat" % self.thread_pid)
        if not files:
            return ""
        return "grep -sH '' %s" % " ".join(files)

    def sample(self):
        rows = {probe.label: dict(EMPTY) for probe in self.probes}
        threads = {}
        if not self.command or not self.probes:
            return rows, threads
        text = self.probes[0].shell.ask(self.command)
        for line in text.split("\n"):
            path, _, body = line.partition(":")
            if not body:
                continue
            if path.startswith("/proc/"):
                self._thread(path, body, threads)
                continue
            folder, _, name = path.rpartition("/")
            label = self.by_cgroup.get(folder)
            if label is None:
                continue
            self._counter(rows[label], name, body)
        for probe in self.probes:
            rows[probe.label]["ncpus"] = probe.cpus
        return rows, threads

    def _counter(self, row, name, body):
        body = body.strip()
        if name == "cpu.stat":
            key, _, value = body.partition(" ")
            mapping = {"usage_usec": "cpu_usec", "user_usec": "user_usec",
                       "system_usec": "sys_usec", "nr_throttled": "nr_throttled",
                       "throttled_usec": "throttled_usec"}
            if key in mapping and value.strip().isdigit():
                row[mapping[key]] = int(value.strip())
        elif name == "io.stat":
            for token in body.split():
                key, _, value = token.partition("=")
                if key in ("rbytes", "wbytes", "rios", "wios") and value.isdigit():
                    row[key] += int(value)
        elif name == "io.pressure":
            if body.startswith("some"):
                row["io_stall_usec"] = _psi_total(body)
        elif name == "cpu.pressure":
            if body.startswith("some"):
                row["cpu_stall_usec"] = _psi_total(body)
        elif name == "memory.current":
            if body.isdigit():
                row["mem_current"] = int(body)
        elif name == "memory.peak":
            if body.isdigit():
                row["mem_peak"] = int(body)
        elif name == "memory.events":
            key, _, value = body.partition(" ")
            value = value.strip()
            if not value.isdigit():
                return
            if key == "high":
                row["ev_high"] = int(value)
            elif key == "max":
                row["ev_max"] = int(value)
            elif key == "oom_kill":
                row["ev_oom"] = int(value)
        elif name == "cpuset.cpus.effective":
            row["ncpus"] = cpuset_size(body)

    def _thread(self, path, body, threads):
        parts = path.split("/")
        if len(parts) < 5:
            return
        tid = parts[4]
        close = body.rfind(")")
        open_paren = body.find("(")
        if close < 0 or open_paren < 0:
            return
        comm = body[open_paren + 1:close]
        fields = body[close + 2:].split()
        if len(fields) < 13:
            return
        try:
            utime, stime = int(fields[11]), int(fields[12])
        except ValueError:
            return
        factor = 1000000 // self.clock_ticks
        threads[tid] = {"comm": comm, "user_usec": utime * factor,
                        "sys_usec": stime * factor,
                        "cpu_usec": (utime + stime) * factor}


def _psi_total(body):
    for token in body.split():
        key, _, value = token.partition("=")
        if key == "total" and value.isdigit():
            return int(value)
    return 0


def clock_ticks(shell):
    text = shell.ask("getconf CLK_TCK 2>/dev/null").strip()
    return int(text) if text.isdigit() else 100


if __name__ == "__main__":
    import json
    import sys
    shell = Shell()
    probes = []
    for spec in sys.argv[1:]:
        label, _, container = spec.partition("=")
        probe = Probe(label, container or label, shell)
        probes.append(probe)
        if not probe.cgroup:
            print("not running yet: %s" % container, file=sys.stderr)
    sampler = Sampler(probes, probes[0].label if probes else "",
                      clock_ticks(shell))
    start = time.time()
    rows, threads = sampler.sample()
    print("shell=%s sample=%.1f ms" % (shell.where, (time.time() - start) * 1000))
    print(json.dumps({"containers": rows, "threads": threads}, indent=2))
    shell.close()
