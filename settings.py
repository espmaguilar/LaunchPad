"""Runtime settings edited from the Settings page.

Values live in settings.json (gitignored) and are applied onto config at startup and
on every save, so code that reads config.* at call time picks them up immediately.
.env stays the source of AWS settings.
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
    "polly_voice": config.POLLY_VOICE_ID,
    "min_person_area": config.MIN_PERSON_AREA,
}


class SettingsError(ValueError):
    pass


def _load():
    try:
        saved = json.loads(PATH.read_text())
        return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def _apply(s):
    config.POLLY_VOICE_ID = s["polly_voice"]
    config.MIN_PERSON_AREA = float(s["min_person_area"])


def current():
    with _lock:
        return _load()


def init():
    _apply(current())


def validate(s):
    try:
        area = float(s.get("min_person_area", 0))
    except (TypeError, ValueError):
        raise SettingsError("minimum person size must be a number.")
    if not 0 <= area <= 0.5:
        raise SettingsError("minimum person size must be between 0 and 0.5.")
    return {
        "polly_voice": s.get("polly_voice") if s.get("polly_voice") in POLLY_VOICES else "Joanna",
        "min_person_area": area,
    }


def save(s):
    s = validate(s)
    with _lock:
        PATH.write_text(json.dumps(s, indent=2))
        _apply(s)
    return s
