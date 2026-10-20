import copy
import math
import os

import yaml

DEFAULTS = {
    "interval": 2,
    "warn_at": 40,
    "contain_at": 70,
    "protected_names": ["systemd", "init", "sshd", "NetworkManager", "sentinel"],
    "rules": {
        "cpu":          {"percent": 90, "for_seconds": 10, "score": 15},
        "children":     {"count": 50, "score": 25},
        "file_changes": {"count": 100, "window": 10, "score": 40},
        "connections":  {"count": 100, "score": 20},
        "open_files":   {"count": 500, "score": 15},
    },
}


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path is None:
        if not os.path.exists("config.yaml"):
            return cfg
        path = "config.yaml"
    with open(path) as f:
        user = yaml.safe_load(f)
    if user is None:
        user = {}
    if not isinstance(user, dict):
        raise ValueError("config should contain YAML sections and values")
    merge(cfg, user)
    if cfg["interval"] <= 0:
        raise ValueError("interval must be greater than zero")
    if cfg["warn_at"] > cfg["contain_at"]:
        raise ValueError("warn_at must not exceed contain_at")
    if cfg["rules"]["file_changes"]["window"] <= 0:
        raise ValueError("rules.file_changes.window must be greater than zero")
    return cfg


def merge(base, extra, where=""):
    for key, val in extra.items():
        if key not in base:
            raise ValueError(f"unknown config key: {where}{key}")
        if isinstance(base[key], dict):
            if not isinstance(val, dict):
                raise ValueError(f"{where}{key} should be a section, not a value")
            merge(base[key], val, f"{where}{key}.")
        elif isinstance(base[key], list):
            if not isinstance(val, list) or not all(isinstance(name, str) and name for name in val):
                raise ValueError(f"{where}{key} must be a list of nonempty names")
            base[key] = val
        else:
            if (isinstance(val, bool) or not isinstance(val, (int, float))
                    or (isinstance(val, float) and not math.isfinite(val)) or val < 0):
                raise ValueError(f"{where}{key} must be a finite, nonnegative number")
            base[key] = val
