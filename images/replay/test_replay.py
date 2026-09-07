#!/usr/bin/env python3
"""Tests for the two things the replay engine reconstructs.

Both exist because the archive does not carry them. A process log has no date
on its lines and, once every process of a node was flattened into one file, no
program on its records either. Everything downstream — severity tiering, the
per-program view, any correlation with the journal — rests on these two.

Run: python3 images/replay/test_replay.py
"""
import io
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("S3_BUCKET", "test")
import replay  # noqa: E402


class EventTimeFromName(unittest.TestCase):
    def test_real_name(self):
        got = replay._stdout_event_ts(
            "run/epn146.internal/mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log")
        self.assertEqual(got, ("2026-06-20", 12 * 3600 + 15 * 60 + 21))

    def test_name_without_a_timestamp(self):
        self.assertIsNone(replay._stdout_event_ts("run/epn146.internal/notes.log"))


class Clock(unittest.TestCase):
    def stamp(self, clock, line):
        return clock.stamp(line).decode()

    def test_takes_the_time_from_the_line_not_the_file(self):
        # The process started at 12:15:21 and this line was printed five minutes
        # later. Stamping every line with the start time is what made a
        # half-hour process land on one second.
        clock = replay._StdoutClock("2026-06-20", 12 * 3600 + 15 * 60 + 21)
        got = self.stamp(clock, b"[12:20:34][INFO] New error registered\n")
        self.assertTrue(got.startswith("2026-06-20 12:20:34."), got)

    def test_a_line_with_no_clock_inherits_the_one_before_it(self):
        clock = replay._StdoutClock("2026-06-20", 0)
        self.stamp(clock, b"[12:20:34][INFO] first\n")
        got = self.stamp(clock, b"Loading O2PDPSuite/epn-20260615\n")
        self.assertTrue(got.startswith("2026-06-20 12:20:34."), got)

    def test_ordering_is_preserved_inside_one_second(self):
        clock = replay._StdoutClock("2026-06-20", 0)
        first = self.stamp(clock, b"[12:20:34][INFO] a\n")
        second = self.stamp(clock, b"[12:20:34][INFO] b\n")
        self.assertLess(first, second)

    def test_a_run_crossing_midnight_advances_the_date(self):
        # Real logs contain [00:32:15] lines from runs that started the previous
        # evening. Without this the whole night lands a day early.
        clock = replay._StdoutClock("2026-06-20", 23 * 3600 + 59 * 60)
        self.stamp(clock, b"[23:59:58][INFO] before\n")
        got = self.stamp(clock, b"[00:32:15][INFO] after\n")
        self.assertTrue(got.startswith("2026-06-21 00:32:15."), got)

    def test_a_small_step_backwards_is_not_midnight(self):
        clock = replay._StdoutClock("2026-06-20", 0)
        self.stamp(clock, b"[12:20:34][INFO] a\n")
        got = self.stamp(clock, b"[12:20:33][INFO] out of order\n")
        self.assertTrue(got.startswith("2026-06-20 12:20:33."), got)

    def test_the_clock_is_readable_through_terminal_colour(self):
        # ErrorMonitorTask writes to its file as though it were a terminal.
        clock = replay._StdoutClock("2026-06-20", 0)
        got = self.stamp(clock, b"[\x1b[01;36m16:31:31\x1b[0m][INFO] state\n")
        self.assertTrue(got.startswith("2026-06-20 16:31:31."), got)

    def test_a_sub_second_clock_is_used_as_written(self):
        clock = replay._StdoutClock("2026-06-20", 0)
        got = self.stamp(clock, b"[12:20:34.125][INFO] a\n")
        self.assertEqual(got, "2026-06-20 12:20:34.125000 ")


class ProgramIdentity(unittest.TestCase):
    def test_the_member_basename_is_what_carries_the_program(self):
        # The collector recovers `program` from this name, so the engine must
        # keep it. Flattening the members into one file destroyed it.
        name = "run/epn146.internal/mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log"
        self.assertEqual(os.path.basename(name),
                         "mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log")

    def test_the_member_pattern_still_matches_both_streams(self):
        for suffix in ("_out.log", "_err.log"):
            self.assertTrue(replay._STDOUT_MEMBER_RE.search("x" + suffix), suffix)


