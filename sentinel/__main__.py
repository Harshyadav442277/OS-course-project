import argparse
import math
import sys
import time

import yaml

from . import config
from .monitor import Monitor
from .rules import NORMAL, RiskEngine


def format_bytes(n):
    for unit in "BKMG":
        if abs(n) < 1024:
            return f"{n:.0f}{unit}" if unit in "BK" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def print_table(procs, verdicts, top):
    rows = sorted(procs.values(), key=lambda p: (verdicts[p.pid].score, p.cpu), reverse=True)
    rows = rows[:top]
    print(f"{'PID':>7} {'PPID':>7}  {'NAME':<16} {'CPU%':>6} {'MEM':>7} "
          f"{'KIDS':>5} {'FDS':>5} {'CONN':>5} {'WRITE/s':>8} {'RISK':>5}  LEVEL")
    for p in rows:
        v = verdicts[p.pid]
        fds = "-" if p.fds is None else p.fds
        print(f"{p.pid:>7} {p.ppid:>7}  {p.name[:16]:<16} {p.cpu:>6.1f} {format_bytes(p.rss):>7} "
              f"{p.descendants:>5} {fds:>5} {p.conns:>5} {format_bytes(p.write_rate):>8} "
              f"{v.score:>5}  {v.level}")

    for p in rows:
        v = verdicts[p.pid]
        if v.level == NORMAL:
            continue
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
    if args.interval is not None and (not math.isfinite(args.interval) or args.interval <= 0):
        ap.error("--interval must be a finite number greater than zero")
    if args.top <= 0:
        ap.error("--top must be greater than zero")

    if not sys.platform.startswith("linux"):
        sys.exit("sentinel needs Linux (/proc). On Windows run it inside WSL.")

    try:
        cfg = config.load(args.config)
    except (OSError, ValueError, yaml.YAMLError) as e:
        sys.exit(f"bad config: {e}")
    interval = cfg["interval"] if args.interval is None else args.interval

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
