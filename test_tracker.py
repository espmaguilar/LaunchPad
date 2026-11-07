"""Offline checks for debounce, tiered escalation, hysteresis, stale-zone pause.
No AWS, no server. Uses explicit clock values. Run: python test_tracker.py"""
import threading

import config
from tracker import Tracker

# Pin demo timings so the test doesn't depend on .env
config.DEBOUNCE_FRAMES = 3
config.VOICE_REPEAT_SECONDS = 10
config.VOICE_MAX_PLAYS = 2
config.ALERT_GRACE_SECONDS = 20
config.ESCALATION_SECONDS = 60
config.RESOLVE_SECONDS = 10
config.RESOLVE_WINDOW_SECONDS = 300
config.ZONE_STALE_SECONDS = 10

G = "HAND_COVER"
REQ = [G]
MISSING = [{"missing": [G], "worn": [], "unknown": []}]
WORN = [{"missing": [], "worn": [G], "unknown": []}]
NOBODY = []
HIDDEN = [{"missing": [], "worn": [], "unknown": [G]}]


class Clock:
    """Feeds frames 2s apart and runs tick() after every frame, like the real server."""

    def __init__(self, t, zone="lab"):
        self.t, self.now, self.zone = t, 0.0, zone

    def frames(self, frames, step=2.0):
        out = []
        for f in frames:
            out += self.t.update(self.zone, REQ, f, now=self.now)
            out += self.t.tick(now=self.now)
            self.now += step
        return out

    def idle(self, seconds, step=1.0):
        """No frames at all (camera off / other zone) - only the ticker runs."""
        out, end = [], self.now + seconds
        while self.now < end:
            out += self.t.tick(now=self.now)
            self.now += step
        return out


def types(evs):
    return [e["type"] for e in evs]


def logged(evs):
    return [e for e in evs if e["type"] == "violation"]


# 1. Debounce: 2 missing frames -> nothing; 3rd -> violation with voice, no email
c = Clock(Tracker())
assert c.frames([MISSING, MISSING]) == []
ev = c.frames([MISSING])
assert types(ev) == ["violation"] and ev[0]["voice"] is True, ev

# 2. Voice repeats once after 10s if still missing, never a third time
ev = c.frames([MISSING] * 5)                      # t = 6..14
assert types(ev) == ["voice_repeat"], ev
assert "voice_repeat" not in types(c.frames([MISSING] * 4))  # t = 16..22 -> email fires at 20 instead

# 3. Max 2 emails per violation: email at 20s, escalation at 60s, then nothing more
c = Clock(Tracker())
ev = c.frames([MISSING] * 40)                     # 80 s of continuous violation
assert len(logged(ev)) == 1, ev                   # still one incident, no duplicates
first_ts = logged(ev)[0]["timestamp"]
mails = [e for e in ev if e["type"] in ("email", "escalation")]
assert types(mails) == ["email", "escalation"], mails
assert mails[0]["age"] >= 20 and mails[1]["age"] >= 60, mails
ev = c.frames([MISSING] * 150)                    # keep going past expiry (300 s)
assert not [e for e in ev if e["type"] in ("email", "escalation") and e["timestamp"] == first_ts]  # no 3rd email
assert "expired" in types(ev)                     # stops tracking after 300 s; record stays OPEN
# A worker still non-compliant after expiry starts a NEW incident - with its own max of 2 emails
per_incident = {}
for e in ev:
    if e["type"] in ("email", "escalation"):
        per_incident[e["timestamp"]] = per_incident.get(e["timestamp"], 0) + 1
assert all(n <= 2 for n in per_incident.values()), per_incident

# 4. Hysteresis: resolves only after 10 s of continuous compliance; a miss resets it
c = Clock(Tracker())
c.frames([MISSING] * 3)                           # violation at t=4
ev = c.frames([WORN] * 4 + [MISSING] + [WORN] * 4)  # compliance broken mid-way
assert "resolved" not in types(ev), ev
ev = c.frames([WORN] * 2)
assert types(ev) == ["resolved"] and ev[0]["emails_sent"] == 0, ev

