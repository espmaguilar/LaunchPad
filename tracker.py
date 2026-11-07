"""Smart logic: debounce, tiered escalation, hysteresis. Pure in-memory, no AWS.

Per (zone, gear type):
  miss      consecutive frames where someone is missing that gear
  ok_since  when continuous compliance started (None = not currently compliant)
  open      the logged violation we're escalating / watching for a fix

Frame rules for a gear type (update(), called per camera frame):
  someone missing it            -> miss += 1, ok_since = None (compliance broken)
  people seen, all wearing it   -> miss = 0, ok_since = ok_since or now
  no evidence (nobody visible,
  or body part hidden)          -> miss = 0, ok_since unchanged
     (walking out of frame neither confirms nor breaks a fix)

Tiers for one violation:
  Tier 1  miss reaches DEBOUNCE_FRAMES  -> log violation + VOICE warning (no email).
          Still missing VOICE_REPEAT_SECONDS later -> one repeat (max VOICE_MAX_PLAYS).
  Tier 2  still OPEN after ALERT_GRACE_SECONDS  -> ONE supervisor email.
  Tier 3  still OPEN after ESCALATION_SECONDS   -> ONE escalation email.
          => never more than 2 emails per violation.
  Emails are held while the violation is mid-resolve (compliance timer running).
  Stale zone: no frames for ZONE_STALE_SECONDS (camera stopped / switched zones)
  -> email timers PAUSE (paused time doesn't count) and resume when frames return.

Thread safety: update() (request threads), tick() (background thread) and
status() all take the same lock.

Resolve (hysteresis): gear visibly worn and no miss for RESOLVE_SECONDS.
  fixed within RESOLVE_WINDOW_SECONDS of logging -> RESOLVED, fine avoided
  fixed later                                    -> fixed_late (exposure stands)
Expire: unfixed after max(RESOLVE_WINDOW, ESCALATION) -> stop tracking; it stays
  OPEN and a new streak can log a new incident.
"""
import threading
import time
from datetime import datetime, timezone

import config


