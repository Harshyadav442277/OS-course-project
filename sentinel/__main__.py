import argparse
import json
import math
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import psutil
import yaml

from . import config
from .contain import Controller
from .events import EventLog
from .files import FileWatcher
from .monitor import Monitor
from .rules import CONTAIN, NORMAL, RiskEngine


def format_bytes(n):
    for unit in "BKMG":
        if abs(n) < 1024:
            return f"{n:.0f}{unit}" if unit in "BK" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def print_table(procs, verdicts, top, frozen=None):
    frozen = frozen or {}
    rows = sorted(procs.values(),
                  key=lambda p: (p.pid in frozen, verdicts[p.pid].score, p.cpu), reverse=True)
    rows = rows[:top]
    print(f"{'PID':>7} {'PPID':>7}  {'NAME':<16} {'CPU%':>6} {'MEM':>7} "
          f"{'KIDS':>5} {'FDS':>5} {'CONN':>5} {'WRITE/s':>8} {'CHG~':>5} {'RISK':>5}  LEVEL     STATE")
    for p in rows:
        v = verdicts[p.pid]
        fds = "-" if p.fds is None else p.fds
        print(f"{p.pid:>7} {p.ppid:>7}  {p.name[:16]:<16} {p.cpu:>6.1f} {format_bytes(p.rss):>7} "
              f"{p.descendants:>5} {fds:>5} {p.conns:>5} {format_bytes(p.write_rate):>8} "
              f"{p.file_changes:>5} {v.score:>5}  {v.level:<9} "
              f"{'FROZEN' if p.pid in frozen else p.status}")

    for p in rows:
        v = verdicts[p.pid]
        if v.level == NORMAL:
            continue
        print(f"\n{v.level}: {p.name} (pid {p.pid}), risk {v.score}")
        for reason in v.reasons:
            print(f"  + {reason}")


def command(line, procs, verdicts, controller, log):
    parts = line.split()
    if not parts:
        return True, ""
    if parts == ["quit"]:
        return False, ""
    if len(parts) != 2 or parts[0] not in ("freeze", "resume", "kill", "inspect"):
        return True, "Commands: freeze PID | resume PID | kill PID | inspect PID | quit"
    action = parts[0]
    try:
        pid = int(parts[1])
        if action == "inspect":
            if pid not in procs:
                raise ValueError("PID is not in the latest sample")
            return True, json.dumps({**asdict(procs[pid]), **asdict(verdicts[pid]),
                                     "frozen_by_sentinel": pid in controller.frozen}, indent=2)
        started = (controller.frozen[pid][0] if pid in controller.frozen
                   else controller.targets.get(pid))
        if action == "freeze":
            changed = controller.freeze(pid)
            if not changed:
                return True, f"PID {pid} is already frozen"
        elif action == "resume":
            controller.resume(pid)
        else:
            controller.kill(pid)
        log.record(action, pid=pid, started=started, source="command")
        return True, f"{action}: PID {pid}"
    except (ValueError, OSError, psutil.Error) as exc:
        log.record("command_error", command=line.strip(), error=str(exc))
        return True, str(exc)


def remember_demo_children(demo, children):
    if demo is not None and demo.poll() is None:
        try:
            children.update(psutil.Process(demo.pid).children(recursive=True))
        except psutil.Error:
            pass


def cleanup_demo(demo, children):
    if demo is None:
        return
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    if demo.poll() is None:
        demo.terminate()
    try:
        demo.wait(timeout=3)
    except subprocess.TimeoutExpired:
        demo.kill()
        demo.wait()
    _, alive = psutil.wait_procs(list(children), timeout=2)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            pass