# 5. Resolved before the email -> no email ever, counts as fine avoided ("resolved" not "fixed_late")
c = Clock(Tracker())
c.frames([MISSING] * 3)                           # logged at t=4
ev = c.frames([WORN] * 7)                         # compliant from t=6, resolved at t=16 (< 20 s grace)
assert types(ev) == ["resolved"], ev
assert not [e for e in c.frames([WORN] * 30) if e["type"] in ("email", "escalation")]

# 6. Held email while resolving: grace passes mid-resolve -> email waits; resolution cancels it
c = Clock(Tracker())
c.frames([MISSING] * 3)                           # logged at t=4
c.frames([MISSING] * 6)                           # t = 6..16, still missing
ev = c.frames([WORN] * 6)                         # compliant from t=18; grace hits at t=24 while resolving
assert "email" not in types(ev), ev               # held
assert types(ev) == ["resolved"], ev              # resolved at t=28 -> no email at all

# 6b. ...but if compliance breaks again, the held email goes out
c = Clock(Tracker())
c.frames([MISSING] * 3)
c.frames([MISSING] * 7)                           # t up to 18
ev = c.frames([WORN] * 3)                         # resolving across the 24 s grace -> held
assert "email" not in types(ev), ev
ev = c.frames([MISSING])
assert "email" in types(ev), ev

# 7. Walking out of frame neither resolves nor holds emails forever
c = Clock(Tracker())
c.frames([MISSING] * 3)
ev = c.frames([NOBODY, HIDDEN] * 10)              # 40 s with no evidence
assert "resolved" not in types(ev) and "email" in types(ev), ev

# 8. Stale-zone pause: camera off -> timers freeze; resume when frames return
c = Clock(Tracker())
c.frames([MISSING] * 3)                           # logged at t=4, now t=6
c.frames([MISSING] * 2)                           # active age ~6 s at t=10
ev = c.idle(120)                                  # 2 minutes with no frames from this zone
assert not [e for e in ev if e["type"] in ("email", "escalation")], ev  # paused: nothing fires
st = c.t.status("lab", REQ, now=c.now)[G]
assert st["paused"] is True and st["email_in"] > 0, st
wall_age = c.now - 4
ev = c.frames([MISSING] * 5)                      # frames return
mail = [e for e in ev if e["type"] == "email"]
assert mail and 20 <= mail[0]["age"] <= 25, ev    # fires on ~20 s of ACTIVE time...
assert wall_age > 100                             # ...even though >100 s of wall-clock time passed
assert c.t.status("lab", REQ, now=c.now)[G]["paused"] is False

# 8b. Switching zones: the old zone pauses while the new one gets frames
t = Tracker()
lab, site = Clock(t, "lab"), Clock(t, "site")
lab.frames([MISSING] * 3)
site.now = lab.now
ev = site.frames([WORN] * 60)                     # 2 minutes on the other zone; lab gets no frames
assert not [e for e in ev if e["type"] in ("email", "escalation")], ev

# 9. Fixed after the 5-minute window -> fixed_late (exposure stands), not "resolved"
#    Only possible with production timings (escalation 600 s keeps it tracked past the 300 s window).
config.ESCALATION_SECONDS = 600
c = Clock(Tracker())
c.frames([MISSING] * 3)
c.frames([MISSING] * 160)                         # t ~ 326 s, still the same incident
ev = c.frames([WORN] * 7)
assert types(ev) == ["fixed_late"], ev
config.ESCALATION_SECONDS = 60

# 10. Zones and gear types are independent
t = Tracker()
for i in range(3):
    t.update("lab", REQ, MISSING, now=i)
assert t.update("other", REQ, MISSING, now=3) == []

# 11. Thread safety smoke test: update() and tick() hammering the same tracker
t = Tracker()
errors = []


def frames():
    try:
        for i in range(2000):
            t.update("lab", REQ, MISSING if i % 7 else WORN, now=i * 0.5)
    except Exception as e:
        errors.append(e)


def ticks():
    try:
        for i in range(2000):
            t.tick(now=i * 0.5)
    except Exception as e:
        errors.append(e)


ths = [threading.Thread(target=frames), threading.Thread(target=ticks)]
[th.start() for th in ths]
[th.join() for th in ths]
assert not errors, errors

print("all tracker checks passed")
