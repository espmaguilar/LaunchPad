"""Voice guidance: ElevenLabs (if ELEVENLABS_API_KEY is set) or Amazon Polly (neural), cached per phrase.

Each (provider, voice, phrase) is synthesized at most once: results are kept in memory and on
disk (audio_cache/), so restarts don't re-synthesize. If ElevenLabs fails (bad key, quota), the
same phrase falls back to Polly. If Polly is unavailable the
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


def provider():
    return "elevenlabs" if config.ELEVENLABS_API_KEY else "polly"


def _cache_get(key):
    with _lock:
        if key in _mem:
            return _mem[key]
        path = CACHE_DIR / f"{key}.mp3"
        if path.exists():
            _mem[key] = path.read_bytes()
            return _mem[key]
    return None


def _cache_put(key, audio):
    with _lock:
        _mem[key] = audio
        CACHE_DIR.mkdir(exist_ok=True)
        (CACHE_DIR / f"{key}.mp3").write_bytes(audio)


def _key(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _elevenlabs(text):
    """ElevenLabs text-to-speech (stdlib HTTP, no extra dependency)."""
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
        data=json.dumps({"text": text, "model_id": config.ELEVENLABS_MODEL_ID}).encode(),
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise VoiceUnavailable(f"ElevenLabs HTTP {e.code}: {e.read()[:200]!r}") from e
    except OSError as e:
        raise VoiceUnavailable(f"ElevenLabs unreachable: {e}") from e


def _polly(text):
    from botocore.exceptions import BotoCoreError, ClientError

    from aws_clients import polly

    try:
        resp = polly.synthesize_speech(Text=text, OutputFormat="mp3", VoiceId=config.POLLY_VOICE_ID,
                                       Engine=config.POLLY_ENGINE)
        return resp["AudioStream"].read()
    except (ClientError, BotoCoreError) as e:
        raise VoiceUnavailable(str(e)) from e


def mp3_for(text):
    """Return (mp3_bytes, text). Raises VoiceUnavailable if no voice provider works."""
    text = clean(text)
    if not text:
        raise VoiceUnavailable("empty text")
    if config.ELEVENLABS_API_KEY:
        key = _key("elevenlabs", config.ELEVENLABS_VOICE_ID, config.ELEVENLABS_MODEL_ID, text)
        audio = _cache_get(key)
        if audio:
            return audio, text
        try:
            audio = _elevenlabs(text)
            _cache_put(key, audio)
            return audio, text
        except VoiceUnavailable as e:
            print(f"[voice] {e} - falling back to Polly")
    if config.MOCK_MODE:
        raise VoiceUnavailable("mock mode")
    key = _key(config.POLLY_VOICE_ID, config.POLLY_ENGINE, text)  # same key format as before: old cache still valid
    audio = _cache_get(key)
    if audio:
        return audio, text
    audio = _polly(text)
    _cache_put(key, audio)
    return audio, text