class FakeS3:
    """Enough of the S3 client for replay_tarballs: one run tarball, one host."""

    def __init__(self, blob, key):
        self.blob, self.key = blob, key

    def get_paginator(self, _):
        return self

    def paginate(self, **_):
        return [{"Contents": [{"Key": self.key, "Size": len(self.blob)}]}]

    def get_object(self, **_):
        return {"Body": io.BytesIO(self.blob)}


def one_tarball(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


class TarballDispatch(unittest.TestCase):
    """The behaviour the whole round turns on: one file per process, kept under
    the name the farm gave it, with a real event time in front of every
    record-start line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reset = []
        for name, value in [
                ("NODES_ROOT", self.tmp), ("NODE_COUNT", 1),
                ("COLLECTOR_HOSTS", ["node-01"]), ("RUN_TAG", "RUN"),
                ("STDOUT_MAX_MEMBERS", 10), ("STDOUT_MAX_LINES", 100),
                ("STDOUT_MAX_OBJECTS", 1), ("DDS_MAX_OBJECTS", 1),
                ("STDOUT_RATE", 0), ("DDS_RATE", 0)]:
            self.reset.append((name, getattr(replay, name)))
            setattr(replay, name, value)

    def tearDown(self):
        for name, value in self.reset:
            setattr(replay, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_one(self, members):
        s3 = FakeS3(one_tarball(members), "dds/RUN_epn146.tar.gz")
        replay.replay_tarballs(s3, threading.Event(), True, True)
        return os.path.join(self.tmp, "node-01")

    def test_each_process_gets_its_own_file_under_its_host(self):
        root = self.run_one([
            ("run/epn146.internal/mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log",
             b"[12:15:25][INFO] tracking\n"),
            ("run/epn146.internal/TfBuilderTask_reco1_2026-06-20-12-15-19_1234_out.log",
             b"[2026-06-20 12:15:23.825][I] sink created\n"),
            ("run/epn146.internal/dds_2026-06-20.0.log",
             b"2026-06-20 12:15:12.9   inf  dds-agent  <0x0:0x0>  up\n"),
        ])
        host_dir = os.path.join(root, "stdout", "epn146")
        self.assertEqual(
            sorted(os.listdir(host_dir)),
            ["TfBuilderTask_reco1_2026-06-20-12-15-19_1234_out.log",
             "mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log"])
        # dds keeps its own shape: one firehose per node, named after the node.
        self.assertTrue(os.path.exists(os.path.join(root, "dds", "epn146.log")))

    def test_the_line_carries_the_event_time_the_clock_rebuilt(self):
        root = self.run_one([
            ("run/epn146.internal/mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log",
             b"[12:20:34][INFO] New error registered\n  indented continuation\n"),
        ])
        path = os.path.join(root, "stdout", "epn146",
                            "mft-tracker_t0_reco3_2026-06-20-12-15-21_9613_out.log")
        with open(path) as fh:
            lines = fh.read().splitlines()
        self.assertTrue(lines[0].startswith("2026-06-20 12:20:34."), lines[0])
        self.assertIn("[12:20:34][INFO] New error registered", lines[0])
        # A continuation keeps its indent, or the collector cannot fold it.
        self.assertTrue(lines[1].startswith("  "), lines[1])


class CapturedFamilies(unittest.TestCase):
    """The three families with no S3 archive behind them.

    Each must be skippable without failing, because a VM with no captured
    bundle is the ordinary case and a replay that dies on a missing optional
    source takes the other three families down with it.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reset = [("BUNDLE_ROOT", replay.BUNDLE_ROOT),
                      ("ILDAEMON_PATH", replay.ILDAEMON_PATH),
                      ("ILDAEMON_RATE", replay.ILDAEMON_RATE),
                      ("ODC_DIR", replay.ODC_DIR),
                      ("ODC_RATE", replay.ODC_RATE)]
        replay.ILDAEMON_RATE = 0
        replay.ILDAEMON_PATH = os.path.join(self.tmp, "daemon.log")
        replay.ODC_RATE = 0
        replay.ODC_DIR = os.path.join(self.tmp, "odc-out")

    def tearDown(self):
        for name, value in self.reset:
            setattr(replay, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_bundle_skips_rather_than_fails(self):
        replay.BUNDLE_ROOT = ""
        for result in (replay.replay_ildaemon(threading.Event()),
                       replay.replay_journald(threading.Event()),
                       replay.replay_odc(threading.Event())):
            self.assertEqual(result.get("skipped"), "no bundle", result)

    def test_an_empty_bundle_skips_too(self):
        replay.BUNDLE_ROOT = self.tmp
        for result in (replay.replay_ildaemon(threading.Event()),
                       replay.replay_journald(threading.Event()),
                       replay.replay_odc(threading.Event())):
            self.assertEqual(result.get("skipped"), "no capture", result)

    def test_the_daemon_log_is_written_where_the_collector_tails(self):
        replay.BUNDLE_ROOT = self.tmp
        os.makedirs(os.path.join(self.tmp, "ildaemon"))
        with open(os.path.join(self.tmp, "ildaemon", "d.log"), "w") as fh:
            fh.write("2026-06-20 11:21:15.724736     New client: 557/2048\n"
                     "2026-06-20 11:21:16.100200     "
                     "4 clients disconnected, now having 553/2048\n")
        result = replay.replay_ildaemon(threading.Event())
        self.assertEqual(result["lines"], 2)
        with open(replay.ILDAEMON_PATH) as fh:
            written = fh.read()
        # Byte for byte: the separator is spaces, and a parser that expected a
        # tab matched none of the real file's 162,642 lines.
        self.assertIn("New client: 557/2048", written)
        self.assertNotIn("\t", written)

    def test_the_journal_is_read_in_place_not_rewritten(self):
        replay.BUNDLE_ROOT = self.tmp
        machine = os.path.join(self.tmp, "journal", "abc123")
        os.makedirs(machine)
        with open(os.path.join(machine, "system.journal"), "wb") as fh:
            fh.write(b"\0" * 4096)
        result = replay.replay_journald(threading.Event())
        self.assertEqual(result["files"], 1)
        # The path handed back is the capture itself. libsystemd reads journal
        # files, so replay here means BEING PRESENT, not being paced.
        self.assertEqual(result["path"], os.path.join(self.tmp, "journal"))


    def test_the_orchestrator_keeps_one_output_file_per_captured_day(self):
        """The file names carry the date, and the collector globs the directory.

        Merging two captured days into one file would flatten exactly the
        rotation the tail has to follow, which is the same mistake the stdout
        replay made when it merged every program into one host file.
        """
        replay.BUNDLE_ROOT = self.tmp
        os.makedirs(os.path.join(self.tmp, "odc"))
        day_one = ("2026-01-20 08:56:29.045314 fat  odc-grpc-server"
                   "     1091836 2zRVEg9xZRp:2304     "
                   "Timed out waiting for STOP transition\n")
        day_two = ("2026-01-21 00:00:01.000001 inf  odc-grpc-server"
                   "     1091836                      "
                   "Status: found 0 partition(s)\n")
        with open(os.path.join(self.tmp, "odc",
                               "odc_2026-01-20.70.log"), "w") as fh:
            fh.write(day_one)
        with open(os.path.join(self.tmp, "odc",
                               "odc_2026-01-21.71.log"), "w") as fh:
            fh.write(day_two)

        result = replay.replay_odc(threading.Event())
        self.assertEqual(result["lines"], 2)
        self.assertEqual(sorted(os.listdir(replay.ODC_DIR)),
                         ["odc_2026-01-20.70.log", "odc_2026-01-21.71.log"])

    def test_the_orchestrator_line_is_written_byte_for_byte(self):
        """The partition column is space-padded and the parser reads the
        padding. A replay that normalised the whitespace would produce a file
        the production parser does not see on the farm."""
        replay.BUNDLE_ROOT = self.tmp
        os.makedirs(os.path.join(self.tmp, "odc"))
        original = ("2026-01-20 08:56:29.045314 fat  odc-grpc-server"
                    "     1091836 2zRVEg9xZRp:2304     "
                    "Timed out waiting for STOP transition\n")
        with open(os.path.join(self.tmp, "odc", "odc_x.log"), "w") as fh:
            fh.write(original)
        replay.replay_odc(threading.Event())
        with open(os.path.join(replay.ODC_DIR, "odc_x.log")) as fh:
            self.assertEqual(fh.read(), original)

    def test_the_orchestrator_is_in_the_autostart_set(self):
        """A family the engine can replay but never starts is not replayable."""
        self.assertIn("odc", replay.AUTOSTART_FAMILIES.split(","))


if __name__ == "__main__":
    unittest.main(verbosity=2)
