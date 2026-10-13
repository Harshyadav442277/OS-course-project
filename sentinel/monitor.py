"""Per-process stats, read from /proc through psutil."""

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
        # (pid, create_time) -> (when, read_bytes, write_bytes)
        # create_time is in the key so a reused pid doesn't inherit old numbers
        self._last_io = {}

    def sample(self):
        """One pass over every process. Returns {pid: ProcStats}.

        psutil keeps the Process objects between calls to process_iter(), which is
        what makes cpu_percent meaningful: it's measured since the last call. So the
        very first sample reports 0% for everything.
        """
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
                key = (info["pid"], info["create_time"])
                prev = self._last_io.get(key)
                if prev and now > prev[0]:
                    dt = now - prev[0]
                    read_rate = (io.read_bytes - prev[1]) / dt
                    write_rate = (io.write_bytes - prev[2]) / dt
                io_seen[key] = (now, io.read_bytes, io.write_bytes)

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
        count_connections(procs)
        return procs


def count_children(procs):
    """Fill in children and descendants from the ppid links."""
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


def count_connections(procs):
    # one system-wide call is far cheaper than asking every process separately.
    # Without root we only get pids for our own sockets, the rest come back as None.
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return
    for c in conns:
        if c.pid in procs:
            procs[c.pid].conns += 1
