import sys

import pytest

from sentinel.__main__ import main


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
