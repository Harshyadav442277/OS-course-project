# Sentinel

Sentinel is an OS course project for monitoring Linux processes, scoring unusual
activity and pausing selected high-risk processes. It has a terminal dashboard,
manual signal controls, file activity monitoring and a JSON Lines event log.

## Run

Use Linux (or WSL), Python 3.10+, and a normal user account. Signal controls need
Linux pidfd support, available on kernels 5.3 and later.

On Windows, enter Ubuntu from PowerShell first with `wsl -d Ubuntu`. Plain `wsl`
may open Docker Desktop if it is your default distribution. Run the commands below
inside Ubuntu, not PowerShell or Docker's shell.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m sentinel
```

This monitors all visible processes without sending signals. The first table
appears after one sampling interval. For a complete demonstration:

```bash
python -m sentinel --demo
```

The demo uses lower thresholds from `demo.yaml`, writes eight small files in a
temporary directory, uses part of one CPU core and starts four children waiting
on pipes. The workload has about 60 seconds of active work; frozen time does not
consume it, and the children exit when the parent closes their pipes or exits.
Only the demo and its descendants are eligible for controls. After a few samples,
the demo should show `FROZEN`. Quitting cleans up its processes and scratch files.

For another workload, select its PID and the directory to watch:

```bash
python -m sentinel --pid 12345 --watch /path/to/scratch --contain
```

`--contain` automatically sends SIGSTOP when a selected process reaches the risk
threshold. Without it, selected processes can still be controlled manually.
Selecting a PID also selects descendants discovered while it is monitored.

| Option | Purpose |
|---|---|
| `-c file.yaml` | Override configuration values |
| `-n 1` | Sample every second (default: 2) |
| `--top 30` | Show more rows (default: 15) |
| `--once` | Print one sample and exit |
| `--pid PID` | Select processes eligible for controls; repeatable |
| `--watch DIR` | Watch immediate files in a directory; repeatable |
| `--log path.jsonl` | Event log path (default: `sentinel.jsonl`) |
| `--no-clear` | Keep previous tables on screen |

Type a command and press Enter:

```text
inspect PID
freeze PID
resume PID
kill PID
quit
```

`resume` approves that process for the rest of the session, preventing an immediate
automatic freeze. A manual `freeze` removes that approval. `kill` sends SIGKILL
only on request. Signals affect the specified PID, not its entire process group.
`quit`, Ctrl+C, SIGTERM and SIGHUP resume processes stopped by this session.
An unrelated process that was already stopped is not claimed or resumed.

## How it works

1. `monitor.py` uses psutil and Linux `/proc` data to collect PIDs, parent PIDs,
   states, CPU, RSS memory, threads, file descriptors, I/O rates and network sockets.
   Parent links give direct-child and whole-subtree counts.
2. `files.py` reads inotify events and estimates which process modified a file
   using writable `/proc/PID/fd` entries, inode numbers and recent I/O activity.
3. `rules.py` adds points for exceeded thresholds. `config.py` loads and checks YAML.
4. `contain.py` checks scope, ownership and protected processes before sending
   signals through pidfds. PID plus start time identifies stored process history.
5. `__main__.py` runs the sampling loop, displays results and accepts commands.
   `events.py` records risk changes, process arrivals/exits, file activity and actions.

| Rule | Default threshold | Points |
|---|---|---:|
| CPU | Above 90% at samples spanning at least 10 seconds | 15 |
| Descendants | More than 50 | 25 |
| Estimated file modifications | More than 100 observed MODIFY events in 10 seconds | 40 |
| Network sockets, including listeners | More than 100 | 20 |
| File descriptors, including pipes and sockets | More than 500 | 15 |

Under 40 is `NORMAL`, 40 through 69 is `WARNING`, and 70 or more is `CONTAIN`.
`CONTAIN` is the score label; `FROZEN` confirms Sentinel sent SIGSTOP. The CLI also
shows the latest OS process state. `KIDS` includes grandchildren; `CHG~` is the
estimated modification count over the configured window. Memory is measured but
has no scoring rule. The `open_files` configuration key counts all descriptors.
Frozen rows stay above other rows even when their score falls. Use `--no-clear`
when inspecting a process so its detailed snapshot stays in terminal scrollback.

## Limits

1. These are heuristics, not malware detection. Busy builds and large process
   trees can score highly. Sentinel, its ancestors, PID 1, configured protected
   names and other users' processes cannot be signalled through the controls.
2. Sampling misses short-lived processes and activity between samples. CPU is
   relative to one core and can exceed 100%. New processes need a rate baseline.
3. inotify supplies no writer PID and can coalesce events. Only MODIFY events
   with one visible active writable owner are attributed. Closed descriptors,
   multiple writers, permissions and changes between scans leave gaps or can
   produce an incorrect estimate. Creates, deletes and renames are observed but
   not assigned to a process. `CHG~` is not an exact count of writes or changed files.
4. Watches are nonrecursive. Replaced or moved watched directories require a
   restart. Queue overflow is reported and clears accumulated file scores.
5. I/O rates count syscall bytes, including pipes and tmpfs, rather than disk I/O.
   Missing descriptor counts show `-`; some other unavailable values show zero.
6. Signals affect individual processes, so children may continue after a parent
   is frozen or killed. SIGKILL or a system crash cannot run shutdown cleanup;
   a remaining stopped workload can be resumed with `kill -CONT PID`.
7. The append-only log grows until you rotate or remove it. It records observed
   process arrivals/exits, not every fork or exit between samples.

## Testing and next improvements

```bash
pip install pytest
python -m pytest
```

Linux tests launch real temporary processes and exercise CPU/I/O readings,
inotify, signals, automatic containment, interactive commands and shutdown cleanup.
See [TESTING.md](TESTING.md) for hands-on checks and the existing WSL setup.

Possible next improvements are recursive watches, better file-writer attribution,
process-group controls and log rotation. cgroups and eBPF are outside this version.
