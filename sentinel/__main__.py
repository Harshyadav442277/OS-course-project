import argparse
import sys
import time

from .monitor import Monitor


def human(n):
    for unit in "BKMG":
        if abs(n) < 1024:
            return f"{n:.0f}{unit}" if unit in "BK" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def print_table(procs, top):
    rows = sorted(procs.values(), key=lambda p: p.cpu, reverse=True)[:top]
    # KIDS is the whole subtree, which is what matters for something that keeps forking
    print(f"{'PID':>7} {'PPID':>7}  {'NAME':<16} {'CPU%':>6} {'MEM':>7} "
          f"{'KIDS':>5} {'FDS':>5} {'CONN':>5} {'WRITE/s':>8}  STATUS")
    for p in rows:
        fds = "-" if p.fds is None else p.fds
        print(f"{p.pid:>7} {p.ppid:>7}  {p.name[:16]:<16} {p.cpu:>6.1f} {human(p.rss):>7} "
              f"{p.descendants:>5} {fds:>5} {p.conns:>5} {human(p.write_rate):>8}  {p.status}")


def main():
    ap = argparse.ArgumentParser(prog="sentinel", description="Watch running processes.")
    ap.add_argument("-n", "--interval", type=float, default=2.0,
                    help="seconds between samples (default: 2)")
    ap.add_argument("--top", type=int, default=15, help="rows to show (default: 15)")
    ap.add_argument("--once", action="store_true", help="print one table and exit")
    args = ap.parse_args()

    if not sys.platform.startswith("linux"):
        sys.exit("sentinel needs Linux (/proc, signals). On Windows run it inside WSL.")

    mon = Monitor()
    mon.sample()  # first cpu reading is always 0, so throw it away
    try:
        while True:
            time.sleep(args.interval)
            procs = mon.sample()
            if not args.once:
                print("\033[2J\033[H", end="")  # clear the terminal
            print(f"sentinel  {time.strftime('%H:%M:%S')}  {len(procs)} processes\n")
            print_table(procs, args.top)
            if args.once:
                break
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
