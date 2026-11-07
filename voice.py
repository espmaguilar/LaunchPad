"""Tier-1 voice warnings via Amazon Polly, cached per (zone, gear) phrase.

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


def phrase(zone_name, gear):
    return config.VOICE_TEXT.format(gear=config.GEAR_LABELS.get(gear, gear), zone=zone_name)


def mp3_for(zone_name, gear):
    """Return (mp3_bytes, text). Raises VoiceUnavailable if Polly can't be used."""
    text = phrase(zone_name, gear)
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
