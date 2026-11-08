"""Voice warnings via Amazon Polly: ONE combined phrase per zone (all missing items),
cached per phrase.

Polly is called at most once per phrase: results are kept in memory and on disk
(audio_cache/), so restarts don't re-synthesize. If Polly is unavailable the
endpoint returns an error and the browser falls back to speechSynthesis with the
same text.
"""
import hashlib
import threading
from pathlib import Path

import config

CACHE_DIR = Path(__file__).parent / "audio_cache"
_mem = {}
_lock = threading.Lock()


class VoiceUnavailable(Exception):
    pass


def join_labels(gears):
    """["HAND_COVER", "FACE_COVER"] -> "gloves and face cover" (fixed order, no duplicates)."""
    labels = [config.GEAR_LABELS.get(g, g) for g in config.GEAR_TYPES if g in gears]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def phrase(zone_name, gears, zone=None):
    """One generalized message for everyone in the zone - no per-person announcements.
    PPE: "Attention: gloves required in the Chem Lab."
    AI rules are appended as reminders: "Safety rule: No phones."  (same text as app.js)"""
    if isinstance(gears, str):
        gears = [gears]
    ppe = [g for g in gears if not config.is_ai(g)]
    ai = [config.gear_label(g, zone) for g in gears if config.is_ai(g)]
    parts = [config.VOICE_TEXT.format(gear=join_labels(ppe), zone=zone_name)] if ppe else []
    if ai:
        lead = "Safety rule" if len(ai) == 1 else "Safety rules"
        parts.append(f"{'' if ppe else f'Attention, {zone_name}. '}{lead}: {'; '.join(a.rstrip('.') for a in ai)}.")
    return " ".join(parts)


def mp3_for(zone_name, gears, zone=None):
    """Return (mp3_bytes, text). Raises VoiceUnavailable if Polly can't be used."""
    text = phrase(zone_name, gears, zone)
    if config.MOCK_MODE:
        raise VoiceUnavailable("mock mode")
    key = hashlib.sha1(f"{config.POLLY_VOICE_ID}|{config.POLLY_ENGINE}|{text}".encode()).hexdigest()[:16]
    with _lock:
        if key in _mem:
            return _mem[key], text
        path = CACHE_DIR / f"{key}.mp3"
        if path.exists():
            _mem[key] = path.read_bytes()
            return _mem[key], text

    from botocore.exceptions import BotoCoreError, ClientError

    from aws_clients import polly

    try:
        resp = polly.synthesize_speech(
            Text=text, OutputFormat="mp3", VoiceId=config.POLLY_VOICE_ID, Engine=config.POLLY_ENGINE
        )
        audio = resp["AudioStream"].read()
    except (ClientError, BotoCoreError) as e:
        raise VoiceUnavailable(str(e)) from e

    with _lock:
        _mem[key] = audio
        CACHE_DIR.mkdir(exist_ok=True)
        (CACHE_DIR / f"{key}.mp3").write_bytes(audio)
    return audio, text
