"""Offline check: a server restart mid-violation must not create a duplicate incident
or a second notification. Mock store in a temp dir - no AWS. Run: python test_incidents.py"""
import pathlib
import tempfile

import config

config.MOCK_MODE = True
config.VOICE_AFTER_SECONDS, config.NOTIFY_AFTER_SECONDS, config.EXPIRE_SECONDS = 20, 30, 600
config.RESOLVE_SECONDS, config.ZONE_STALE_SECONDS = 10, 10

import store
import reports

store.MOCK_DIR = pathlib.Path(tempfile.mkdtemp())
reports.submit = lambda item: None

import incidents
import tracker as tracker_mod

ZONE = {"zone_id": "chem-lab", "name": "Chem Lab", "required": ["HAND_COVER"], "rules": {}, "alert_email": "s@x.example"}
MISSING = [{"missing": ["HAND_COVER"], "worn": [], "unknown": [], "box": {"left": 0, "top": 0, "width": .3, "height": .8}, "status": "violation"}]
WORN = [{"missing": [], "worn": ["HAND_COVER"], "unknown": [], "box": {"left": 0, "top": 0, "width": .3, "height": .8}, "status": "ok"}]
T0 = 1_800_000_000


def run(trk, frames, start, step=2):
    incidents.tracker = trk  # the module-level tracker the pipeline uses
    ui, t = [], start
    for f in frames:
        ev = trk.update("chem-lab", ["HAND_COVER"], f, now=t, meta=incidents.zone_meta(ZONE))
        ui += incidents.process(ev, ZONE, b"jpg", f)
        incidents.process_timed(trk.tick(now=t))
        t += step
    return ui + incidents.drain_ui(), t


# 1. Normal run: one incident, one notification
t1 = tracker_mod.Tracker()
ui, t = run(t1, [MISSING] * 20, T0)                     # 40 s
items = store.list_violations(None)
assert len(items) == 1 and int(items[0]["emails_sent"]) == 1, items
first_ts = items[0]["timestamp"]

# 2. Server restarts (fresh tracker), worker still without gloves -> re-attached, no duplicate, no 2nd email
t2 = tracker_mod.Tracker()
ui, t = run(t2, [MISSING] * 25, t + 5)                  # another 50 s
items = store.list_violations(None)
assert len(items) == 1, [i["timestamp"] for i in items]
assert [e for e in ui if e["type"] == "violation"][0]["reattached"] is True
assert not [e for e in ui if e["type"] == "notify"], ui
assert int(items[0]["voice_warnings"]) == 2               # warned again on site

# 3. ...and the re-attached incident can still resolve
ui, t = run(t2, [WORN] * 7, t)
assert [e["type"] for e in ui] == ["resolved"] and ui[0]["timestamp"] == first_ts, ui
assert store.list_violations(None)[0]["status"] == "RESOLVED"

# 4. A genuinely new violation later (old one resolved) is a new incident
ui, t = run(t2, [MISSING] * 12, t + 60)
assert len(store.list_violations(None)) == 2

# 5. Gloves + face cover missing together -> ONE incident with two items, combined fine
import records
import stats
store.MOCK_DIR = pathlib.Path(tempfile.mkdtemp())
Z2 = {**ZONE, "zone_id": "clean-room", "name": "Clean Room", "required": ["HAND_COVER", "FACE_COVER"],
      "rules": {"HAND_COVER": {"fine": 9500, "source": "policy", "rule": "Sec 4.3(c)"}}}
BOTH = [{"missing": ["HAND_COVER", "FACE_COVER"], "worn": [], "unknown": [], "box": {"left": 0, "top": 0, "width": .3, "height": .8}, "status": "violation"}]
ONLY_FACE = [{"missing": ["FACE_COVER"], "worn": ["HAND_COVER"], "unknown": [], "box": {"left": 0, "top": 0, "width": .3, "height": .8}, "status": "violation"}]
NONE_MISSING = [{"missing": [], "worn": ["HAND_COVER", "FACE_COVER"], "unknown": [], "box": {"left": 0, "top": 0, "width": .3, "height": .8}, "status": "ok"}]


def run2(trk, frames, start, step=2):
    incidents.tracker = trk
    ui, t = [], start
    for f in frames:
        ev = trk.update("clean-room", Z2["required"], f, now=t, meta=incidents.zone_meta(Z2))
        ui += incidents.process(ev, Z2, b"jpg", f)
        incidents.process_timed(trk.tick(now=t))
        t += step
    return ui + incidents.drain_ui(), t


t3 = tracker_mod.Tracker()
# face cover goes missing 6 s after gloves: still the same incident
ui, t = run2(t3, [[{**BOTH[0], "missing": ["HAND_COVER"], "worn": ["FACE_COVER"]}]] * 3 + [BOTH] * 15, T0)
recs = store.list_violations(None)
assert len(recs) == 1, [r["timestamp"] for r in recs]
r = recs[0]
assert [i["gear"] for i in r["items"]] == ["HAND_COVER", "FACE_COVER"], r["items"]
assert int(r["fine"]) == 9500 + 16550
assert sum(1 for e in ui if e["type"] == "notify") == 1          # one message for the incident
assert int(r["emails_sent"]) == 1 and all(i["notified"] for i in r["items"])

# 6. Gloves fixed, face cover still missing -> incident stays OPEN; exposure only counts the face cover
ui, t = run2(t3, [ONLY_FACE] * 7, t)
r = store.list_violations(None)[0]
st_items = {i["gear"]: i["status"] for i in r["items"]}
assert st_items == {"HAND_COVER": "RESOLVED", "FACE_COVER": "OPEN"} and r["status"] == "OPEN", st_items
assert records.exposure(r) == 16550 and records.avoided(r) == 9500
d = stats.dashboard([r], 0, 7, now=__import__("datetime").datetime.fromtimestamp(t, __import__("datetime").timezone.utc))
assert d["current"]["fine_exposure"] == 16550 and d["current"]["fines_avoided"] == 9500
assert {x["label"] for x in d["by_issue"]} == {"gloves", "face cover"}

# 7. Everything fixed -> incident RESOLVED
ui, t = run2(t3, [NONE_MISSING] * 7, t)
r = store.list_violations(None)[0]
assert r["status"] == "RESOLVED" and records.exposure(r) == 0 and int(r["seconds_to_fix"]) > 0, r

# 8. Manager resolves a whole open incident
t4 = tracker_mod.Tracker()
ui, t = run2(t4, [BOTH] * 12, t + 120)
recs = store.list_violations(None)
assert len(recs) == 2
assert incidents.resolve_all("clean-room", recs[0]["timestamp"]) is None
r = store.get_violation("clean-room", recs[0]["timestamp"])
assert r["status"] == "RESOLVED" and r["resolved_by"] == "manager" and all(i["status"] == "RESOLVED" for i in r["items"])

print("all incident (restart / dedupe / grouping) checks passed")
