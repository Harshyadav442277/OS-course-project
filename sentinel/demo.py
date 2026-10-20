import argparse
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path


def run(directory, duration=60):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    children = []

    def stop(signum, frame):
        raise SystemExit(0)

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        with ExitStack() as stack:
            files = [stack.enter_context(open(directory / f"sample-{i}.txt", "ab", buffering=0))
                     for i in range(8)]
            for _ in range(4):
                children.append(subprocess.Popen([
                    sys.executable, "-c", f"import time; time.sleep({duration + 5})",
                ]))
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                for file in files:
                    file.write(b"sentinel demo\n" * 16)
                busy_until = time.monotonic() + 0.03
                while time.monotonic() < busy_until:
                    pass
                time.sleep(0.02)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        signal.signal(signal.SIGTERM, previous)


def main():
    parser = argparse.ArgumentParser(description="Bounded CPU, child-process and file-write demo.")
    parser.add_argument("--directory", required=True, help="scratch directory for eight small files")
    parser.add_argument("--duration", type=int, default=60, help="1 to 120 seconds (default: 60)")
    args = parser.parse_args()
    if not 1 <= args.duration <= 120:
        parser.error("--duration must be between 1 and 120")
    run(args.directory, args.duration)


if __name__ == "__main__":
    main()
