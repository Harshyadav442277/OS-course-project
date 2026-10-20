# Sentinel

Sentinel is an OS course project that monitors Linux processes and gives them
rule-based risk scores. It prints a terminal table with resource usage and the
reasons for warning or containment labels. **It currently only reports scores.
Freezing, resuming and killing processes are planned.**

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
The first table appears after one sampling interval so CPU and I/O rates have a
baseline. Press Ctrl+C to stop. Run as your normal user; permissions may limit
details for other users' processes.

Tests: `pip install pytest`, then `python -m pytest`.

## How it works

`__main__.py` loads thresholds through `config.py`, then repeats three steps:

1. `monitor.py` uses psutil and Linux `/proc` data to collect process statistics.
2. `rules.py` adds points for each threshold exceeded and returns scores and reasons.
3. The CLI sorts by risk score, then CPU usage, and prints the top processes.

The OS concepts are PIDs and parent PIDs, process states and lifetimes, process
trees, CPU usage, resident memory, threads, file descriptors, process I/O and
IPv4/IPv6 sockets. The table shows a subset of the collected fields. `KIDS` counts
all descendants, including grandchildren. `FDS` includes files, pipes and sockets.
Memory is measured but has no scoring rule.

Each process then gets a risk score. Every rule that fires adds points:

| Rule | Default threshold | Points |
|---|---|---:|
| CPU | above 90% at each sample over at least 10 s | 15 |
| Descendant processes | more than 50 | 25 |
| Network sockets (including listeners) | more than 100 | 20 |
| File descriptors | more than 500 | 15 |
| File changes (scoring only, no live watcher) | more than 100 in 10 s | 40 |

Under 40 is `NORMAL`, 40 through 69 is `WARNING`, and 70 or more is `CONTAIN`.
`CONTAIN` is a label and sends no signal. Thresholds and points live in
`config.yaml`; omitted values use defaults. The `open_files` config key counts
all file descriptors. The file-change rule accepts supplied counts in tests,
but the CLI does not supply any yet.

## Limitations

1. These are simple heuristics. A busy build or a parent with many descendants
   can score highly without being harmful. This is not malware detection.
2. Sampling can miss short-lived processes and activity between samples. The
   process tree is a snapshot, not a complete history of forks and exits.
3. CPU is relative to one core and can exceed 100%. A new process needs another
   sample before CPU and I/O rates are available. PID and start time together
   identify the stored CPU-rule timer and I/O history, because Linux reuses PIDs.
4. I/O rates count bytes passed through read/write calls, including pipes and
   tmpfs, rather than bytes reaching disk. They do not count changed files.
5. Unreadable file descriptor counts display as `-`. Other unavailable values
   may appear as zero; sockets without a visible owner cannot be assigned a PID.

## Planned features

1. Freeze, resume and kill controls using SIGSTOP, SIGCONT and SIGKILL, with
   protection for Sentinel and essential processes.
2. File activity monitoring using inotify. Mapping events to processes still
   needs a design, since inotify events do not include the writer's PID.
3. Interactive CLI controls, an event log and a harmless demo program.
