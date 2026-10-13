# Sentinel

OS course project. Sentinel watches the processes running on a Linux machine and
freezes the ones that start behaving badly (CPU spikes, forking lots of children,
rewriting lots of files) so you can decide whether to let them continue or kill them.

Still a work in progress.

## Running it

Needs Linux and Python 3.10+. On Windows it works inside WSL.

```
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m sentinel
```

Options: `-n 1` samples every second instead of every 2, `--top 30` shows more rows,
`--once` prints a single table and exits, `-c file.yaml` uses a different config.
Without root you only get full details (open files, connections) for your own
processes.

Tests: `pip install pytest`, then `python -m pytest`.

## How it works so far

Every interval the monitor walks all processes (psutil, which reads `/proc/<pid>/stat`,
`status`, `fd` and `io`) and records CPU, resident memory, threads, open file
descriptors, read/write rate and inet connections. Child counts are worked out from the
parent pid of every process, so `KIDS` in the table is the whole subtree under a
process, not just its direct children.

Each process then gets a risk score. Every rule that fires adds points:

| Rule | Default threshold | Points |
|---|---|---:|
| CPU | above 90% for 10 s straight | 15 |
| Child processes | more than 50 | 25 |
| File changes | more than 100 in 10 s | 40 |
| Network connections | more than 100 | 20 |
| Open files | more than 500 | 15 |

Under 40 is normal, 40-69 is a warning, 70 and up means the process should be
contained. All of these numbers live in `config.yaml`.

## Plan

- [x] process monitor
- [x] risk scoring rules
- [ ] freeze / resume / kill (SIGSTOP, SIGCONT, SIGKILL)
- [ ] file activity monitor (inotify)
- [ ] dashboard and event log
