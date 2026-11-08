"""Offline checks for the step matcher + session state machine (Phase C). No AWS.
Run: python test_matcher.py"""
import json
import pathlib
import sys
import types

import config
import ingest
import matcher
import procedures
import sessions

config.MOCK_MODE = True
# sections 1-9 test STRICT mode; section 10 tests the default lenient mode
config.LENIENT_ADVANCE, config.DONE_MIN_CONFIDENCE = False, 0.7
config.MATCH_MIN_CONFIDENCE, config.DONE_CHECKS, config.MISTAKE_CHECKS, config.SKIP_CHECKS = 0.7, 2, 2, 2

pdf = (pathlib.Path(__file__).parent / "demo" / "color_change_lab.pdf").read_bytes()
STEPS = procedures.normalize(procedures.heuristic_extract(ingest.pypdf_text(pdf)))["steps"]
PROC = {"procedure_id": "demo", "title": "Color-Change Reaction", "steps": STEPS}


def run(s, ticks, start=0):
    out = []
    for i in range(ticks):
        r = sessions.scribe_frames(s["session_id"], [b"jpeg-bytes"], now=s["t0"] + 3 * (start + i + 1))
        out.append(r)
    return out


# 1. Full mock demo: every step advances after 2 confident checks; the scripted mistake
#    (vinegar before food coloring) is flagged once after 2 detections, with a friendly voice line.
s = sessions.start(PROC)
res = run(s, 17)
events = [e for r in res for e in r["state"]["events"]]
kinds = [e["type"] for e in events]
assert kinds == ["step_done", "step_done", "mistake", "step_done", "step_done", "step_done", "complete"], kinds
assert events[0]["voice"].startswith("Step 1 complete. Next: Fill the clear cup")
mistake_ev = events[2]
assert mistake_ev["voice"] == ("Hold on — you poured vinegar before adding the food coloring. "
                               "Step 3 says to add 3 drops of blue food coloring first."), mistake_ev["voice"]
st = sessions.public(s)
assert st["complete"] and st["completed_at"]
assert [x["status"] for x in st["steps_state"]] == ["done"] * 6
assert all(x["evidence"] and x["method"] == "auto" and x["snapshot_id"] for x in st["steps_state"])
fb = st["feedback"]
assert len(fb) == 1 and fb[0]["kind"] == "mistake" and fb[0]["step"] == 3 and fb[0]["mistake_type"] == "wrong_order"
assert fb[0]["corrected"] is True                       # step 3 was then completed properly
assert sessions.snapshot(s["session_id"], fb[0]["snapshot_id"]) == b"jpeg-bytes"
# after completion the matcher stops; the scribe keeps writing
assert "line" in run(s, 1, start=17)[0]

# 2. One confident "done" then a different status -> no advance (needs 2 in a row)
def check(status, conf=0.9, **kw):
    base = {"status": status, "confidence": conf, "evidence": "e", "mistake": False, "mistake_type": "",
            "what_went_wrong": "", "how_to_fix": ""}
    return {**base, **kw}


s = sessions.start(PROC)
sessions.apply_check(s, check("done"), s["t0"] + 3)
sessions.apply_check(s, check("in_progress"), s["t0"] + 6)
sessions.apply_check(s, check("done"), s["t0"] + 9)
assert s["current"] == 0
sessions.apply_check(s, check("done", 0.6), s["t0"] + 12)   # below 0.7 -> doesn't count, resets
sessions.apply_check(s, check("done"), s["t0"] + 15)
assert s["current"] == 0
sessions.apply_check(s, check("done"), s["t0"] + 18)
assert s["current"] == 1

# 3. Mistakes: different types in a row don't flag; same type twice does, only once per step
m1 = dict(mistake=True, mistake_type="wrong_order", what_went_wrong="You added vinegar.", how_to_fix="Step 3 first.")
m2 = dict(mistake=True, mistake_type="wrong_material", what_went_wrong="You used salt.", how_to_fix="Step 2 says water.")
sessions.apply_check(s, check("wrong_action", **m1), s["t0"] + 21)
sessions.apply_check(s, check("wrong_action", **m2), s["t0"] + 24)
assert not s["feedback"]
sessions.apply_check(s, check("wrong_action", **m2), s["t0"] + 27)
assert len(s["feedback"]) == 1 and s["feedback"][0]["mistake_type"] == "wrong_material"
for t in (30, 33, 36):
    sessions.apply_check(s, check("wrong_action", **m2), s["t0"] + t)
