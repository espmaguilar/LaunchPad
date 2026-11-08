"""Offline checks for the smart logic: 20 s voice, 30 s single notification,
per-zone batching, hysteresis, stale-zone pause. No AWS, no server.
Uses explicit clock values. Run: python test_tracker.py"""
import threading

import config
from tracker import Tracker

# Pin timings so the test doesn't depend on .env
config.VOICE_AFTER_SECONDS = 20
config.NOTIFY_AFTER_SECONDS = 30
config.MISS_GAP_SECONDS = 6
config.RESOLVE_SECONDS = 10
config.RESOLVE_WINDOW_SECONDS = 300
config.EXPIRE_SECONDS = 600
config.ZONE_STALE_SECONDS = 10

G, F = "HAND_COVER", "FACE_COVER"
REQ = [G]
MISSING = [{"missing": [G], "worn": [], "unknown": []}]
WORN = [{"missing": [], "worn": [G], "unknown": []}]
NOBODY = []
HIDDEN = [{"missing": [], "worn": [], "unknown": [G]}]


class Clock:
    """Feeds frames 2s apart and runs tick() after every frame, like the real server."""

    def __init__(self, t, zone="lab", req=REQ):
        self.t, self.now, self.zone, self.req = t, 0.0, zone, req

    def frames(self, frames, step=2.0):
        out = []
        for f in frames:
            out += self.t.update(self.zone, self.req, f, now=self.now)
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


def of(evs, kind):
    return [e for e in evs if e["type"] == kind]


# 1. Nothing before 20 s of continuous miss; voice incident at 20 s; no notification yet
c = Clock(Tracker())
assert c.frames([MISSING] * 10) == []             # t = 0..18
ev = c.frames([MISSING])                          # t = 20
assert types(ev) == ["violation"] and ev[0]["voice"] and ev[0]["missing_for"] == 20, ev

# 2. Exactly ONE notification at 30 s, never repeated, no escalation
ev = c.frames([MISSING] * 4)                      # t = 22..28
assert ev == [], ev
ev = c.frames([MISSING])                          # t = 30
assert types(ev) == ["notify"] and ev[0]["items"][0]["age"] == 30, ev
ev = c.frames([MISSING] * 200)                    # 400 s more of the same violation
assert not of(ev, "notify") and not of(ev, "violation"), ev
ev = c.frames([MISSING] * 110)                    # past 600 s
assert types(ev) == ["expired", "violation", "notify"], ev  # record stays OPEN; fresh cycle after it
e, v, n = ev
assert v["missing_for"] == 20 and n["items"][0]["age"] == 30, ev

# 3. A compliant frame restarts the 20 s clock
c = Clock(Tracker())
c.frames([MISSING] * 8 + [WORN])                  # 16 s missing, then worn
assert c.frames([MISSING] * 10) == []             # t = 18..36: a fresh streak, only 18 s
assert types(c.frames([MISSING])) == ["violation"]

# 4. Brief no-evidence frames (hands hidden) don't restart it; a long gap does
c = Clock(Tracker())
ev = c.frames([MISSING] * 5 + [HIDDEN, HIDDEN] + [MISSING] * 4)   # t=0..20 with a 4 s blip
assert types(ev) == ["violation"], ev
c = Clock(Tracker())
ev = c.frames([MISSING] * 5 + [NOBODY] * 4 + [MISSING] * 6)       # 8 s with nobody -> restart
assert ev == [], ev

# 5. Two missing items in one zone -> ONE notification listing both
c = Clock(Tracker(), req=[G, F])
BOTH = [{"missing": [G, F], "worn": [], "unknown": []}]
ev = c.frames([BOTH] * 16)
assert len(of(ev, "violation")) == 2, ev
n = of(ev, "notify")
assert len(n) == 1 and {i["gear"] for i in n[0]["items"]} == {G, F}, n
# Face detected a couple of frames later than hands -> still batched into the same message
c = Clock(Tracker(), req=[G, F])
ONLY_G = [{"missing": [G], "worn": [F], "unknown": []}]
ev = c.frames([ONLY_G] * 2 + [BOTH] * 20)
assert len(of(ev, "notify")) == 1 and len(of(ev, "notify")[0]["items"]) == 2, ev

