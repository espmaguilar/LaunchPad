"""Runtime settings edited from the Settings page.

Values live in settings.json (gitignored) and are applied onto config at startup and
on every save, so the tracker/vision/voice code (which reads config.* at call time)
picks them up immediately - no restart. .env stays the source of AWS settings.
"""
import json
import threading
from pathlib import Path

import config

PATH = Path(__file__).parent / "settings.json"
_lock = threading.Lock()

POLLY_VOICES = {"Joanna": "Joanna · US English", "Matthew": "Matthew · US English",
                "Ruth": "Ruth · US English", "Stephen": "Stephen · US English"}

DEFAULTS = {
    "workspace_name": "Brightwater Labs",
    "mode": "demo" if config.DEMO_MODE else "production",
    "voice_after_seconds": config.VOICE_AFTER_SECONDS,
    "notify_after_seconds": config.NOTIFY_AFTER_SECONDS,
    "resolve_seconds": config.RESOLVE_SECONDS,
    "polly_voice": config.POLLY_VOICE_ID,
    "min_person_area": config.MIN_PERSON_AREA,
}

# Preset timings for the Demo / Production toggle (user can still edit each value)
PRESETS = {
    "demo": {"voice_after_seconds": 20, "notify_after_seconds": 30, "resolve_seconds": 10},
    "production": {"voice_after_seconds": 20, "notify_after_seconds": 120, "resolve_seconds": 30},
}


class SettingsError(ValueError):
    pass


def _load():
    try:
        return {**DEFAULTS, **json.loads(PATH.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def _apply(s):
    config.DEMO_MODE = s["mode"] == "demo"
    config.VOICE_AFTER_SECONDS = int(s["voice_after_seconds"])
    config.NOTIFY_AFTER_SECONDS = int(s["notify_after_seconds"])
    config.RESOLVE_SECONDS = int(s["resolve_seconds"])
    config.POLLY_VOICE_ID = s["polly_voice"]
    config.MIN_PERSON_AREA = float(s["min_person_area"])


def current():
    with _lock:
        return _load()


def init():
    _apply(current())


def validate(s):
    def num(key, lo, hi, cast=int):
        try:
            v = cast(s[key])
        except (KeyError, TypeError, ValueError):
            raise SettingsError(f"{key.replace('_', ' ')} must be a number.")
        if not lo <= v <= hi:
            raise SettingsError(f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
        return v

    out = {
        "workspace_name": str(s.get("workspace_name") or "").strip()[:60] or DEFAULTS["workspace_name"],
        "mode": s.get("mode") if s.get("mode") in PRESETS else "demo",
        "voice_after_seconds": num("voice_after_seconds", 5, 300),
        "notify_after_seconds": num("notify_after_seconds", 10, 1800),
        "resolve_seconds": num("resolve_seconds", 4, 300),
        "polly_voice": s.get("polly_voice") if s.get("polly_voice") in POLLY_VOICES else "Joanna",
        "min_person_area": num("min_person_area", 0.0, 0.5, float),
    }
    if out["notify_after_seconds"] <= out["voice_after_seconds"]:
        raise SettingsError("The supervisor notification must come after the voice warning.")
    return out


def save(s):
    s = validate(s)
    with _lock:
        PATH.write_text(json.dumps(s, indent=2))
        _apply(s)
    return s
