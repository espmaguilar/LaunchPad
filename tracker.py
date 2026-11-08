"""Smart logic: time-based streaks, voice warning, one supervisor notification,
hysteresis. Pure in-memory, no AWS.

Timeline for one (zone, gear type), counted from the first missing frame:
   0s                    someone is missing the gear (streak starts)
  VOICE_AFTER  (20s)     still missing -> log the incident + VOICE warning. No message.
  NOTIFY_AFTER (30s)     still OPEN    -> ONE supervisor notification (email now, SMS later).
  => at most one notification per incident. Other open incidents in the same zone
     ride along in that same notification, so 2 missing items = 1 message, not 2.

Frame rules (update(), called per camera frame):
  someone missing it            -> streak continues (or starts); compliance broken
  people seen, all wearing it   -> streak cleared; compliance timer starts
  no evidence (nobody visible,
  or body part hidden)          -> neutral, unless no miss for MISS_GAP_SECONDS -> streak cleared

Notifications are held while a fix is being confirmed (compliance timer running).
Stale zone: no frames for ZONE_STALE_SECONDS (camera stopped / switched zones)
  -> timers PAUSE (paused time doesn't count) and resume when frames return.

Resolve (hysteresis): gear visibly worn and no miss for RESOLVE_SECONDS.
  fixed within RESOLVE_WINDOW_SECONDS of logging -> RESOLVED, fine avoided
  fixed later                                    -> fixed_late (exposure stands)
Expire: unresolved after EXPIRE_SECONDS -> stop tracking; it stays OPEN and a
  new streak can log a new incident.

Thread safety: update() (request threads), tick() (background thread) and
status() all take the same lock.
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
        self.miss_since = {}  # key -> start of the current missing streak (None = no streak)
        self.last_miss = {}   # key -> time of the latest missing frame
        self.ok_since = {}    # key -> start of continuous compliance (None = not compliant)
        self.open = {}        # key -> dict, see _new_open
        self.last_frame = {}  # zone_id -> time of last frame
        self.ai_hits = {}     # key -> consecutive positive AI-vision checks

    @staticmethod
    def _new_open(ts, now, streak_start, meta):
        return {"timestamp": ts, "logged_at": now, "streak_start": streak_start,
                "notified_at": None, "meta": meta or {},
                "paused_total": 0.0, "paused_since": None}

    @staticmethod
    def _active_age(o, now):
        """Seconds since the streak started, excluding time the zone's camera was offline."""
        paused = o["paused_total"] + ((now - o["paused_since"]) if o["paused_since"] is not None else 0)
        return now - o["streak_start"] - paused

    def _stale(self, zone_id, now):
        return now - self.last_frame.get(zone_id, now) > config.ZONE_STALE_SECONDS

    def _resolving(self, key, now):
        # Hold notifications while a fix is being confirmed - but not forever
        # (e.g. compliant once, then walked out of frame).
        ok_since = self.ok_since.get(key)
        return ok_since is not None and now - ok_since <= config.RESOLVE_SECONDS + 5

    def _maybe_resolve(self, key, opened, compliant_now, now, events):
        """Hysteresis: resolve only after RESOLVE_SECONDS of continuous compliance."""
        ok_since = self.ok_since.get(key)
        if compliant_now and ok_since is not None and now - ok_since >= config.RESOLVE_SECONDS:
            del self.open[key]
            fix_secs = max(0, round(ok_since - opened["logged_at"]))
            in_window = ok_since - opened["logged_at"] <= config.RESOLVE_WINDOW_SECONDS
            events.append({
                "type": "resolved" if in_window else "fixed_late",
                "gear": key[1],
                "timestamp": opened["timestamp"],
                "seconds_to_fix": fix_secs,
                "notified": opened["notified_at"] is not None,
            })

    def _resume(self, zone_id, now):
        """Frames are back: resume any paused timers in this zone."""
        self.last_frame[zone_id] = now
        for (z, _), o in self.open.items():
            if z == zone_id and o["paused_since"] is not None:
                o["paused_total"] += now - o["paused_since"]
                o["paused_since"] = None

    # ---------------- per frame ----------------
    def update(self, zone_id, required, persons, now=None, meta=None):
        now = time.time() if now is None else now
        events = []
        with self.lock:
            self._resume(zone_id, now)

            for gear in required:
                key = (zone_id, gear)
                missing_now = any(gear in p["missing"] for p in persons)
                worn_now = not missing_now and any(gear in p["worn"] for p in persons)
                gap = now - self.last_miss.get(key, now) > config.MISS_GAP_SECONDS

                if missing_now:
                    if self.miss_since.get(key) is None or gap:
                        self.miss_since[key] = now
                    self.last_miss[key] = now
                    self.ok_since[key] = None
                elif worn_now:
                    self.miss_since[key] = None
                    if self.ok_since.get(key) is None:
                        self.ok_since[key] = now
                elif gap:
                    self.miss_since[key] = None

                opened = self.open.get(key)
                streak = self.miss_since.get(key)

                # Tier 1: missing continuously for VOICE_AFTER_SECONDS -> incident + voice
                if opened is None:
                    if missing_now and streak is not None and now - streak >= config.VOICE_AFTER_SECONDS:
                        ts = utc_timestamp(now)
                        self.open[key] = self._new_open(ts, now, streak, meta)
                        self.ok_since[key] = None
                        events.append({"type": "violation", "gear": gear, "timestamp": ts, "voice": True,
                                       "missing_for": round(now - streak),
                                       "people": sum(gear in p["missing"] for p in persons)})
                    continue

                self._maybe_resolve(key, opened, worn_now, now, events)
        return events

    # ---------------- AI-vision checks (every AI_CHECK_SECONDS) ----------------
    def update_ai(self, zone_id, results, now=None, meta=None):
        """results: {rule_key: True (violated) | False (complied) | None (can't tell)}.

        Checks are ~10 s apart, so the debounce counts CHECKS, not seconds:
        AI_DEBOUNCE_CHECKS consecutive positives -> incident + voice. From there it's
        the same pipeline as Rekognition: notify at NOTIFY_AFTER_SECONDS from the first
        positive, hysteresis resolve, stale-zone pause, expiry."""
        now = time.time() if now is None else now
        events = []
        with self.lock:
            for rule_key, violated in results.items():
                key = (zone_id, rule_key)
                if violated is True:
                    self.ai_hits[key] = self.ai_hits.get(key, 0) + 1
                    if self.miss_since.get(key) is None:
                        self.miss_since[key] = now
                    self.last_miss[key] = now
                    self.ok_since[key] = None
                elif violated is False:
                    self.ai_hits[key] = 0
                    self.miss_since[key] = None
                    if self.ok_since.get(key) is None:
                        self.ok_since[key] = now
                # None: unsure -> neutral

                opened = self.open.get(key)
                if opened is None:
                    if violated is True and self.ai_hits[key] >= config.AI_DEBOUNCE_CHECKS:
                        ts = utc_timestamp(now)
                        self.open[key] = self._new_open(ts, now, self.miss_since[key], meta)
                        self.ok_since[key] = None
                        events.append({"type": "violation", "gear": rule_key, "timestamp": ts, "voice": True,
                                       "missing_for": round(now - self.miss_since[key]), "people": 1})
                    continue
                self._maybe_resolve(key, opened, violated is False, now, events)
        return events

    # ---------------- time-based (background thread) ----------------
    def tick(self, now=None):
        """Supervisor notification + expiry for every open incident in every zone.

        Returns at most one "notify" event per zone per tick, listing every open,
        not-yet-notified, not-resolving incident in that zone."""
        now = time.time() if now is None else now
        events = []
        with self.lock:
            due_zones = set()
            for key, o in list(self.open.items()):
                zone_id, gear = key
                if self._stale(zone_id, now):
                    if o["paused_since"] is None:
                        o["paused_since"] = now
                    continue  # camera offline: timers frozen, nothing fires
                age = self._active_age(o, now)
                if (o["notified_at"] is None and age >= config.NOTIFY_AFTER_SECONDS
                        and not self._resolving(key, now)):
                    due_zones.add(zone_id)
                if age > config.EXPIRE_SECONDS:
                    del self.open[key]
                    if self.miss_since.get(key) is not None:
                        self.miss_since[key] = now  # still missing: a fresh 20 s / 30 s cycle
                    self.ai_hits[key] = 0
                    events.append({"type": "expired", "zone_id": zone_id, "gear": gear,
                                   "timestamp": o["timestamp"], "meta": o["meta"]})

            for zone_id in sorted(due_zones):
                items = []
                for (z, gear), o in self.open.items():
                    if z != zone_id or o["notified_at"] is not None or self._resolving((z, gear), now):
                        continue
                    o["notified_at"] = now
                    items.append({"gear": gear, "timestamp": o["timestamp"],
                                  "age": round(self._active_age(o, now)), "meta": o["meta"]})
                if items:
                    items.sort(key=lambda i: -i["age"])
                    events.append({"type": "notify", "zone_id": zone_id, "items": items,
                                   "meta": items[0]["meta"]})
        return events

    def group_timestamp(self, zone_id, exclude=None):
        """Record id of the incident currently open in this zone (via any other open item)."""
        with self.lock:
            for (z, g), o in self.open.items():
                if z == zone_id and g != exclude:
                    return o["timestamp"]
        return None

    def relink(self, zone_id, gear, timestamp, notified, logged_at):
        """Point the open incident at an existing record (found in the database after a
        restart). Keeps its timestamp and, if it was already notified, never notifies again."""
        with self.lock:
            o = self.open.get((zone_id, gear))
            if o is None:
                return
            o["timestamp"] = timestamp
            o["logged_at"] = min(o["logged_at"], logged_at)
            if notified and o["notified_at"] is None:
                o["notified_at"] = o["logged_at"]

    def forget(self, zone_id, gear, restart_clock=False, now=None):
        """Drop an open violation. Used when saving it failed (retry on the next streak),
        and when a manager resolves it by hand: restart_clock=True starts a fresh
        voice/notify cycle so a worker who is still non-compliant isn't re-logged instantly."""
        now = time.time() if now is None else now
        with self.lock:
            self.open.pop((zone_id, gear), None)
            if restart_clock:
                key = (zone_id, gear)
                self.ai_hits[key] = 0
                if self.miss_since.get(key) is not None:
                    self.miss_since[key] = now

    # ---------------- UI ----------------
    def status(self, zone_id, required, now=None):
        now = time.time() if now is None else now
        out = {}
        with self.lock:
            for gear in required:
                key = (zone_id, gear)
                o = self.open.get(key)
                streak = self.miss_since.get(key)
                ok_since = self.ok_since.get(key)
                s = {"open": o is not None,
                     "detector": config.detector(gear),
                     "missing_for": round(now - streak) if streak is not None and o is None else 0,
                     "voice_after": config.VOICE_AFTER_SECONDS,
                     "notify_after": config.NOTIFY_AFTER_SECONDS}
                if config.is_ai(gear):
                    s.update(checks=self.ai_hits.get(key, 0), checks_needed=config.AI_DEBOUNCE_CHECKS)
                if o:
                    age = self._active_age(o, now)
                    s.update(
                        paused=self._stale(zone_id, now),
                        missing_for=round(age),
                        notified=o["notified_at"] is not None,
                        notify_in=max(0, round(config.NOTIFY_AFTER_SECONDS - age)),
                        resolving=round(now - ok_since, 1) if ok_since is not None else None,
                        resolve_seconds=config.RESOLVE_SECONDS,
                    )
                out[gear] = s
        return out


tracker = Tracker()
