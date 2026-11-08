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
assert not s["feedback"], s["feedback"]     # nothing off-track happened in that run

# Session f16faa: a good run - headphones, stuffed animal, unscrewing the cap, tilting the bottle all advanced on
# their own; then only "Lowering a small white bottle" (no screwing) -> stays on step 5, and no false coach cards.
s = replay("test_data/session_f16faa.json")
st = s["steps_state"]
assert [x["method"] for x in st[:4]] == ["auto"] * 4, [x["method"] for x in st]
assert s["current"] == 4, f"should be on step 5, is on step {s['current'] + 1}"
assert not s["feedback"], [f["what_went_wrong"] for f in s["feedback"]]

print("all replay checks passed")
