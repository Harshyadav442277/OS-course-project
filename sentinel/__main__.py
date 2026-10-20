import argparse
import sys
import time

from . import config
from .monitor import Monitor
from .rules import NORMAL, RiskEngine


def human(n):
    for unit in "BKMG":
        if abs(n) < 1024:
            return f"{n:.0f}{unit}" if unit in "BK" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def print_table(procs, verdicts, top):
    rows = sorted(procs.values(), key=lambda p: (verdicts[p.pid].score, p.cpu), reverse=True)
    rows = rows[:top]
    # KIDS is the whole subtree, which is what matters for something that keeps forking
    print(f"{'PID':>7} {'PPID':>7}  {'NAME':<16} {'CPU%':>6} {'MEM':>7} "
          f"{'KIDS':>5} {'FDS':>5} {'CONN':>5} {'WRITE/s':>8} {'RISK':>5}  LEVEL")
    for p in rows:
        v = verdicts[p.pid]
        fds = "-" if p.fds is None else p.fds
        print(f"{p.pid:>7} {p.ppid:>7}  {p.name[:16]:<16} {p.cpu:>6.1f} {human(p.rss):>7} "
              f"{p.descendants:>5} {fds:>5} {p.conns:>5} {human(p.write_rate):>8} "
              f"{v.score:>5}  {v.level}")

    bad = [p for p in rows if verdicts[p.pid].level != NORMAL]
    for p in bad:
        v = verdicts[p.pid]
        print(f"\n{v.level}: {p.name} (pid {p.pid}), risk {v.score}")
        for reason in v.reasons:
            print(f"  + {reason}")


def main():
    ap = argparse.ArgumentParser(prog="sentinel", description="Watch running processes.")
    ap.add_argument("-c", "--config", help="yaml file with thresholds (default: ./config.yaml)")
    ap.add_argument("-n", "--interval", type=float,
                    help="seconds between samples (default: from config, 2)")
    ap.add_argument("--top", type=int, default=15, help="rows to show (default: 15)")
    ap.add_argument("--once", action="store_true", help="print one table and exit")
    args = ap.parse_args()

    if not sys.platform.startswith("linux"):
        sys.exit("sentinel needs Linux (/proc, signals). On Windows run it inside WSL.")

    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as e:
        sys.exit(f"bad config: {e}")
    interval = args.interval or cfg["interval"]

    mon = Monitor()
    engine = RiskEngine(cfg)
    mon.sample()  # first cpu reading is always 0, so throw it away
    try:
        while True:
            time.sleep(interval)
            procs = mon.sample()
            verdicts = engine.assess(procs)
            if not args.once:
                print("\033[2J\033[H", end="")  # clear the terminal
            print(f"sentinel  {time.strftime('%H:%M:%S')}  {len(procs)} processes\n")
            print_table(procs, verdicts, args.top)
            if args.once:
                break
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
