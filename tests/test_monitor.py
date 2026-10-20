import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from sentinel.monitor import ATTRS, Monitor, ProcStats, count_children

needs_linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")


def fake(pid, ppid):
    return ProcStats(pid=pid, ppid=ppid, name="x", user=None, status="sleeping", cpu=0.0,
                     rss=0, threads=1, fds=None, read_rate=0.0, write_rate=0.0, started=0.0)


def test_tree_counts():
    #   1
    #   +-- 2 -- 3 -- 5
    #   +-- 4
    procs = {p.pid: p for p in [fake(1, 0), fake(2, 1), fake(3, 2), fake(4, 1), fake(5, 3)]}
    count_children(procs)
    assert (procs[1].children, procs[1].descendants) == (2, 4)
    assert (procs[2].children, procs[2].descendants) == (1, 2)
    assert (procs[4].children, procs[4].descendants) == (0, 0)


@needs_linux
def test_sees_own_process():
    me = Monitor().sample()[os.getpid()]
    assert me.ppid == os.getppid()
    assert me.rss > 0
    assert me.fds is not None  # our own /proc/self/fd is always readable


@needs_linux
def test_counts_real_children():
    # sh with two sleeps in the background: 1 direct child (sh), 3 descendants
    sh = subprocess.Popen(["sh", "-c", "sleep 30 & sleep 30 & wait"])
    try:
        time.sleep(0.3)
        me = Monitor().sample()[os.getpid()]
        assert me.children >= 1
        assert me.descendants >= 3
    finally:
        subprocess.run(["pkill", "-P", str(sh.pid)])
        sh.kill()
        sh.wait()


@needs_linux
def test_busy_process_shows_cpu():
    busy = subprocess.Popen([sys.executable, "-c", "while True: pass"])
    try:
        mon = Monitor()
        mon.sample()
        time.sleep(1)
        assert mon.sample()[busy.pid].cpu > 50
    finally:
        busy.kill()
        busy.wait()


@needs_linux
def test_write_rate(tmp_path):
    # This should also detect writes on tmpfs, where disk I/O counters stay at zero.
    code = (
        "import os, sys, time\n"
        "f = open(sys.argv[1], 'wb')\n"
        "while True:\n"
        "    f.write(os.urandom(1 << 20)); f.flush(); time.sleep(0.05)\n"
    )
    writer = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "junk")])
    try:
        mon = Monitor()
        time.sleep(0.3)
        mon.sample()
        time.sleep(1)
        assert mon.sample()[writer.pid].write_rate > 1 << 20
    finally:
        writer.kill()
        writer.wait()


def test_io_rates_reset_on_pid_reuse_and_exit(monkeypatch):
    info = dict.fromkeys(ATTRS)
    info.update(pid=100, ppid=1, create_time=10,
                io_counters=SimpleNamespace(read_chars=100, write_chars=200))
    processes = [SimpleNamespace(info=info)]
    monkeypatch.setattr("sentinel.monitor.psutil.process_iter", lambda *a, **kw: processes)
    monkeypatch.setattr("sentinel.monitor.psutil.net_connections", lambda **kw: [])
    times = iter([0, 2, 4, 6, 8])
    monkeypatch.setattr("sentinel.monitor.time.monotonic", lambda: next(times))
    mon = Monitor()
    assert mon.sample()[100].write_rate == 0
    info["io_counters"] = SimpleNamespace(read_chars=120, write_chars=240)
    sample = mon.sample()[100]
    assert (sample.read_rate, sample.write_rate) == (10, 20)
    info["create_time"] = 20
    sample = mon.sample()[100]
    assert (sample.read_rate, sample.write_rate) == (0, 0)
    processes.clear()
    assert mon.sample() == {}
    processes.append(SimpleNamespace(info=info))
    assert mon.sample()[100].write_rate == 0