def main():
    ap = argparse.ArgumentParser(prog="sentinel", description="Watch running processes.")
    ap.add_argument("-c", "--config", help="yaml file with thresholds (default: ./config.yaml)")
    ap.add_argument("-n", "--interval", type=float,
                    help="seconds between samples (default: from config, 2)")
    ap.add_argument("--top", type=int, default=15, help="rows to show (default: 15)")
    ap.add_argument("--once", action="store_true", help="print one table and exit")
    ap.add_argument("--watch", action="append", default=[], metavar="DIR",
                    help="watch immediate files in DIR (repeat for multiple directories)")
    ap.add_argument("--pid", type=int, action="append", default=[],
                    help="allow controls for this PID and its descendants (repeatable)")
    ap.add_argument("--contain", action="store_true", help="automatically freeze selected high-risk PIDs")
    ap.add_argument("--demo", action="store_true", help="run and contain a temporary demo workload")
    ap.add_argument("--log", default="sentinel.jsonl", help="event log path (default: sentinel.jsonl)")
    ap.add_argument("--no-clear", action="store_true", help="keep previous tables on screen")
    args = ap.parse_args()
    if args.interval is not None and (not math.isfinite(args.interval) or args.interval <= 0):
        ap.error("--interval must be a finite number greater than zero")
    if args.top <= 0:
        ap.error("--top must be greater than zero")
    if any(pid <= 1 for pid in args.pid):
        ap.error("--pid must be greater than 1")
    if args.contain and not (args.pid or args.demo):
        ap.error("--contain requires --pid targets or --demo")

    if not sys.platform.startswith("linux"):
        sys.exit("sentinel needs Linux (/proc). On Windows run it inside WSL.")

    try:
        path = args.config
        if args.demo and path is None:
            path = Path(__file__).resolve().parent.parent / "demo.yaml"
        cfg = config.load(path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        sys.exit(f"bad config: {e}")
    interval = cfg["interval"] if args.interval is None else args.interval

    log = watcher = controller = demo = scratch = None
    demo_children = set()
    handlers = {}

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    try:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            handlers[sig] = signal.signal(sig, interrupted)
        log = EventLog(args.log)
        directories = list(args.watch)
        pids = list(args.pid)
        if args.demo:
            scratch = tempfile.TemporaryDirectory(prefix="sentinel-demo-")
            directories.append(scratch.name)
        watcher = FileWatcher(directories, cfg["rules"]["file_changes"]["window"])
        if args.demo:
            demo = subprocess.Popen([sys.executable, "-m", "sentinel.demo",
                                     "--directory", scratch.name])
            pids.append(demo.pid)
        controller = Controller(pids, cfg["protected_names"])
        auto = args.contain or args.demo
        log.record("session_start", auto_contain=auto, targets=controller.roots,
                   watch=directories, config=cfg)
        mon = Monitor()
        engine = RiskEngine(cfg)
        procs = mon.sample()  # cpu_percent needs a baseline before a useful reading
        controller.refresh(procs)
        verdicts = engine.assess(procs)
        previous = {(p.pid, p.started): p for p in procs.values()}
        levels = {}
        refusals = {}
        messages = []
        stdin = sys.stdin
        input_buffer = ""
        next_sample = time.monotonic() + interval
        running = True
        while running:
            timeout = max(0, next_sample - time.monotonic())
            ready = select.select([stdin] if stdin else [], [], [], timeout)[0]
            if ready:
                data = os.read(stdin.fileno(), 4096)
                if not data:
                    stdin = None
                input_buffer += data.decode("utf-8", errors="replace")
                while "\n" in input_buffer:
                    line, input_buffer = input_buffer.split("\n", 1)
                    remember_demo_children(demo, demo_children)
                    running, message = command(line, procs, verdicts, controller, log)
                    if message:
                        if not line.strip().startswith("inspect "):
                            messages.append(f"{time.strftime('%H:%M:%S')} {message}")
                        print(message, flush=True)
                    if not running:
                        break
                if not running:
                    break
            if time.monotonic() < next_sample:
                continue
            procs = mon.sample()
            controller.refresh(procs)
            remember_demo_children(demo, demo_children)
            changes, observed, unassigned, warnings = watcher.sample(procs)
            for p in procs.values():
                p.file_changes = changes.get(p.pid, 0)
            if observed or warnings:
                log.record("file_activity", observed=observed, unassigned=unassigned,
                           estimated_by_pid=changes, warnings=warnings)
            messages.extend(warnings)
            verdicts = engine.assess(procs, changes)
            current = {(p.pid, p.started): p for p in procs.values()}
            for key in current.keys() - previous.keys():
                p = current[key]
                log.record("process_start", pid=p.pid, started=p.started, ppid=p.ppid, name=p.name)
            for key in previous.keys() - current.keys():
                log.record("process_exit", pid=key[0], started=key[1])
            for pid, p in procs.items():
                v = verdicts[pid]
                key = (pid, p.started)
                if levels.get(key, (0, NORMAL)) != (v.score, v.level):
                    log.record("risk", pid=pid, started=p.started, **asdict(v))
                if (auto and v.level == CONTAIN
                        and pid in controller.targets and pid not in controller.frozen
                        and key not in controller.allowed):
                    try:
                        controller.freeze(pid)
                        log.record("freeze", pid=pid, started=p.started, source="automatic",
                                   score=v.score, reasons=v.reasons)
                        messages.append(f"Frozen PID {pid} ({p.name})")
                    except (ValueError, OSError, psutil.Error) as exc:
                        if refusals.get(key) != str(exc):
                            log.record("freeze_refused", pid=pid, error=str(exc))
                            messages.append(f"PID {pid}: {exc}")
                        refusals[key] = str(exc)
            previous = current
            levels = {(pid, procs[pid].started): (v.score, v.level) for pid, v in verdicts.items()}
            refusals = {key: error for key, error in refusals.items() if key in current}
            if not args.once and not args.no_clear and sys.stdout.isatty():
                print("\033[2J\033[H", end="")  # clear the terminal
            print(f"sentinel  {time.strftime('%H:%M:%S')}  {len(procs)} processes  "
                  f"auto-freeze={'on' if auto else 'off'}  log={args.log}")
            if demo is not None:
                print(f"Demo PID: {demo.pid}  scratch directory: {scratch.name}")
            print(f"File events this sample: {observed}, unassigned: {unassigned}; "
                  "CHG~ is an estimate")
            print_table(procs, verdicts, args.top, controller.frozen)
            print(f"\nFrozen PIDs: {', '.join(map(str, sorted(controller.frozen))) or 'none'}")
            messages = messages[-5:]
            if messages:
                print("Recent messages:")
            for message in messages:
                print(message)
            if not args.once:
                print("Commands: freeze PID | resume PID | kill PID | inspect PID | quit", flush=True)
            if args.once:
                break
            next_sample = time.monotonic() + interval
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, psutil.Error) as exc:
        sys.exit(f"sentinel: {exc}")
    finally:
        # Release our stops before cleaning up the demo or closing the event log.
        if controller is not None:
            resumed, errors = controller.close()
            for error in errors:
                print(f"Could not resume {error}", file=sys.stderr)
            if log is not None:
                try:
                    log.record("shutdown_resume", pids=resumed, errors=errors)
                except OSError:
                    pass
        cleanup_demo(demo, demo_children)
        if watcher is not None:
            watcher.close()
        if scratch is not None:
            scratch.cleanup()
        if log is not None:
            try:
                log.record("session_end")
            finally:
                log.close()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