def utc_timestamp(epoch):
    """Sortable ISO-8601 UTC string with milliseconds, e.g. 2026-11-07T20:15:03.123Z"""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.miss = {}
        self.ok_since = {}
        self.open = {}  # key -> dict, see _new_open
        self.last_frame = {}  # zone_id -> time of last frame

    @staticmethod
    def _new_open(ts, now, meta):
        return {"timestamp": ts, "logged_at": now, "voice_count": 1, "last_voice_at": now,
                "emailed_at": None, "escalated_at": None, "meta": meta or {},
                "paused_total": 0.0, "paused_since": None}

    @staticmethod
    def _active_age(o, now):
        """Seconds open, excluding time the zone's camera was offline."""
        paused = o["paused_total"] + ((now - o["paused_since"]) if o["paused_since"] is not None else 0)
        return now - o["logged_at"] - paused

    def _stale(self, zone_id, now):
        return now - self.last_frame.get(zone_id, now) > config.ZONE_STALE_SECONDS

    # ---------------- per frame ----------------
    def update(self, zone_id, required, persons, now=None, meta=None):
        now = time.time() if now is None else now
        events = []
        with self.lock:
            # Frames are back: resume any paused timers in this zone.
            self.last_frame[zone_id] = now
            for (z, _), o in self.open.items():
                if z == zone_id and o["paused_since"] is not None:
                    o["paused_total"] += now - o["paused_since"]
                    o["paused_since"] = None

            for gear in required:
                key = (zone_id, gear)
                missing_now = any(gear in p["missing"] for p in persons)
                worn_now = not missing_now and any(gear in p["worn"] for p in persons)

                if missing_now:
                    self.miss[key] = self.miss.get(key, 0) + 1
                    self.ok_since[key] = None
                elif worn_now:
                    self.miss[key] = 0
                    if self.ok_since.get(key) is None:
                        self.ok_since[key] = now
                else:
                    self.miss[key] = 0

                opened = self.open.get(key)

                # Tier 1: debounced violation -> voice warning
                if opened is None and self.miss[key] >= config.DEBOUNCE_FRAMES:
                    ts = utc_timestamp(now)
                    self.open[key] = self._new_open(ts, now, meta)
                    self.ok_since[key] = None
                    events.append({"type": "violation", "gear": gear, "timestamp": ts, "voice": True,
                                   "people": sum(gear in p["missing"] for p in persons)})
                    continue
                if opened is None:
                    continue

                # Tier 1 repeat: still missing after VOICE_REPEAT_SECONDS
                if (missing_now and opened["voice_count"] < config.VOICE_MAX_PLAYS
                        and now - opened["last_voice_at"] >= config.VOICE_REPEAT_SECONDS):
                    opened["voice_count"] += 1
                    opened["last_voice_at"] = now
                    events.append({"type": "voice_repeat", "gear": gear, "timestamp": opened["timestamp"]})

                # Hysteresis: resolve only after RESOLVE_SECONDS of continuous compliance
                ok_since = self.ok_since.get(key)
                if worn_now and ok_since is not None and now - ok_since >= config.RESOLVE_SECONDS:
                    del self.open[key]
                    fix_secs = max(0, round(ok_since - opened["logged_at"]))
                    in_window = ok_since - opened["logged_at"] <= config.RESOLVE_WINDOW_SECONDS
                    events.append({
                        "type": "resolved" if in_window else "fixed_late",
                        "gear": gear,
                        "timestamp": opened["timestamp"],
                        "seconds_to_fix": fix_secs,
                        "emails_sent": int(opened["emailed_at"] is not None) + int(opened["escalated_at"] is not None),
                    })
        return events

    # ---------------- time-based (background thread) ----------------
    def tick(self, now=None):
        """Email tiers + expiry for every open violation in every zone."""
        now = time.time() if now is None else now
        events = []
        expire_after = max(config.RESOLVE_WINDOW_SECONDS, config.ESCALATION_SECONDS)
        with self.lock:
            for key, o in list(self.open.items()):
                zone_id, gear = key
                if self._stale(zone_id, now):
                    if o["paused_since"] is None:
                        o["paused_since"] = now
                    continue  # camera offline: timers frozen, nothing fires
                age = self._active_age(o, now)
                base = {"zone_id": zone_id, "gear": gear, "timestamp": o["timestamp"], "meta": o["meta"]}
                # Hold emails while a fix is being confirmed - but not forever
                # (e.g. compliant once, then walked out of frame).
                ok_since = self.ok_since.get(key)
                resolving = ok_since is not None and now - ok_since <= config.RESOLVE_SECONDS + 5
                if not resolving:
                    if o["emailed_at"] is None and age >= config.ALERT_GRACE_SECONDS:
                        o["emailed_at"] = now
                        events.append({**base, "type": "email", "age": round(age)})
                    if o["escalated_at"] is None and age >= config.ESCALATION_SECONDS:
                        o["escalated_at"] = now
                        events.append({**base, "type": "escalation", "age": round(age)})
                if age > expire_after and o["escalated_at"] is not None:
                    del self.open[key]
                    events.append({**base, "type": "expired"})
        return events

    def forget(self, zone_id, gear):
        """Undo an open violation (used when saving it failed)."""
        with self.lock:
            self.open.pop((zone_id, gear), None)

    # ---------------- UI ----------------
    def status(self, zone_id, required, now=None):
        now = time.time() if now is None else now
        out = {}
        with self.lock:
            for gear in required:
                key = (zone_id, gear)
                o = self.open.get(key)
                ok_since = self.ok_since.get(key)
                s = {"miss_streak": self.miss.get(key, 0), "open": o is not None}
                if o:
                    age = self._active_age(o, now)
                    s.update(
                        paused=self._stale(zone_id, now),
                        voice_count=o["voice_count"],
                        emailed=o["emailed_at"] is not None,
                        email_in=max(0, round(config.ALERT_GRACE_SECONDS - age)),
                        escalated=o["escalated_at"] is not None,
                        escalation_in=max(0, round(config.ESCALATION_SECONDS - age)),
                        resolving=round(now - ok_since, 1) if ok_since is not None else None,
                        resolve_seconds=config.RESOLVE_SECONDS,
                    )
                out[gear] = s
        return out


tracker = Tracker()
