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
config.ADVANCE_MODE = "careful"   # sections 1-14 test the older modes; section 15 tests the default instant mode
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
# 11. "Feeling confused?": offered once after CONFUSED_AFTER_SECONDS on a camera step; "no" = never again
#     for that step; "yes" = sub-steps checked one by one, finishing them unlocks the next step.
config.CONFUSED_AFTER_SECONDS, config.MOCK_MODE = 25, True
s = sessions.start(PROC)
sid = s["session_id"]
s["step_since"] = s["t0"]
sessions._maybe_offer_help(s, s["t0"] + 10)
assert not [e for e in s["events"] if e["type"] == "confused_offer"]
sessions._maybe_offer_help(s, s["t0"] + 26)
sessions._maybe_offer_help(s, s["t0"] + 60)
assert [e["type"] for e in s["events"]].count("confused_offer") == 1     # once per step
st = sessions.answer_confused(sid, True, now=s["t0"] + 61)
b = st["breakdown"]
assert b["step"] == 1 and len(b["items"]) == 3 and b["current"] == 0
assert any(e["type"] == "breakdown" and "First:" in e["voice"] for e in st["events"])
for i in range(3):                                                        # mock: one sub-step per tick
    sessions._check_substep(s, STEPS[0], s["t0"] + 62 + i)
assert s["current"] == 1 and s["breakdown"] is None                       # unlocked step 2
assert s["steps_state"][0]["evidence"] == "All sub-steps done"
# "no" on step 2 -> never asked again for step 2
s["events"].clear()
s["step_since"] = s["t0"] + 65
sessions._maybe_offer_help(s, s["t0"] + 95)
assert [e["type"] for e in s["events"]].count("confused_offer") == 1
sessions.answer_confused(sid, False, now=s["t0"] + 96)                   # hands out (clears) pending events
sessions._maybe_offer_help(s, s["t0"] + 200)
assert not [e for e in s["events"] if e["type"] == "confused_offer"] and s["confused"][2] == "declined"
# manual sub-step override
sessions.answer_confused(sid, True, now=s["t0"] + 201)
st = sessions.mark_substep_done(sid, now=s["t0"] + 202)
assert st["breakdown"]["current"] == 1 and st["breakdown"]["items"][0]["method"] == "manual"

# 11b. Strict window: the previous step's line never counts for the new step; snapshot = evidence line's frame
s = sessions.start(PROC)
s["transcript"] = [{"elapsed": 16.0, "action": "Placing gray headphones over head.", "repeat": 1, "frame_id": "f1"},
                   {"elapsed": 19.0, "action": "Setting a stuffed animal on the desk.", "repeat": 1, "frame_id": "f2"}]
s["frames"] = {"f1": b"headphones", "f2": b"stuffed-animal"}
s["current"], s["steps_state"][1]["started_elapsed"] = 1, 16.0
w = sessions._window(s, s["t0"] + 20)
assert [l["action"] for l in w] == ["Setting a stuffed animal on the desk."], w
sessions._advance(s, s["t0"] + 20, "Setting a stuffed animal on the desk.", "auto")
assert s["snapshots"][s["steps_state"][1]["snapshot_id"]] == b"stuffed-animal"
s2 = sessions.start(PROC)
s2["transcript"] = [{"elapsed": 16.0, "action": "Placing gray headphones over head.", "repeat": 1}]
s2["current"], s2["steps_state"][1]["started_elapsed"] = 1, 16.0
assert sessions._window(s2, s2["t0"] + 17) == []                          # nothing new yet -> nothing to judge
assert matcher.normalize({"status": "done", "confidence": 1, "evidence": "[00:16] Placing headphones"})["evidence"] == "Placing headphones"
config.MOCK_MODE = True

