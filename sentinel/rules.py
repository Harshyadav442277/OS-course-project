import time
from dataclasses import dataclass, field

NORMAL = "NORMAL"
WARNING = "WARNING"
CONTAIN = "CONTAIN"


@dataclass
class Verdict:
    score: int = 0
    level: str = NORMAL
    reasons: list[str] = field(default_factory=list)


class RiskEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        # the cpu rule needs history: (pid, start time) -> when it first went over
        self._hot_since = {}

    def assess(self, procs, file_changes=None, now=None):
        # Optional {pid: changes in the configured window}; no live watcher yet.
        # now can be passed in so the tests don't have to sleep 10 s
        now = time.monotonic() if now is None else now
        file_changes = file_changes or {}
        rules = self.cfg["rules"]
        hot = {}
        verdicts = {}

        for pid, p in procs.items():
            v = Verdict()

            r = rules["cpu"]
            if p.cpu > r["percent"]:
                key = (pid, p.started)
                since = self._hot_since.get(key, now)
                hot[key] = since
                if now - since >= r["for_seconds"]:
                    v.score += r["score"]
                    v.reasons.append(f"CPU {p.cpu:.0f}% for {now - since:.0f}s")

            r = rules["children"]
            if p.descendants > r["count"]:
                v.score += r["score"]
                v.reasons.append(f"{p.descendants} descendant processes")

            r = rules["file_changes"]
            changes = file_changes.get(pid, 0)
            if changes > r["count"]:
                v.score += r["score"]
                v.reasons.append(f"{changes} file changes in {r['window']}s")

            r = rules["connections"]
            if p.conns > r["count"]:
                v.score += r["score"]
                v.reasons.append(f"{p.conns} network connections")

            r = rules["open_files"]
            if p.fds is not None and p.fds > r["count"]:
                v.score += r["score"]
                v.reasons.append(f"{p.fds} file descriptors")

            if v.score >= self.cfg["contain_at"]:
                v.level = CONTAIN
            elif v.score >= self.cfg["warn_at"]:
                v.level = WARNING
            verdicts[pid] = v

        # anything that cooled down or exited starts from scratch next time
        self._hot_since = hot
        return verdicts
