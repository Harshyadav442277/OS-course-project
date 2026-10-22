# Hands-on testing

Start Ubuntu explicitly from PowerShell:

```powershell
wsl -d Ubuntu
```

Plain `wsl` may open Docker Desktop. Run the following inside Ubuntu, not Windows
Python. In this workspace, an environment already exists outside OneDrive:

```bash
cd '/mnt/c/Users/hyada/OneDrive/Documents/Study-Related/OS Lab project'
source ~/.venvs/sentinel/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

On another machine, create `.venv` as described in the README. Do not use sudo
for these exercises. Use the PIDs printed by your own run in place of `PID`.

## 1. Check observation mode

```bash
python -m sentinel --once -n 1
```

Expect a process table with `auto-freeze=off`. Compare your shell's PID (`echo $$`),
parent PID, CPU and memory with `ps -eo pid,ppid,stat,pcpu,rss,comm`.
The numbers need not match exactly because the tools sample at different times.

## 2. Run the complete demo

```bash
python -m sentinel --demo --no-clear --log demo-session.jsonl
```

Expect the printed demo PID to have four children, nonzero CPU, nonzero `WRITE/s`
and a rising `CHG~` count. After a few seconds its score should reach at least 70
and its state should become `FROZEN`. Subsequent CPU and write rates should drop.
The score may fall after the process stops; it stays frozen until your decision.

In a second WSL terminal, inspect the actual kernel state:

```bash
ps -o pid,ppid,stat,pcpu,rss,comm -p PID
cat /proc/PID/status
ls -l /proc/PID/fd
cat /proc/PID/io
```

`ps` should show `T` while stopped. The demo's children are sleepers; they remain
separate processes. Enter these commands in Sentinel, one at a time:

```text
inspect PID
resume PID
freeze PID
kill PID
quit
```

After `resume`, verify that the process leaves the stopped state and its file
activity returns. It will not automatically freeze again in that session unless
you manually freeze it. After `kill`, verify that its PID disappears. After
`quit`, the temporary directory and remaining demo children should be gone.
The demo has about 60 seconds of active work. Frozen time does not consume that
work, so you can inspect a stopped process before resuming it. The children wait
on pipes and exit when the parent closes the pipes or ends. If the active work
finishes, start a fresh demo and use its new PID.

Inspect the action record:

```bash
python -c 'import json; [print(json.loads(line)) for line in open("demo-session.jsonl") if json.loads(line)["event"] in ("freeze", "resume", "kill", "shutdown_resume")]'
```

## 3. Verify shutdown releases an external process

In the first terminal:

```bash
sleep 300 &
echo $!
```

In the second terminal, use that PID:

```bash
python -m sentinel --pid PID -n 1
```

Enter `freeze PID`, confirm `T` using `ps`, then press Ctrl+C in Sentinel.
The sleep should return to `S` (sleeping), not be killed. Repeat with `quit`
or SIGTERM if desired. Finish by running `kill PID` from the shell so your test
sleep does not remain. Sentinel refuses to resume stops it did not create.

## 4. Check refusal paths and logs

1. In observation mode, try `freeze PID` for any process. It should be refused
   because you selected no targets.
2. Run `python -m sentinel --pid $$`, then try `freeze` with that shell PID.
   Sentinel must refuse to stop its ancestor. Quit afterward.
3. Run `python -m sentinel --watch /does/not/exist`. It should exit with a clear
   error. `python -m sentinel -n 0` should also reject the argument.
4. In demo mode, verify that unrelated programs keep running. Only the selected
   demo tree is eligible for actions.

The automated suite additionally checks PID reuse, already-stopped processes,
shared writable files, rolling-window expiry and synthetic queue overflow.
Normal shutdown can clean up its own stops; `kill -9` cannot. If you deliberately
test forced termination, record the stopped workload PID first and recover it
from the shell with `kill -CONT PID`.
