from pathlib import Path

import pytest

from sentinel import config
from sentinel.monitor import ProcStats
from sentinel.rules import CONTAIN, NORMAL, WARNING, RiskEngine


def proc(pid=100, **kw):
    stats = dict(pid=pid, ppid=1, name="test", user=None, status="running", cpu=0.0,
                 rss=0, threads=1, fds=10, read_rate=0.0, write_rate=0.0, started=1.0)
    stats.update(kw)
    return ProcStats(**stats)


@pytest.fixture
def engine():
    return RiskEngine(config.DEFAULTS)


def test_quiet_process_is_normal(engine):
    v = engine.assess({100: proc()})[100]
    assert (v.score, v.level, v.reasons) == (0, NORMAL, [])


def test_cpu_has_to_stay_high(engine):
    procs = {100: proc(cpu=99.0)}
    assert engine.assess(procs, now=0)[100].score == 0
    assert engine.assess(procs, now=5)[100].score == 0
    assert engine.assess(procs, now=10)[100].score == 15


def test_cpu_timer_resets_when_it_drops(engine):
    engine.assess({100: proc(cpu=99.0)}, now=0)
    engine.assess({100: proc(cpu=20.0)}, now=5)
    assert engine.assess({100: proc(cpu=99.0)}, now=10)[100].score == 0
    assert engine.assess({100: proc(cpu=99.0)}, now=20)[100].score == 15


def test_reused_pid_does_not_inherit_cpu_history(engine):
    engine.assess({100: proc(cpu=99.0, started=1.0)}, now=0)
    # same pid, different process (new start time)
    assert engine.assess({100: proc(cpu=99.0, started=50.0)}, now=10)[100].score == 0


def test_levels(engine):
    # children (25) + connections (20) = 45 -> warning
    v = engine.assess({100: proc(descendants=60, conns=150)})[100]
    assert (v.score, v.level) == (45, WARNING)

    # Supplied file-change counts exercise scoring; there is no live watcher yet.
    procs = {100: proc(cpu=95.0, descendants=71)}
    engine.assess(procs, now=0)
    v = engine.assess(procs, file_changes={100: 342}, now=12)[100]
    assert (v.score, v.level) == (80, CONTAIN)
    assert v.reasons == ["CPU 95% for 12s", "71 descendant processes", "342 file changes in 10s"]


def test_unreadable_fds_dont_count(engine):
    assert engine.assess({100: proc(fds=None)})[100].score == 0


def test_fd_threshold_and_reason(engine):
    assert engine.assess({100: proc(fds=500)})[100].score == 0
    v = engine.assess({100: proc(fds=501)})[100]
    assert (v.score, v.reasons) == (15, ["501 file descriptors"])


def test_cpu_timer_is_forgotten_after_exit(engine):
    engine.assess({100: proc(cpu=99.0)}, now=0)
    engine.assess({}, now=5)
    assert engine.assess({100: proc(cpu=99.0)}, now=10)[100].score == 0


def test_config_override(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("contain_at: 50\nrules:\n  children:\n    count: 5\n")
    cfg = config.load(str(path))
    assert cfg["contain_at"] == 50
    assert cfg["rules"]["children"] == {"count": 5, "score": 25}
    assert cfg["rules"]["cpu"]["percent"] == 90


def test_config_typo_is_an_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("rules:\n  cpu:\n    precent: 50\n")
    with pytest.raises(ValueError, match="rules.cpu.precent"):
        config.load(str(path))


def test_shipped_config_matches_defaults():
    shipped = Path(__file__).parent.parent / "config.yaml"
    assert config.load(str(shipped)) == config.DEFAULTS


@pytest.mark.parametrize("contents, message", [
    ("[]", "config should contain"),
    ("false", "config should contain"),
    ("rules: 5", "rules should be a section"),
    ("interval: 0", "interval must be greater than zero"),
    ("interval: -1", "interval must be a finite, nonnegative number"),
    ("interval: soon", "interval must be a finite, nonnegative number"),
    ("interval: true", "interval must be a finite, nonnegative number"),
    ("interval: .nan", "interval must be a finite, nonnegative number"),
    ("interval: .inf", "interval must be a finite, nonnegative number"),
    ("warn_at: 80", "warn_at must not exceed contain_at"),
    ("rules:\n  file_changes:\n    window: 0", "window must be greater than zero"),
])
def test_invalid_config(tmp_path, contents, message):
    path = tmp_path / "config.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError, match=message):
        config.load(path)


def test_empty_config_uses_independent_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("")
    cfg = config.load(path)
    assert cfg == config.DEFAULTS
    cfg["rules"]["cpu"]["percent"] = 50
    assert config.DEFAULTS["rules"]["cpu"]["percent"] == 90
