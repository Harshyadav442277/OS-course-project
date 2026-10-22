import json
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="full Linux CLI")
ROOT = Path(__file__).resolve().parent.parent


def records(path):
    if not path.exists():
        return []
    lines = path.read_text().splitlines(keepends=True)
    return [json.loads(line) for line in lines if line.endswith("\n")]


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail("timed out waiting for CLI state")


@contextmanager
def cli(tmp_path, *args):
    log = tmp_path / "events.jsonl"
    output = tmp_path / "output.txt"
    with output.open("w") as stream:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "sentinel", "--no-clear", "--log", str(log), *args],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True,
        )
        try:
            yield process, log, output
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdin.close()


def send(process, text):
    process.stdin.write(text + "\n")
    process.stdin.flush()


def test_demo_freeze_resume_inspect_and_kill(tmp_path):
    with cli(tmp_path, "--demo") as (process, log, output):
        freeze = wait_for(lambda: next((r for r in records(log) if r["event"] == "freeze"), None))
        pid = freeze["pid"]
        assert freeze["source"] == "automatic" and freeze["score"] >= 70
        assert any("estimated file modifications" in reason for reason in freeze["reasons"])
        wait_for(lambda: psutil.Process(pid).status() == psutil.STATUS_STOPPED)
        children = psutil.Process(pid).children()
        assert len(children) == 4
        scratch = Path(records(log)[0]["watch"][0])
        send(process, f"inspect {pid}\nresume {pid}")
        wait_for(lambda: any(r["event"] == "resume" for r in records(log)))
        wait_for(lambda: psutil.Process(pid).status() != psutil.STATUS_STOPPED)
        time.sleep(1.2)
        assert len([r for r in records(log) if r["event"] == "freeze"]) == 1
        assert '"file_changes"' in output.read_text()
        send(process, f"freeze {pid}")
        wait_for(lambda: any(r["event"] == "freeze" and r["source"] == "command" for r in records(log)))
        wait_for(lambda: psutil.Process(pid).status() == psutil.STATUS_STOPPED)
        send(process, f"kill {pid}")
        wait_for(lambda: any(r["event"] == "kill" for r in records(log)))
        wait_for(lambda: not psutil.pid_exists(pid))
        send(process, "quit")
        assert process.wait(timeout=8) == 0, output.read_text()
        assert records(log)[-1]["event"] == "session_end"
        assert not scratch.exists()
        assert not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in children)
        assert "Traceback" not in output.read_text()


@pytest.mark.parametrize("shutdown", ["quit", signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_shutdown_resumes_external_target(tmp_path, shutdown):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    cfg = tmp_path / "thresholds.yaml"
    cfg.write_text("warn_at: 0\ncontain_at: 0\n")
    try:
        with cli(tmp_path, "--pid", str(child.pid), "--contain", "-c", str(cfg), "-n", "0.1") as (process, log, output):
            wait_for(lambda: any(r["event"] == "freeze" and r["pid"] == child.pid for r in records(log)))
            wait_for(lambda: psutil.Process(child.pid).status() == psutil.STATUS_STOPPED)
            if shutdown == "quit":
                send(process, "quit")
            else:
                process.send_signal(shutdown)
            assert process.wait(timeout=8) == 0, output.read_text()
            assert child.poll() is None
            wait_for(lambda: psutil.Process(child.pid).status() != psutil.STATUS_STOPPED)
            event = next(r for r in records(log) if r["event"] == "shutdown_resume")
            assert event["pids"] == [child.pid] and not event["errors"]
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGCONT)
            child.kill()
        child.wait(timeout=3)


def test_monitor_once_and_startup_errors(tmp_path):
    with cli(tmp_path, "--once", "-n", "0.05") as (process, log, output):
        assert process.wait(timeout=8) == 0, output.read_text()
        assert "auto-freeze=off" in output.read_text()
        assert records(log)[0]["auto_contain"] is False
        assert not any(r["event"] == "freeze" for r in records(log))
    result = subprocess.run(
        [sys.executable, "-m", "sentinel", "--watch", str(tmp_path / "missing"),
         "--log", str(tmp_path / "error.jsonl")], cwd=ROOT, capture_output=True, text=True, timeout=8,
    )
    assert result.returncode == 1
    assert "sentinel:" in result.stderr and "Traceback" not in result.stderr


def test_demo_survives_long_pause_and_children_follow_parent_exit(tmp_path):
    demo = subprocess.Popen([sys.executable, "-m", "sentinel.demo", "--directory", str(tmp_path),
                             "--duration", "2"], cwd=ROOT)
    children = []
    try:
        children = wait_for(lambda: (kids if len(kids := psutil.Process(demo.pid).children()) == 4
                                    else None))
        demo.send_signal(signal.SIGSTOP)
        wait_for(lambda: psutil.Process(demo.pid).status() == psutil.STATUS_STOPPED)
        time.sleep(2.2)  # Longer than the whole configured workload, while stopped.
        assert all(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in children)
        demo.send_signal(signal.SIGCONT)
        time.sleep(0.2)
        assert demo.poll() is None, "resuming must not immediately end the demo"
        demo.kill()
        demo.wait(timeout=3)
        wait_for(lambda: all(not p.is_running() or p.status() == psutil.STATUS_ZOMBIE for p in children))
    finally:
        if demo.poll() is None:
            demo.kill()
        demo.wait(timeout=3)
        for child in children:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                child.kill()