# 12. Paper steps (calculate/plot): matcher skipped, coach says "Mark step done"; voice mentions it
config.LENIENT_ADVANCE, config.DONE_MIN_CONFIDENCE, config.DONE_CHECKS = True, 0.5, 1
paper_proc = {"procedure_id": "p", "title": "Lab 7", "steps": [
    {"number": 1, "instruction": "Measure the string.", "camera_checkable": True, "expected_action": "ruler"},
    {"number": 2, "instruction": "Calculate the speed.", "camera_checkable": False, "expected_action": ""},
    {"number": 3, "instruction": "Connect the speaker.", "camera_checkable": True, "expected_action": "speaker"}]}
s = sessions.start(paper_proc)
sessions.apply_check(s, check("done"), s["t0"] + 3)
ev = [e for e in s["events"] if e["type"] == "step_done"][-1]
assert s["current"] == 1 and "on paper" in ev["voice"] and "Mark step done" in s["coach_tip"]["text"]
config.MOCK_MODE = True   # mock scribe lines; the paper step must still never auto-advance
for i in range(4):
    sessions.scribe_frames(s["session_id"], [b"f"], now=s["t0"] + 6 + 3 * i)
assert s["current"] == 1                                         # never auto-advanced by the camera
st = sessions.mark_done(s["session_id"])
assert st["current"] == 2 and st["steps_state"][1]["method"] == "manual"
config.LENIENT_ADVANCE = False

# 13. Restart step: old lines stop counting, confused offer + mistake warnings can come back
s = sessions.start(PROC)
sid = s["session_id"]
s["transcript"] = [{"elapsed": 5.0, "action": "Holding a bottle.", "repeat": 1}]
s["confused"][1] = "declined"
s["flagged"].append([1, "mistake:wrong_order"])
s["done_streak"] = 1
st = sessions.restart_step(sid, now=s["t0"] + 10)
assert st["current"] == 0 and st["steps_state"][0]["started_elapsed"] == 10.0
assert 1 not in s["confused"] and s["flagged"] == [] and s["done_streak"] == 0 and s["breakdown"] is None
assert sessions._window(s, s["t0"] + 11) == []                    # the old 5 s line no longer counts
assert any(e["type"] == "restart" and "start step 1 again" in e["voice"] for e in st.get("events", []))
s["current"] = len(s["steps_state"])
try:
    sessions.restart_step(sid); raise AssertionError("restart after the last step should fail")
except sessions.SessionError:
    pass

# 14. Careful advance: in_progress never advances; < MIN_STEP_SECONDS doesn't count; every "done" needs the strict
#     vision finish check (hands lifted high != headphones on); 2 confirmed checks in a row complete the step
config.LENIENT_ADVANCE, config.DONE_MIN_CONFIDENCE, config.DONE_CHECKS = False, 0.7, 2
config.MOCK_MODE, config.CONFIRM_WITH_VISION, config.MIN_STEP_SECONDS = False, True, 5
config.FINISH_MIN_CONFIDENCE, config.IN_PROGRESS_MIN_CONFIDENCE = 0.55, 0.55
seen = []
def fake_finish(frame, step, lines):
    seen.append(frame)
    return fake_finish.answer
