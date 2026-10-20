import select
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from sentinel.files import FileWatcher
from sentinel.monitor import Monitor

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux inotify and /proc")


@contextmanager
def writer(path):
    code = (
        "import sys,time\n"
        "f=open(sys.argv[1], 'ab', buffering=0)\n"
        "print('ready', flush=True)\n"
        "while True:\n"
        "    f.write(b'test\\n'); time.sleep(0.02)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(path)],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 3)[0], "writer did not start"
        assert child.stdout.readline().strip() == "ready"
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
        child.stdout.close()


def test_real_file_events_attribution_window_and_pid_reuse(tmp_path):
    watcher = FileWatcher([tmp_path], window=5)
    try:
        with writer(tmp_path / "data") as child:
            mon = Monitor()
            mon.sample()
            time.sleep(0.15)
            procs = mon.sample()
            changes, observed, _, warnings = watcher.sample(procs, now=0)
            assert changes[child.pid] > 0
            assert observed > 0 and warnings == []
            procs[child.pid].started += 1
            assert child.pid not in watcher.sample(procs, now=1)[0]
        changes, _, _, _ = watcher.sample({}, now=10)
        assert changes == {}
        assert not watcher.history
    finally:
        watcher.close()


def test_shared_writable_file_is_unassigned(tmp_path):
    watcher = FileWatcher([tmp_path], window=5)
    try:
        with writer(tmp_path / "shared") as first, writer(tmp_path / "shared") as second:
            mon = Monitor()
            mon.sample()
            time.sleep(0.15)
            procs = mon.sample()
            assert procs[first.pid].write_rate > 0 and procs[second.pid].write_rate > 0
            changes, observed, unassigned, _ = watcher.sample(procs)
            assert changes == {}
            assert observed == unassigned and observed > 0
    finally:
        watcher.close()


def test_invalid_watch_directory(tmp_path):
    with pytest.raises(OSError):
        FileWatcher([tmp_path / "missing"], window=5)
    file = tmp_path / "file"
    file.touch()
    with pytest.raises(OSError):
        FileWatcher([file], window=5)


def test_directory_events_are_not_scored(tmp_path):
    watcher = FileWatcher([tmp_path], window=5)
    try:
        subdir = tmp_path / "nested"
        subdir.mkdir()
        (subdir / "unwatched").write_text("data")
        assert watcher.sample({})[:3] == ({}, 0, 0)
    finally:
        watcher.close()


def test_overflow_is_reported_and_history_cleared(tmp_path, monkeypatch):
    from inotify_simple import Event, flags

    watcher = FileWatcher([tmp_path], window=5)
    try:
        watcher.history.append((0, (123, 1)))
        monkeypatch.setattr(watcher.inotify, "read", lambda **kw: [Event(-1, flags.Q_OVERFLOW, 0, "")])
        changes, _, _, warnings = watcher.sample({}, now=1)
        assert changes == {} and not watcher.history
        assert "overflow" in warnings[0]
    finally:
        watcher.close()


def test_readonly_descriptor_cannot_be_a_writer(tmp_path):
    path = tmp_path / "data"
    path.write_text("initial")
    watcher = FileWatcher([tmp_path], window=5)
    reader = subprocess.Popen([sys.executable, "-c",
        "import sys,time; f=open(sys.argv[1]); print('ready',flush=True); time.sleep(30)",
        str(path)], stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([reader.stdout], [], [], 3)[0]
        assert reader.stdout.readline().strip() == "ready"
        p = Monitor().sample()[reader.pid]
        p.write_rate = 10  # Even unrelated pipe writes must not make a read-only FD writable.
        path.write_text("changed by another process")
        changes, observed, unassigned, _ = watcher.sample({reader.pid: p})
        assert changes == {} and observed == unassigned and observed > 0
    finally:
        reader.kill()
        reader.wait(timeout=3)
        reader.stdout.close()
        watcher.close()


def test_moved_watch_is_reported(tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    watcher = FileWatcher([watched], window=5)
    try:
        watched.rename(tmp_path / "moved")
        changes, _, _, warnings = watcher.sample({})
        assert changes == {}
        assert "watch removed" in warnings[0]
        assert not watcher.paths
    finally:
        watcher.close()