assert len(s["feedback"]) == 1                           # not repeated
sessions.apply_check(s, check("wrong_action", 0.5, **m1), s["t0"] + 39)   # unsure -> ignored
sessions.apply_check(s, check("wrong_action", 0.5, **m1), s["t0"] + 42)
assert len(s["feedback"]) == 1

# 4. Possible skipped step: next step seen twice while current isn't done
sessions.apply_check(s, check("next_step_detected"), s["t0"] + 45)
sessions.apply_check(s, check("next_step_detected"), s["t0"] + 48)
skips = [f for f in s["feedback"] if f["kind"] == "possible_skip"]
assert len(skips) == 1 and skips[0]["step"] == 2 and "step 3" in skips[0]["what_went_wrong"]
assert s["current"] == 1                                 # flagged, not auto-advanced

# 5. Manual override: mark done -> advance (manual); undo -> back; dismiss feedback
sid = s["session_id"]
st = sessions.mark_done(sid)
assert st["current"] == 2 and st["steps_state"][1]["method"] == "manual"
assert st["events"][-1]["voice"].startswith("Step 2 complete. Next:")
st = sessions.undo(sid)
assert st["current"] == 1 and st["steps_state"][1]["status"] == "current" and st["steps_state"][2]["status"] == "upcoming"
assert st["events"][0]["voice"].startswith("Okay, back to step 2")
st = sessions.dismiss(sid, skips[0]["id"])
assert [f for f in st["feedback"] if f["kind"] == "possible_skip"][0]["dismissed"] is True
ended = sessions.end(sid)
assert all(f["corrected"] is False for f in ended["feedback"])   # step 2 never completed after them

# 6. normalize: unknown status, unexplained mistakes, clamped confidence
n = matcher.normalize({"status": "finished", "confidence": 3})
assert n["status"] == "in_progress" and n["confidence"] <= 0.4
n = matcher.normalize({"status": "wrong_action", "confidence": 0.9, "mistake": True, "mistake_type": "oops"})
assert n["mistake"] is False                              # unexplained -> not shown to the student
n = matcher.normalize({"status": "done", "confidence": "0.8", "mistake": "false", "evidence": " poured  water "})
assert n == {"status": "done", "confidence": 0.8, "evidence": "poured water", "tip": "", "mistake": False,
             "mistake_type": "", "what_went_wrong": "", "how_to_fix": ""}

# 7. Prompt: whole step list, current + next step, and only the transcript since the current step began
s = sessions.start(PROC)
s["transcript"] = [{"elapsed": 2, "action": "Putting goggles on.", "repeat": 1},
                   {"elapsed": 20, "action": "Pouring water into a clear cup.", "repeat": 2, "last_elapsed": 23}]
s["current"] = 1
s["steps_state"][1]["started_elapsed"] = 18
window = sessions._window(s, s["t0"] + 24)
assert [l["action"] for l in window] == ["Pouring water into a clear cup."]
p = matcher.build_prompt(STEPS, 1, window)
assert "CURRENT step: 2." in p and "NEXT step: 3." in p and "(seen 2 times)" in p and "Putting goggles" not in p

# 8. Real matcher call path (Bedrock stubbed): text-only, no temperature, first JSON object used
calls = []
sys.modules["aws_clients"] = types.SimpleNamespace(bedrock=types.SimpleNamespace(converse=lambda **kw: (calls.append(kw), {
    "output": {"message": {"content": [{"text": json.dumps({"status": "done", "confidence": 0.92, "evidence": "Pouring water",
                                                             "mistake": False}) + "\n{\"status\": \"not_started\"}"}]}}})[1]))
config.BEDROCK_MODEL_ID = "test-model"
r, model = matcher.check(STEPS, 1, window)
assert r["status"] == "done" and r["confidence"] == 0.92 and model == "test-model"
assert "temperature" not in calls[0]["inferenceConfig"] and all("image" not in b for b in calls[0]["messages"][0]["content"])

# 9. Compound step stuck at in_progress -> focused done-check confirms it (2nd call); no confirm on mistakes
replies = [json.dumps({"status": "in_progress", "confidence": 0.8, "evidence": "", "mistake": False}),
           json.dumps({"seen": True, "confidence": 0.9})]
