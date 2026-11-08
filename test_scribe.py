"""Offline checks for the Scribe + sessions (Phase B). Bedrock is stubbed - no AWS.
Run: python test_scribe.py"""
import sys
import threading
import types

import config
import scribe
import sessions
import vision

# 1. normalize: trims, capitalizes, ends with a period, dedupes objects, caps length
n = scribe.normalize({"action": '  pouring water into a clear cup ', "objects": ["Clear Cup", "clear cup", "water", ""]})
assert n == {"action": "Pouring water into a clear cup.", "objects": ["clear cup", "water"]}, n
assert scribe.normalize({"action": " ".join(["word"] * 40)})["action"].endswith("…")
for bad in [{}, {"action": "  "}, "text"]:
    try:
        scribe.normalize(bad)
        raise AssertionError(bad)
    except scribe.ScribeError:
        pass

# 2. model returned one object per frame (seen with Llama 4 Scout): keep the one with an action
reply = '{"action": "Lifting hand to mouth.", "objects": ["hand"]} \n\n{"action": "No visible action", "objects": ["hand"]}'
objs = vision.parse_all(reply)
assert len(objs) == 2
assert scribe.pick(objs)["action"] == "Lifting hand to mouth."
assert scribe.pick(vision.parse_all('```json\n{"action": "No visible action", "objects": []}\n```'))["action"] == "No visible action."
assert scribe.pick(vision.parse_all("I can't see anything")) is None

# 3. describe(): sends the latest 2 frames oldest-first as image blocks, no temperature
calls = []


def fake_converse(**kw):
    calls.append(kw)
    return {"output": {"message": {"content": [{"text": '{"action": "stirring blue water with a spoon", "objects": ["spoon"]}'}]}}}


sys.modules["aws_clients"] = types.SimpleNamespace(bedrock=types.SimpleNamespace(converse=fake_converse))
config.MOCK_MODE, config.BEDROCK_MODEL_ID = False, "test-model"
vision._active_model = None
out = scribe.describe([b"f1", b"f2", b"f3"])
blocks = calls[0]["messages"][0]["content"]
assert [b["image"]["source"]["bytes"] for b in blocks if "image" in b] == [b"f2", b"f3"]
assert "temperature" not in calls[0]["inferenceConfig"]
assert out["action"] == "Stirring blue water with a spoon." and out["model"] == "test-model"

# 4. sessions: transcript lines, repeats merged, timestamps relative to start
proc = {"procedure_id": "p1", "title": "Color-Change Reaction", "steps": [{"number": 1, "instruction": "Goggles"}]}
config.MOCK_MODE = True
s = sessions.start(proc)
sid = s["session_id"]
r1 = sessions.scribe_frames(sid, [], now=s["t0"] + 3)       # mock line 0: "No visible action."
r2 = sessions.scribe_frames(sid, [], now=s["t0"] + 6)       # line 1: picking up goggles
assert r1["new_line"] and r2["new_line"] and r2["line"]["elapsed"] == 6.0
assert len(sessions.get(sid)["transcript"]) == 2

# repeated identical action -> merged (repeat count), not a new line
config.MOCK_MODE = False
fixed = '{"action": "No visible action", "objects": ["cup"]}'
sys.modules["aws_clients"].bedrock.converse = lambda **kw: {"output": {"message": {"content": [{"text": fixed}]}}}
a = sessions.scribe_frames(sid, [b"x"], now=s["t0"] + 9)
b = sessions.scribe_frames(sid, [b"x"], now=s["t0"] + 12)
assert a["new_line"] is True and b["new_line"] is False and b["line"]["repeat"] == 2 and b["line"]["last_elapsed"] == 12.0
assert len(sessions.get(sid)["transcript"]) == 3

# 5. no backlog: while a call is running, new frames are skipped (not queued)
held = sessions.get(sid)["busy"]
held.acquire()
try:
    assert sessions.scribe_frames(sid, [b"x"]) == {"skipped": True}
finally:
    held.release()
assert sessions.get(sid)["skipped"] == 1

# 6. a failing model call doesn't break the session
def boom(**kw):
    raise RuntimeError("throttled")


sys.modules["aws_clients"].bedrock.converse = boom
e = sessions.scribe_frames(sid, [b"x"])
assert "throttled" in e["error"] and sessions.get(sid)["errors"] == 1
assert sessions.public(sessions.get(sid), since=2)["transcript"][0]["repeat"] == 2   # incremental fetch

# 7. ended sessions reject frames; unknown sessions are a clear error
sessions.end(sid)
for fn in (lambda: sessions.scribe_frames(sid, [b"x"]), lambda: sessions.get("nope")):
    try:
        fn()
        raise AssertionError("expected SessionError")
    except sessions.SessionError:
        pass

# 8. concurrent calls on one session: exactly one runs, the rest skip
config.MOCK_MODE = True
s2 = sessions.start(proc)
gate = threading.Event()
orig = scribe.mock_line
scribe.mock_line = lambda tick: (gate.wait(2), orig(tick))[1]
results = []
ths = [threading.Thread(target=lambda: results.append(sessions.scribe_frames(s2["session_id"], []))) for _ in range(5)]
[t.start() for t in ths]
gate.set()
[t.join() for t in ths]
scribe.mock_line = orig
assert sum(1 for r in results if r.get("skipped")) == 4 and sum(1 for r in results if "line" in r) == 1, results

print("all scribe checks passed")
