"""Voice guidance via Amazon Polly (neural), cached per phrase.

Polly is called at most once per (voice, phrase): results are kept in memory and on
disk (audio_cache/), so restarts don't re-synthesize. If Polly is unavailable the
endpoint returns an error and the browser falls back to speechSynthesis with the
same text. Step phrases ("Step 2 complete. Next: ...") repeat across sessions, so
the cache makes them instant.
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


def clean(text):
    return " ".join(str(text or "").split())[: config.MAX_VOICE_CHARS]


def mp3_for(text):
    """Return (mp3_bytes, text). Raises VoiceUnavailable if Polly can't be used."""
    text = clean(text)
    if not text:
        raise VoiceUnavailable("empty text")
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
        resp = polly.synthesize_speech(Text=text, OutputFormat="mp3", VoiceId=config.POLLY_VOICE_ID,
                                       Engine=config.POLLY_ENGINE)
        audio = resp["AudioStream"].read()
    except (ClientError, BotoCoreError) as e:
        raise VoiceUnavailable(str(e)) from e

    with _lock:
        _mem[key] = audio
        CACHE_DIR.mkdir(exist_ok=True)
        (CACHE_DIR / f"{key}.mp3").write_bytes(audio)
    return audio, text