calls.clear()
sys.modules["aws_clients"].bedrock.converse = lambda **kw: (calls.append(kw), {
    "output": {"message": {"content": [{"text": replies[len(calls) - 1]}]}}})[1]
r, _ = matcher.check(STEPS, 1, window)
assert r["status"] == "done" and r.get("confirmed") and len(calls) == 2
assert "Condition:" in calls[1]["messages"][0]["content"][0]["text"]
replies[:] = [json.dumps({"status": "in_progress", "confidence": 0.8, "mistake": False}), json.dumps({"seen": True, "confidence": 0.5})]
calls.clear()
assert matcher.check(STEPS, 1, window)[0]["status"] == "in_progress"      # unsure confirm -> stays in progress
replies[:] = [json.dumps({"status": "wrong_action", "confidence": 0.9, "mistake": True, "mistake_type": "wrong_material",
                          "what_went_wrong": "You added vinegar.", "how_to_fix": "Step 3 first."})]
calls.clear()
r, _ = matcher.check(STEPS, 2, window)
assert len(calls) == 1 and r["mistake_type"] == "wrong_order"           # vinegar is step 5's material -> wrong_order

# 10. LENIENT mode (default): anything resembling the step advances on one check >= 0.5;
#     mistakes stay strict (2 confident detections) and never advance the step.
config.LENIENT_ADVANCE, config.DONE_MIN_CONFIDENCE, config.DONE_CHECKS = True, 0.5, 1
s = sessions.start(PROC)
sessions.apply_check(s, check("not_started"), s["t0"] + 3)
assert s["current"] == 0                                         # unrelated -> stays
sessions.apply_check(s, check("in_progress", 0.55), s["t0"] + 6)
assert s["current"] == 1                                         # roughly doing it -> next step
sessions.apply_check(s, check("in_progress", 0.4), s["t0"] + 9)
assert s["current"] == 1                                         # too unsure
sessions.apply_check(s, check("next_step_detected", 0.6), s["t0"] + 12)
assert s["current"] == 2                                         # already on the next step -> this one counts
sessions.apply_check(s, check("wrong_action", **m1), s["t0"] + 15)
sessions.apply_check(s, check("in_progress", 0.9, **m1), s["t0"] + 18)
assert s["current"] == 2 and len(s["feedback"]) == 1             # mistake flagged after 2, no advance on it
assert not [f for f in s["feedback"] if f["kind"] == "possible_skip"]
sessions.apply_check(s, check("done", 0.6), s["t0"] + 21)
assert s["current"] == 3 and s["feedback"][0]["corrected"] is True
# full mock demo still completes, with the scripted mistake flagged once
s = sessions.start(PROC)
res = run(s, 17)
kinds = [e["type"] for r in res for e in r["state"]["events"]]
assert kinds.count("mistake") == 1 and kinds[-1] == "complete" and kinds.count("step_done") == 5, kinds
# 11. Coach tips: latest tip shown; spoken once the student is stuck for STUCK_SECONDS, not more often
#     than TIP_REPEAT_SECONDS; reset when the step changes. Check history is kept.
config.STUCK_SECONDS, config.TIP_REPEAT_SECONDS = 20, 45
s = sessions.start(PROC)
t0 = s["t0"]
spoken = lambda: [e for e in s["events"] if e["type"] == "tip"]
sessions.apply_check(s, check("not_started", tip="Pick up the goggles."), t0 + 5)
assert s["coach_tip"]["text"] == "Pick up the goggles." and not spoken()
sessions.apply_check(s, check("not_started", tip="Goggles are on the left."), t0 + 21)
assert [e["voice"] for e in spoken()] == ["Goggles are on the left."]
sessions.apply_check(s, check("not_started", tip="Try the goggles."), t0 + 40)
assert len(spoken()) == 1                                         # quiet period
sessions.apply_check(s, check("not_started", tip="Try the goggles."), t0 + 67)
assert len(spoken()) == 2
sessions.apply_check(s, check("done", tip="Great."), t0 + 70)
assert s["current"] == 1 and s["coach_tip"] is None              # new step: fresh start
assert len(s["checks"]) == 5 and s["checks"][-1]["status"] == "done"
assert matcher.normalize({"status": "done", "confidence": 1, "tip": "  Nice   work. "})["tip"] == "Nice work."
config.LENIENT_ADVANCE = False

print("all matcher checks passed")
