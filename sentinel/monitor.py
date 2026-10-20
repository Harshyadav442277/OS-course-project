import time
from dataclasses import dataclass

import psutil


@dataclass
class ProcStats:
    pid: int
    ppid: int
    name: str
    user: str | None
    status: str
    cpu: float            # % of one core, so it can go past 100 on multi-core
    rss: int              # resident memory, bytes
    threads: int
    fds: int | None       # None if we can't read /proc/<pid>/fd (someone else's process)
    read_rate: float      # bytes/s since the previous sample
    write_rate: float
    started: float        # epoch seconds
    children: int = 0     # direct children only
    descendants: int = 0  # the whole subtree
    conns: int = 0

    @property
    def age(self):
        return time.time() - self.started


ATTRS = ["pid", "ppid", "name", "username", "status", "cpu_percent",
         "memory_info", "num_threads", "num_fds", "io_counters", "create_time"]


class Monitor:
    def __init__(self):
        # (pid, create_time) -> (when, rchar, wchar)
        # create_time is in the key so a reused pid doesn't inherit old numbers
        self._last_io = {}

    def sample(self):
        # psutil keeps its Process objects between process_iter() calls, and
        # cpu_percent is measured since the previous call. That's why the very
        # first sample shows 0% for everything.
        now = time.monotonic()
        procs = {}
        io_seen = {}

        for p in psutil.process_iter(ATTRS, ad_value=None):
            info = p.info
            if info["create_time"] is None:
                continue  # exited while we were reading it

            read_rate = write_rate = 0.0
            io = info["io_counters"]
            if io is not None:
                # rchar/wchar count every read()/write() call. read_bytes/write_bytes
                # only count what goes to a block device, so anything written to
                # tmpfs (/tmp on newer Ubuntu) would show up as 0.
                rd, wr = io.read_chars, io.write_chars
                key = (info["pid"], info["create_time"])
                prev = self._last_io.get(key)
                if prev and now > prev[0]:
                    dt = now - prev[0]
                    read_rate = (rd - prev[1]) / dt
                    write_rate = (wr - prev[2]) / dt
                io_seen[key] = (now, rd, wr)

            mem = info["memory_info"]
            procs[info["pid"]] = ProcStats(
                pid=info["pid"],
                ppid=info["ppid"] or 0,
                name=info["name"] or "?",
                user=info["username"],
                status=info["status"] or "?",
                cpu=info["cpu_percent"] or 0.0,
                rss=mem.rss if mem else 0,
                threads=info["num_threads"] or 0,
                fds=info["num_fds"],
                read_rate=read_rate,
                write_rate=write_rate,
                started=info["create_time"],
            )

        self._last_io = io_seen  # forget processes that have exited
        count_children(procs)

        # one system-wide call is far cheaper than asking each process. Without
        # root, sockets that aren't ours come back with pid None and get skipped.
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.pid in procs:
                    procs[c.pid].conns += 1
        except psutil.AccessDenied:
            pass
        return procs


def count_children(procs):
    kids = {}
    for p in procs.values():
        kids.setdefault(p.ppid, []).append(p.pid)

    # walk down from the roots, then add up subtree sizes from the leaves back up
    order = []
    stack = [pid for pid, p in procs.items() if p.ppid not in procs]
    visited = set()
    while stack:
        pid = stack.pop()
        if pid in visited:
            continue
        visited.add(pid)
        order.append(pid)
        stack.extend(kids.get(pid, ()))

    for pid in reversed(order):
        p = procs[pid]
        mine = kids.get(pid, ())
        p.children = len(mine)
        p.descendants = sum(1 + procs[c].descendants for c in mine)
