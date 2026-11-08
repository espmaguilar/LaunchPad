"""Replay REAL recorded sessions through the step rules (no AWS). Each file in test_data/ holds a procedure, the
transcript the scribe wrote, the matcher's answers at each tick, and the student's manual "Mark step done" presses.
The expectations below are what a careful human would say happened in that run.
Run: python test_replay.py"""
import json
import pathlib

import config
config.SAVE_SESSIONS = False  # don't write test sessions to session_logs/
import sessions

config.MOCK_MODE, config.ADVANCE_MODE, config.LENIENT_ADVANCE, config.INSTANT_MIN_CONFIDENCE = False, "instant", False, 0.3


def replay(path):
    d = json.loads(pathlib.Path(path).read_text())
    s = sessions.start({"procedure_id": "replay", "title": d["procedure"]["title"], "steps": d["procedure"]["steps"]})
    manual = sorted(d.get("manual_done", []), key=lambda m: m["elapsed"])
    for c in d["checks"]:
        while manual and manual[0]["elapsed"] <= c["elapsed"]:
            m = manual.pop(0)
            if s["current"] == m["step"] - 1:
                sessions.mark_done(s["session_id"], now=s["t0"] + m["elapsed"])
        s["transcript"] = [{**l, "objects": [], "repeat": 1} for l in d["transcript"] if l["elapsed"] <= c["elapsed"]]
        r = {"status": c["status"], "confidence": c["confidence"], "evidence": "", "mistake": c["mistake"],
             "mistake_type": "", "what_went_wrong": "", "how_to_fix": ""}
        sessions.apply_check(s, r, s["t0"] + c["elapsed"])
    return s


# Session 61035e: headphones (auto) -> stuffed animal (manual) -> then only "replacing a stuffed animal with a silver
# bottle", "lowering and tilting a silver bottle", "lowering a silver metal bottle", "moving a silver cylinder down",
# "No visible action". No cap was ever unscrewed or screwed, the specimen never turned.
s = replay("test_data/session_61035e.json")
st = s["steps_state"]
assert st[0]["status"] == "done" and st[0]["method"] == "auto" and 11 <= st[0]["done_elapsed"] <= 12, st[0]
assert st[1]["status"] == "done" and st[1]["method"] == "manual", st[1]
assert s["current"] == 2, f"should still be on step 3 (unscrew the cap), is on step {s['current'] + 1}"
assert all(x["status"] != "done" for x in st[2:]), [x["status"] for x in st]
# the one real wrong move in that run: "Removing headphones from head" (step 8) while on step 2 -> one card
assert [(f["step"], f["mistake_type"]) for f in s["feedback"]] == [(2, "wrong_order")], s["feedback"]
assert "step 8" in s["feedback"][0]["what_went_wrong"]

# Session f16faa: a good run - headphones, stuffed animal, unscrewing the cap, tilting the bottle all advanced on
# their own; then only "Lowering a small white bottle" (no screwing) -> stays on step 5, and no false coach cards.
s = replay("test_data/session_f16faa.json")
st = s["steps_state"]
assert [x["method"] for x in st[:4]] == ["auto"] * 4, [x["method"] for x in st]
assert s["current"] == 4, f"should be on step 5, is on step {s['current'] + 1}"
# same real wrong move as in 61035e: headphones removed during step 2 -> exactly one card, nothing else
assert [(f["step"], f["mistake_type"]) for f in s["feedback"]] == [(2, "wrong_order")], [f["what_went_wrong"] for f in s["feedback"]]

# ---------- Simple Demo Lab (session 96e979ab189e) ----------
# 1 Put on the headphones. 2 Place the pillow on the table. 3 Place the stuffed animal on the table.
# 4 Turn the stuffed animal around. 5 Take off the headphones.
D96 = json.loads(pathlib.Path("test_data/session_96e979.json").read_text())

# correct-order run (real): completes every step on its own, no false mistakes
s = replay("test_data/session_96e979.json")
assert [x["method"] for x in s["steps_state"]] == ["auto"] * 5 and s["current"] == 5, [x["status"] for x in s["steps_state"]]
assert not s["feedback"], [f["what_went_wrong"] for f in s["feedback"]]


def scenario(on_step, lines, status="in_progress", conf=0.8):
    """Start the Simple Demo Lab on `on_step` and feed scribe lines one tick at a time (3 s apart). The matcher is
    played as missing the mistake (in_progress, no mistake) - the worst case - so only off-track can catch it."""
    s = sessions.start({"procedure_id": "s96", "title": "Simple Demo Lab", "steps": D96["procedure"]["steps"]})
    sid = s["session_id"]
    while s["current"] < on_step - 1:
        sessions.mark_done(sid)
    t0 = s["steps_state"][s["current"]]["started_elapsed"] or 0
    for i, a in enumerate(lines):
        now = s["t0"] + t0 + 3 * (i + 1)
        sessions._append(s, {"action": a, "objects": [], "focus": None, "boxes": []}, now)
        sessions.apply_check(s, {"status": status, "confidence": conf, "evidence": a, "mistake": False,
                                 "mistake_type": "", "what_went_wrong": "", "how_to_fix": ""}, now)
        yield i, s


# placing the stuffed animal while on "Place the pillow" -> flagged on the FIRST line (no shared key objects)
for i, s in scenario(2, ["Placing a white stuffed animal on the table.", "Holding a white stuffed animal."]):
    if i == 0:
        assert s["current"] == 1, "must not complete 'place the pillow'"
        assert len(s["feedback"]) == 1 and "step 3" in s["feedback"][0]["what_went_wrong"], s["feedback"]
assert len(s["feedback"]) == 1

# "Removing headphones from head" while on step 2 -> flagged once
for i, s in scenario(2, ["Removing headphones from head.", "No visible action.", "Removing headphones from head.",
                         "Holding white headphones."]):
    if i == 0:
        assert len(s["feedback"]) == 1 and "step 5" in s["feedback"][0]["what_went_wrong"]
assert len(s["feedback"]) == 1 and s["current"] == 1

# "Holding and turning the stuffed animal" while on step 3 -> flags the skip (step 4 shares the stuffed animal:
# 2 sightings), and does NOT complete step 3 even though "holding" is a placing action
for i, s in scenario(3, ["Holding and turning a white stuffed animal.", "Holding and turning a white stuffed animal."]):
    assert s["current"] == 2, "turning is step 4 - it must not complete 'place the stuffed animal'"
    if i == 0:
        assert not s["feedback"] and s["off_track"]["need"] == 2
assert len(s["feedback"]) == 1 and "step 4" in s["feedback"][0]["what_went_wrong"]

print("all replay checks passed")
