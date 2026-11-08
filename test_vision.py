"""Offline checks for AI-vision rules: parsing, debounce, same pipeline, fallback.
Bedrock is stubbed - no AWS. Run: python test_vision.py"""
import sys
import types

import config

config.VOICE_AFTER_SECONDS, config.NOTIFY_AFTER_SECONDS = 20, 30
config.RESOLVE_SECONDS, config.RESOLVE_WINDOW_SECONDS, config.EXPIRE_SECONDS = 10, 300, 600
config.ZONE_STALE_SECONDS, config.AI_DEBOUNCE_CHECKS, config.AI_MIN_CONFIDENCE = 10, 2, 0.6

import vision
from tracker import Tracker

ZONE = {"zone_id": "clean-room", "name": "Clean Room", "required": ["FACE_COVER"], "rules": {},
        "unmonitored": [{"item": "No phones", "rule": "Custom rule", "severity": None, "fine": 500, "source": "custom"},
                        {"item": "Safety goggles", "rule": "Sec. 4.1(c)", "severity": "Serious", "fine": None}]}
RULES = config.ai_rules(ZONE)
PH, GG = "AI:no-phones", "AI:safety-goggles"

# 1. Rules, labels, fines
assert [r["key"] for r in RULES] == [PH, GG]
assert config.rule_for(ZONE, PH)["fine"] == 500 and config.rule_for(ZONE, GG)["fine"] == config.AI_DEFAULT_FINE
assert config.gear_label(PH, ZONE) == "No phones" and config.detector(PH) == "AI vision"
assert config.detector("HAND_COVER") == "Rekognition"

# 2. Parsing: fenced JSON, number or echoed text, junk ignored, null kept, confidence clamped
txt = '```json\n[{"rule": 1, "violated": true, "confidence": 0.92, "reason": "Worker holding a phone"},' \
      ' {"rule": "Safety goggles", "violated": null, "confidence": 3, "reason": "Face not visible"},' \
      ' {"rule": 7, "violated": true}]\n```'
p = vision.parse(txt, RULES)
assert p[PH] == {"violated": True, "confidence": 0.92, "reason": "Worker holding a phone"}, p
assert p[GG]["violated"] is None and p[GG]["confidence"] == 1.0
try:
    vision.parse("I can't help with that", RULES)
    raise AssertionError("expected VisionError")
except vision.VisionError:
    pass

# 3. Low-confidence "violated" -> unsure; missing rule -> unsure
res = vision.to_tracker({PH: {"violated": True, "confidence": 0.4, "reason": ""}}, RULES)
assert res == {PH: None, GG: None}, res

# 4. Tracker: 2 consecutive positives -> incident + voice; unsure in between doesn't count as a positive
t = Tracker()
t.update("clean-room", ["FACE_COVER"], [], now=0)  # camera frames keep the zone fresh
assert t.update_ai("clean-room", {PH: True}, now=0) == []
assert t.update_ai("clean-room", {PH: None}, now=10) == []
ev = t.update_ai("clean-room", {PH: True}, now=20)
assert [e["type"] for e in ev] == ["violation"] and ev[0]["gear"] == PH and ev[0]["voice"], ev
# a False resets the count
t2 = Tracker()
t2.update_ai("z", {PH: True}, now=0)
t2.update_ai("z", {PH: False}, now=10)
assert t2.update_ai("z", {PH: True}, now=20) == []

# 5. Same pipeline: notification at 30 s from the first positive, batched with Rekognition items
for s in range(22, 31, 2):
    t.update("clean-room", ["FACE_COVER"], [{"missing": ["FACE_COVER"], "worn": [], "unknown": []}], now=s)
n = [e for e in t.tick(now=30) if e["type"] == "notify"]
assert len(n) == 1 and [i["gear"] for i in n[0]["items"]] == [PH], n
st = t.status("clean-room", ["FACE_COVER", PH], now=30)
assert st[PH]["detector"] == "AI vision" and st[PH]["notified"] and st["FACE_COVER"]["detector"] == "Rekognition"

# 6. Resolve: 2 negative checks 10 s apart (hysteresis 10 s)
assert t.update_ai("clean-room", {PH: False}, now=40) == []
ev = t.update_ai("clean-room", {PH: False}, now=50)
assert [e["type"] for e in ev] == ["resolved"] and ev[0]["notified"] is True, ev

# 7. Model fallback: configured model rejects images -> Nova Lite, remembered
from botocore.exceptions import ClientError
calls = []


def fake_converse(**kw):
    calls.append(kw["modelId"])
    assert kw["messages"][0]["content"][0]["image"]["format"] == "jpeg"
    assert "temperature" not in kw["inferenceConfig"]
    if kw["modelId"] == "text-only-model":
        raise ClientError({"Error": {"Code": "ValidationException",
                                     "Message": "This model doesn't support image input"}}, "Converse")
    return {"output": {"message": {"content": [{"text": '[{"rule": 1, "violated": false, "confidence": 0.8, '
                                                        '"reason": "No phone visible"}, {"rule": 2, "violated": '
                                                        'true, "confidence": 0.9, "reason": "No goggles"}]'}]}}}


sys.modules["aws_clients"] = types.SimpleNamespace(bedrock=types.SimpleNamespace(converse=fake_converse))
config.MOCK_MODE = False
config.BEDROCK_MODEL_ID = "text-only-model"
config.VISION_FALLBACK_MODEL_ID = "us.amazon.nova-lite-v1:0"
vision._active_model = None
parsed, model = vision.check(b"\xff\xd8jpeg", RULES)
assert model == "us.amazon.nova-lite-v1:0" and calls == ["text-only-model", "us.amazon.nova-lite-v1:0"], calls
assert parsed[GG]["violated"] is True and parsed[PH]["violated"] is False
vision.check(b"\xff\xd8jpeg", RULES)
assert calls[-1] == "us.amazon.nova-lite-v1:0" and len(calls) == 3  # no second try on the text model

# 8. Scheduler: at most one check per zone per AI_CHECK_SECONDS; zones without AI rules never run
vision._last_check.clear()
got = []
assert vision.maybe_schedule(ZONE, b"x", lambda *a: got.append(a), now=1000) is True
assert vision.maybe_schedule(ZONE, b"x", lambda *a: got.append(a), now=1003) is False
assert vision.maybe_schedule({**ZONE, "unmonitored": []}, b"x", lambda *a: None) is False
vision._pool.shutdown(wait=True)
assert len(got) == 1 and vision.LAST["clean-room"]["error"] is None

# 9. Voice phrase: PPE + AI rule in one sentence
import voice
assert voice.phrase("Clean Room", ["FACE_COVER", PH], ZONE) == \
    "Attention: face cover required in the Clean Room. Safety rule: No phones."
assert voice.phrase("Clean Room", [PH], ZONE) == "Attention, Clean Room. Safety rule: No phones."

print("all vision checks passed")
