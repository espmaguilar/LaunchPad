"""Offline checks for the voice providers (no network, no AWS). Run: python test_voice.py"""
import io
import pathlib
import tempfile
import urllib.error
import urllib.request

import config
import voice

voice.CACHE_DIR = pathlib.Path(tempfile.mkdtemp())
calls = []


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def fake_urlopen(req, timeout=0):
    calls.append(req)
    return Resp(b"ELEVEN-MP3")


# 1. ElevenLabs used when a key is set: right URL (voice id), key header, model; result cached
urllib.request.urlopen = fake_urlopen
config.MOCK_MODE, config.ELEVENLABS_API_KEY, config.ELEVENLABS_VOICE_ID = False, "test-key", "dmWfLQZJq7wNPv9WmtiM"
audio, text = voice.mp3_for("  Step 1 complete.  ")
assert audio == b"ELEVEN-MP3" and text == "Step 1 complete." and voice.provider() == "elevenlabs"
req = calls[0]
assert req.full_url.endswith("/v1/text-to-speech/dmWfLQZJq7wNPv9WmtiM") and req.get_header("Xi-api-key") == "test-key"
voice.mp3_for("Step 1 complete.")
assert len(calls) == 1                                   # cached: no second request

# 2. ElevenLabs failing (bad key / quota) -> falls back to Polly
def failing(req, timeout=0):
    raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b"bad key"))


urllib.request.urlopen = failing
voice._polly = lambda text: b"POLLY-MP3"
assert voice.mp3_for("A new phrase.")[0] == b"POLLY-MP3"

# 3. No key -> Polly; mock mode without key -> unavailable (browser speech)
config.ELEVENLABS_API_KEY = ""
assert voice.provider() == "polly" and voice.mp3_for("Another phrase.")[0] == b"POLLY-MP3"
config.MOCK_MODE = True
try:
    voice.mp3_for("Mock phrase.")
    raise AssertionError("expected VoiceUnavailable")
except voice.VoiceUnavailable:
    pass

print("all voice checks passed")
