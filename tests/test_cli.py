import sys
from types import SimpleNamespace

import pytest

from sentinel.__main__ import main, print_table
from sentinel.rules import CONTAIN, Verdict


@pytest.mark.parametrize("args", [
    ["--interval", "0"],
    ["--interval", "-1"],
    ["--interval", "nan"],
    ["--interval", "inf"],
    ["--top", "0"],
    ["--top", "-1"],
])
def test_invalid_arguments(monkeypatch, capsys, args):
    monkeypatch.setattr(sys, "argv", ["sentinel", *args])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "must be" in capsys.readouterr().err


def test_malformed_yaml_reports_config_error(monkeypatch, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("rules: [")
    monkeypatch.setattr(sys, "argv", ["sentinel", "--config", str(path)])
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit, match="bad config:"):
        main()


@pytest.mark.parametrize("args, message", [
    (["--contain"], "requires --pid targets"),
    (["--pid", "1"], "--pid must be greater than 1"),
    (["--pid", "-3"], "--pid must be greater than 1"),
])
def test_invalid_control_arguments(monkeypatch, capsys, args, message):
    monkeypatch.setattr(sys, "argv", ["sentinel", *args])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_frozen_row_stays_visible_when_its_score_falls(capsys):
    fields = dict(ppid=1, name="test", cpu=0, rss=0, descendants=0,
                  fds=3, conns=0, write_rate=0, file_changes=0, status="sleeping")
    procs = {pid: SimpleNamespace(pid=pid, **fields) for pid in (100, 200)}
    print_table(procs, {100: Verdict(), 200: Verdict(score=80, level=CONTAIN)},
                top=1, frozen={100: (1, 9)})
    output = capsys.readouterr().out
    assert "FROZEN" in output and "100" in output
    assert "200" not in output
