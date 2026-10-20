import os
import select
import signal

import psutil


class Controller:
    def __init__(self, pids, protected_names):
        if pids and not (hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")):
            raise ValueError("signal controls need Linux pidfd support (kernel 5.3+)")
        self.roots = {pid: psutil.Process(pid).create_time() for pid in pids}
        self.targets = dict(self.roots)
        self.protected = {os.getpid(), 1}
        self.protected.update(p.pid for p in psutil.Process().parents())
        self.protected_names = {name.casefold() for name in protected_names}
        self.frozen = {}  # pid -> (start time, pidfd); only processes stopped by us
        self.allowed = set()

    def refresh(self, procs):
        for pid, (_, fd) in list(self.frozen.items()):
            if select.select([fd], [], [], 0)[0]:
                os.close(fd)
                del self.frozen[pid]

        children = {}
        for p in procs.values():
            children.setdefault(p.ppid, []).append(p.pid)
        known = {**self.roots, **self.targets}
        stack = [pid for pid, started in known.items()
                 if pid in procs and procs[pid].started == started]
        targets = {}
        while stack:
            pid = stack.pop()
            if pid in targets:
                continue
            targets[pid] = procs[pid].started
            stack.extend(children.get(pid, ()))
        self.targets = targets
        self.allowed.intersection_update((pid, started) for pid, started in targets.items())

    def _open_target(self, pid):
        if pid not in self.targets:
            raise ValueError("PID is outside the selected targets")
        # A pidfd pins the process even if it exits and Linux reuses its PID.
        fd = os.pidfd_open(pid)
        try:
            p = psutil.Process(pid)
            if p.create_time() != self.targets[pid]:
                raise ValueError("PID was reused; refusing to send a signal")
            if pid in self.protected or p.name().casefold() in self.protected_names:
                raise ValueError("protected process")
            if p.uids().real != os.getuid() or p.uids().effective != os.geteuid():
                raise ValueError("process belongs to another user")
            return fd, p
        except BaseException:
            os.close(fd)
            raise

    def freeze(self, pid):
        if pid in self.frozen:
            return False
        fd, p = self._open_target(pid)
        try:
            if p.status() in (psutil.STATUS_STOPPED, psutil.STATUS_TRACING_STOP):
                raise ValueError("process was already stopped outside Sentinel")
        except BaseException:
            os.close(fd)
            raise
        # Register the handle before STOP so an interrupt cannot strand the process.
        self.frozen[pid] = (self.targets[pid], fd)
        try:
            signal.pidfd_send_signal(fd, signal.SIGSTOP)
        except OSError:
            os.close(fd)
            del self.frozen[pid]
            raise
        self.allowed.discard((pid, self.targets[pid]))
        return True

    def resume(self, pid):
        if pid not in self.frozen:
            raise ValueError("PID was not frozen by this Sentinel session")
        started, fd = self.frozen[pid]
        try:
            signal.pidfd_send_signal(fd, signal.SIGCONT)
        except ProcessLookupError:
            pass
        else:
            # An explicit resume approves this process for the rest of the session.
            self.allowed.add((pid, started))
        os.close(fd)
        del self.frozen[pid]

    def kill(self, pid):
        if pid in self.frozen:
            _, fd = self.frozen[pid]
        else:
            fd, _ = self._open_target(pid)
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        finally:
            # On failure retain our frozen handle so shutdown can still resume it.
            if pid not in self.frozen:
                os.close(fd)
        if pid in self.frozen:
            os.close(fd)
            del self.frozen[pid]

    def close(self):
        resumed, errors = [], []
        for pid in list(self.frozen):
            try:
                self.resume(pid)
                resumed.append(pid)
            except OSError as exc:
                errors.append(f"PID {pid}: {exc}")
                os.close(self.frozen.pop(pid)[1])
        return resumed, errors
