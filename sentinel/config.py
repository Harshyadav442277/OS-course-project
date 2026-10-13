import copy
import os

import yaml

DEFAULTS = {
    "interval": 2,
    "warn_at": 40,
    "contain_at": 70,
    "rules": {
        "cpu":          {"percent": 90, "for_seconds": 10, "score": 15},
        "children":     {"count": 50, "score": 25},
        "file_changes": {"count": 100, "window": 10, "score": 40},
        "connections":  {"count": 100, "score": 20},
        "open_files":   {"count": 500, "score": 15},
    },
}


def load(path=None):
    """Defaults, overridden by whatever is in the yaml file.

    With no path, ./config.yaml is used if it exists.
    """
    cfg = copy.deepcopy(DEFAULTS)
    if path is None:
        if not os.path.exists("config.yaml"):
            return cfg
        path = "config.yaml"
    with open(path) as f:
        user = yaml.safe_load(f) or {}
    merge(cfg, user)
    return cfg


def merge(base, extra, where=""):
    for key, val in extra.items():
        if key not in base:
            # a typo like "precent" would otherwise be silently ignored
            raise ValueError(f"unknown config key: {where}{key}")
        if isinstance(base[key], dict):
            if not isinstance(val, dict):
                raise ValueError(f"{where}{key} should be a section, not a value")
            merge(base[key], val, f"{where}{key}.")
        else:
            base[key] = val
