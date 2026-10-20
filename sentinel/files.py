import os
import stat
import time
from collections import Counter, deque
from pathlib import Path

import psutil


def writable_owners(procs):
    owners = {}
    for p in procs.values():
        if p.write_rate <= 0:
            continue
        try:
            if psutil.Process(p.pid).create_time() != p.started:
                continue
            for fd in Path(f"/proc/{p.pid}/fd").iterdir():
                try:
                    info = Path(f"/proc/{p.pid}/fdinfo/{fd.name}").read_text()
                    mode = next(line.split()[1] for line in info.splitlines()
                                if line.startswith("flags:"))
                    if int(mode, 8) & os.O_ACCMODE == os.O_RDONLY:
                        continue
                    entry = fd.stat()
                    if stat.S_ISREG(entry.st_mode):
                        owners.setdefault((entry.st_dev, entry.st_ino), set()).add((p.pid, p.started))
                except (OSError, StopIteration):
                    continue  # descriptor closed while /proc was being read
        except (OSError, psutil.Error):
            continue
    return owners


class FileWatcher:
    def __init__(self, directories, window):
        # Imported here so pure scoring tests also work on non-Linux hosts.
        from inotify_simple import INotify, flags

        self.flags = flags
        self.window = window
        self.history = deque()
        self.inotify = INotify(nonblocking=True)
        self.paths = {}
        mask = (flags.MODIFY | flags.CREATE | flags.DELETE | flags.MOVED_FROM
                | flags.MOVED_TO | flags.DELETE_SELF | flags.MOVE_SELF | flags.ONLYDIR)
        try:
            for directory in directories:
                path = Path(directory).resolve(strict=True)
                wd = self.inotify.add_watch(path, mask)
                self.paths[wd] = path
        except BaseException:
            self.inotify.close()
            raise

    def sample(self, procs, now=None):
        now = time.monotonic() if now is None else now
        events = list(self.inotify.read(timeout=0))
        warnings = []
        owners = writable_owners(procs) if events else {}
        observed = assigned = 0
        for event in events:
            if event.mask & self.flags.Q_OVERFLOW:
                self.history.clear()
                warnings.append("inotify queue overflow; some events were lost")
                continue
            directory = self.paths.get(event.wd)
            if directory is None:
                continue
            if event.mask & (self.flags.IGNORED | self.flags.DELETE_SELF | self.flags.MOVE_SELF):
                warnings.append(f"watch removed: {directory}; restart to watch its replacement")
                self.paths.pop(event.wd, None)
                continue
            if event.mask & self.flags.ISDIR:
                continue  # watches cover immediate files, not subdirectories
            observed += 1
            owner = None
            if event.mask & self.flags.MODIFY:
                try:
                    entry = (directory / event.name).stat()
                    candidates = owners.get((entry.st_dev, entry.st_ino), set())
                    if len(candidates) == 1:
                        owner = next(iter(candidates))
                        assigned += 1
                except OSError:
                    pass
            if owner is not None:
                self.history.append((now, owner))

        while self.history and now - self.history[0][0] >= self.window:
            self.history.popleft()
        counts = Counter(owner for _, owner in self.history)
        changes = {pid: count for (pid, started), count in counts.items()
                   if pid in procs and procs[pid].started == started}
        return changes, observed, observed - assigned, warnings

    def close(self):
        self.inotify.close()