matcher.finished_check = fake_finish
s = sessions.start(PROC)
s["last_frame"] = b"frame"
fake_finish.answer = (True, 0.9, "headphones on head")
fake_finish.answer = (False, 0.8, "hands raised, no headphones")
sessions.apply_check(s, check("in_progress", 0.5), s["t0"] + 6)
assert seen == []                                               # weak in_progress (< 55%): not even checked
sessions.apply_check(s, check("in_progress", 0.95), s["t0"] + 6)
sessions.apply_check(s, check("in_progress", 0.95), s["t0"] + 9)
assert s["current"] == 0 and len(seen) == 2                     # confident in_progress -> vision decides (here: no)
seen.clear()
fake_finish.answer = (True, 0.9, "headphones on head")
sessions.apply_check(s, check("done", 0.9), s["t0"] + 3)
assert s["current"] == 0 and seen == [] and s["done_streak"] == 0   # too early: not even checked
fake_finish.answer = (False, 0.8, "hands raised, no headphones")
sessions.apply_check(s, check("done", 0.9), s["t0"] + 6)
sessions.apply_check(s, check("done", 0.9), s["t0"] + 9)
assert s["current"] == 0 and s["done_streak"] == 0 and "no 0.80" in s["checks"][-1]["confirm"]
fake_finish.answer = (True, 0.5, "maybe headphones")             # below 55% -> no
sessions.apply_check(s, check("done", 0.9), s["t0"] + 12)
assert s["current"] == 0
fake_finish.answer = (True, 0.9, "headphones on head")
sessions.apply_check(s, check("done", 0.9), s["t0"] + 15)
assert s["current"] == 0 and s["done_streak"] == 1                # once is not enough
sessions.apply_check(s, check("done", 0.9), s["t0"] + 18)
assert s["current"] == 1                                         # held for 2 checks -> complete
# compound step: matcher keeps saying in_progress ("unscrewing cap", cap not yet set down) -> vision yes twice -> next
fake_finish.answer = (True, 0.85, "cap being unscrewed from water bottle")
sessions.apply_check(s, check("in_progress", 0.6), s["t0"] + 30)
sessions.apply_check(s, check("in_progress", 0.7), s["t0"] + 33)
assert s["current"] == 2
config.MOCK_MODE = True

# 15. Instant mode (default): one resembling check + a line naming the key object -> next step right away
config.ADVANCE_MODE, config.LENIENT_ADVANCE, config.DONE_MIN_CONFIDENCE = "instant", False, 0.55
specimen = {"procedure_id": "s", "title": "Specimen", "steps": [
    {"number": 1, "instruction": "Put on the headphones.", "visual_cues": ["headphones"], "expected_action": "headphones on head"},
    {"number": 2, "instruction": "Place the specimen (stuffed animal) in the center of the workspace, facing the camera.",
     "visual_cues": ["stuffed animal", "workspace"], "expected_action": "stuffed animal centered"},
    {"number": 3, "instruction": "Pick up the reagent bottle (water bottle) and unscrew the cap.", "visual_cues": ["water bottle", "cap"]}]}
assert sessions._key_words(specimen["steps"][1]) == {"stuffed", "animal"}
assert "head" not in sessions._key_words({"instruction": "Put on hearing protection: place the headphones over both ears.", "visual_cues": ["headphones", "head"]})
assert "phone" in sessions._key_words({"instruction": "Pick up the data recorder (iPhone) and take a photo.", "visual_cues": ["iPhone"]})
s = sessions.start(specimen)
s["transcript"] = [{"elapsed": 2.0, "action": "Lifting both hands up high.", "objects": [], "repeat": 1}]
sessions.apply_check(s, check("in_progress", 0.8), s["t0"] + 3)
assert s["current"] == 0 and "no line names" in s["checks"][-1]["confirm"]        # hands up != headphones
s["transcript"].append({"elapsed": 4.0, "action": "Adjusting headphones on head.", "objects": ["headphones"], "repeat": 1})
sessions.apply_check(s, check("in_progress", 0.6), s["t0"] + 4.5)
assert s["current"] == 1                                                          # one check, instantly
s["transcript"].append({"elapsed": 6.0, "action": "Holding a stuffed animal with both hands.", "objects": [], "repeat": 1})
sessions.apply_check(s, check("in_progress", 0.5), s["t0"] + 6.5)
assert s["current"] == 1                                                          # below 55%: not yet
sessions.apply_check(s, check("in_progress", 0.6), s["t0"] + 9)
assert s["current"] == 2
s["transcript"].append({"elapsed": 10.0, "action": "Unscrewing cap from a clear water bottle.", "objects": [], "repeat": 1})
sessions.apply_check(s, check("done", 0.9, mistake=True, mistake_type="wrong_order"), s["t0"] + 10.5)
assert s["current"] == 2                                                          # never while a mistake is reported
sessions.apply_check(s, check("done", 0.9), s["t0"] + 11)
assert s["complete"] if "complete" in s else s["current"] == 3

print("all matcher checks passed")
