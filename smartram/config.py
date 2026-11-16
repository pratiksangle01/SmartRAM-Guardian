"""User configuration and runtime file locations."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .rules import normalize

# key: (default, min, max)
_NUMBERS = {
    "interval_seconds": (5, 1, 300),
    "act_above_percent": (60, 0, 100),
    "idle_cpu_percent": (1.0, 0.0, 100.0),
    "idle_samples": (3, 1, 100),
    "min_process_mb": (10, 0, 100000),
    "close_timeout_seconds": (10, 1, 300),
    "retry_minutes": (10, 0, 1440),
    "max_retries": (3, 1, 100),
}
MODES = ("auto", "observe")
DEFAULTS = dict({k: v[0] for k, v in _NUMBERS.items()},
                mode="auto", close_list=[], protect_list=[], widget=True)


class Paths:
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get("SMARTRAM_HOME")
                         or Path.home() / ".smartram-guardian")
        self.config = self.home / "config.json"
        self.state = self.home / "state.json"
        self.pid = self.home / "guardian.pid"
        self.pause = self.home / "guardian.pause"
        self.stop = self.home / "guardian.stop"
        self.log = self.home / "guardian.log"

    def ensure(self):
        self.home.mkdir(parents=True, exist_ok=True)
        return self


def validate(raw):
    """Return (clean config, warnings). Bad values fall back to defaults."""
    cfg, warnings = dict(DEFAULTS, close_list=[], protect_list=[]), []
    if not isinstance(raw, dict):
        return cfg, ["config is not a JSON object; using defaults"]
    for key, (default, lo, hi) in _NUMBERS.items():
        if key not in raw:
            continue
        v = raw[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            warnings.append("%s must be a number in %s..%s; using %s" % (key, lo, hi, default))
        else:
            cfg[key] = v
    if "mode" in raw:
        if raw["mode"] in MODES:
            cfg["mode"] = raw["mode"]
        else:
            warnings.append("mode must be auto or observe; using auto")
    if "widget" in raw:
        cfg["widget"] = bool(raw["widget"])
    for key in ("close_list", "protect_list"):
        v = raw.get(key, [])
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            cfg[key] = sorted({normalize(x) for x in v if normalize(x)})
        else:
            warnings.append("%s must be a list of process names; ignoring" % key)
    return cfg, warnings


def load(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return validate({})
    except (OSError, ValueError) as e:
        cfg, _ = validate({})
        return cfg, ["cannot read config (%s); using defaults" % e]
    return validate(raw)


def write_json(path, data):
    """Atomic write so readers never see a half-written file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def save(path, cfg):
    write_json(path, {k: cfg[k] for k in DEFAULTS})
