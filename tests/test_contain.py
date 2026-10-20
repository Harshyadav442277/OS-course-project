import os
import select
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from sentinel.config import DEFAULTS
from sentinel.contain import Controller
from sentinel.monitor import Monitor

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux signals and pidfds")


def wait_status(pid, stopped):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = psutil.Process(pid).status()
        if (status == psutil.STATUS_STOPPED) == stopped:
            return
        time.sleep(0.02)
    pytest.fail(f"PID {pid} did not reach the expected stopped={stopped} state")


@pytest.fixture
def target():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    controller = Controller([child.pid], DEFAULTS["protected_names"])
    try:
        yield child, controller
    finally:
        controller.close()
        if child.poll() is None:
            child.send_signal(signal.SIGCONT)
            child.kill()
        child.wait(timeout=3)


def test_freeze_resume_and_shutdown(target):
    child, controller = target
    assert controller.freeze(child.pid)
    wait_status(child.pid, stopped=True)
    assert not controller.freeze(child.pid)
    controller.resume(child.pid)
    wait_status(child.pid, stopped=False)
    assert (child.pid, controller.targets[child.pid]) in controller.allowed
    assert controller.freeze(child.pid)
    resumed, errors = controller.close()
    assert (resumed, errors) == ([child.pid], [])
    wait_status(child.pid, stopped=False)


@pytest.mark.parametrize("frozen", [False, True])
def test_kill_selected_process(target, frozen):
    child, controller = target
    if frozen:
        controller.freeze(child.pid)
        wait_status(child.pid, stopped=True)
    controller.kill(child.pid)
    assert child.wait(timeout=3) == -signal.SIGKILL
    assert not controller.frozen


def test_cannot_control_unselected_pid(target):
    _, controller = target
    with pytest.raises(ValueError, match="outside the selected targets"):
        controller.freeze(os.getpid())


def test_self_is_protected():
    controller = Controller([os.getpid()], [])
    try:
        with pytest.raises(ValueError, match="protected process"):
            controller.freeze(os.getpid())
    finally:
        controller.close()


def test_parent_is_protected():
    controller = Controller([os.getppid()], [])
    try:
        with pytest.raises(ValueError, match="protected process"):
            controller.freeze(os.getppid())
    finally:
        controller.close()


def test_other_user_is_rejected(target, monkeypatch):
    child, controller = target
    monkeypatch.setattr(psutil.Process, "uids",
                        lambda self: SimpleNamespace(real=os.getuid() + 1, effective=os.geteuid() + 1))
    with pytest.raises(ValueError, match="another user"):
        controller.freeze(child.pid)


def test_interrupt_after_stop_keeps_handle_for_shutdown(target, monkeypatch):
    child, controller = target
    send_signal = signal.pidfd_send_signal

    def interrupted_send(fd, sig):
        send_signal(fd, sig)
        if sig == signal.SIGSTOP:
            raise KeyboardInterrupt

    monkeypatch.setattr(signal, "pidfd_send_signal", interrupted_send)
    with pytest.raises(KeyboardInterrupt):
        controller.freeze(child.pid)
    assert child.pid in controller.frozen
    controller.close()
    wait_status(child.pid, stopped=False)


def test_configured_name_is_protected(target):
    child, controller = target
    controller.protected_names.add(psutil.Process(child.pid).name().casefold())
    with pytest.raises(ValueError, match="protected process"):
        controller.freeze(child.pid)


def test_reused_pid_is_rejected(target):
    child, controller = target
    controller.targets[child.pid] -= 10
    with pytest.raises(ValueError, match="PID was reused"):
        controller.freeze(child.pid)
    wait_status(child.pid, stopped=False)


def test_existing_external_stop_is_not_owned(target):
    child, controller = target
    child.send_signal(signal.SIGSTOP)
    wait_status(child.pid, stopped=True)
    with pytest.raises(ValueError, match="already stopped outside Sentinel"):
        controller.freeze(child.pid)
    assert controller.close() == ([], [])
    assert psutil.Process(child.pid).status() == psutil.STATUS_STOPPED


def test_resume_requires_our_stop(target):
    child, controller = target
    with pytest.raises(ValueError, match="not frozen by this Sentinel session"):
        controller.resume(child.pid)


def test_refresh_discovers_real_descendant():
    parent = subprocess.Popen([sys.executable, "-c",
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print(p.pid,flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    controller = Controller([parent.pid], [])
    descendant = None
    try:
        assert select.select([parent.stdout], [], [], 3)[0]
        descendant = psutil.Process(int(parent.stdout.readline()))
        controller.refresh(Monitor().sample())
        assert descendant.pid in controller.targets
        controller.freeze(descendant.pid)
        wait_status(descendant.pid, stopped=True)
    finally:
        controller.close()
        if descendant is not None:
            try:
                descendant.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
        parent.wait(timeout=3)
        parent.stdout.close()