# 6. Fixed between voice and notification -> resolved, no notification ever
c = Clock(Tracker())
c.frames([MISSING] * 11)                          # incident at t=20
ev = c.frames([WORN] * 7)                         # compliant from t=22, resolved at t=32
assert types(ev) == ["resolved"] and ev[0]["notified"] is False, ev
assert not of(c.frames([WORN] * 30), "notify")

# 7. Hysteresis: one miss mid-fix resets it
c = Clock(Tracker())
c.frames([MISSING] * 11)
ev = c.frames([WORN] * 2 + [MISSING] + [WORN] * 4)
assert "resolved" not in types(ev), ev

# 8. Held notification while resolving; goes out if compliance breaks again
c = Clock(Tracker())
c.frames([MISSING] * 14)                          # t up to 26
ev = c.frames([WORN] * 3)                         # resolving across the 30 s mark -> held
assert not of(ev, "notify"), ev
assert of(c.frames([MISSING]), "notify")

# 9. Walking out of frame neither resolves nor holds the notification forever
c = Clock(Tracker())
c.frames([MISSING] * 11)
ev = c.frames([NOBODY, HIDDEN] * 10)
assert "resolved" not in types(ev) and of(ev, "notify"), ev

# 10. Stale-zone pause: camera off -> timers freeze; resume when frames return
#     (stale threshold shortened so the pause starts before the 30 s mark)
config.ZONE_STALE_SECONDS = 4
c = Clock(Tracker())
c.frames([MISSING] * 12)                          # incident at t=20, now t=24 (age 22)
ev = c.idle(120)
assert not of(ev, "notify"), ev
st = c.t.status("lab", REQ, now=c.now)[G]
assert st["paused"] is True and st["notify_in"] > 0, st
ev = c.frames([MISSING] * 8)
n = of(ev, "notify")
assert n and 30 <= n[0]["items"][0]["age"] <= 36, ev   # ~30 s of ACTIVE time despite >140 s wall clock

# 10b. Switching zones: the old zone pauses while the new one gets frames
t = Tracker()
lab, site = Clock(t, "lab"), Clock(t, "site")
lab.frames([MISSING] * 11)
site.now = lab.now
assert not of(site.frames([WORN] * 60), "notify")

config.ZONE_STALE_SECONDS = 10

# 11. Fixed after the 5-minute window -> fixed_late (exposure stands)
c = Clock(Tracker())
c.frames([MISSING] * 11)
c.frames([MISSING] * 160)                         # ~340 s, same incident
assert types(c.frames([WORN] * 7)) == ["fixed_late"]

# 12. Status for the panel
c = Clock(Tracker())
c.frames([MISSING] * 5)                           # t = 0..8
st = c.t.status("lab", REQ, now=c.now)[G]
assert st["open"] is False and st["missing_for"] == 10 and st["voice_after"] == 20, st

# 13. Thread safety smoke test
t = Tracker()
errors = []


def frames():
    try:
        for i in range(2000):
            t.update("lab", REQ, MISSING if i % 50 else WORN, now=i * 0.5)
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

# 14. Manager resolves by hand while the worker is still non-compliant -> fresh 20 s cycle, not an instant re-log
t = Tracker()
c = Clock(t)
c.frames([MISSING] * 11)                          # incident at t=20
t.forget("lab", G, restart_clock=True, now=c.now)
assert c.frames([MISSING] * 10) == []             # t=22..40 (18 s since restart): nothing yet
assert types(c.frames([MISSING])) == ["violation"]
print("manual-resolve check passed")
