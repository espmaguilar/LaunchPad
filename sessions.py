"""Lab sessions: one run of a procedure - transcript, step progress, feedback.

Per scribe tick (under the session's lock):
  1. Scribe: latest frames -> one transcript line (Phase B)
  2. Matcher: transcript since the current step started (max MATCH_WINDOW s) -> status/mistake
  3. State machine:
     - "done" with confidence >= MATCH_MIN_CONFIDENCE on DONE_CHECKS consecutive checks -> advance
       ("Step N complete. Next: ..." spoken)
     - "next_step_detected" on SKIP_CHECKS consecutive checks while the current step isn't done
       -> flag "Possible skipped step" (once per step)
     - mistake of the same type on MISTAKE_CHECKS consecutive checks -> feedback item with snapshot
       + friendly spoken correction (once per step + type)
  Manual override: mark the current step done / undo the last step.

No backlog: the lock is acquired without waiting; if the previous tick is still running the new
frames are skipped. Sessions live in memory (Phase C); Phase D/E persist them.
"""
import secrets
import threading
import time

import config
import matcher
import scribe
from ingest import utc_timestamp

_sessions = {}
_lock = threading.Lock()
MAX_LINES = 500
MAX_SNAPSHOTS = 60


class SessionError(Exception):
    pass


def start(procedure):
    sid = secrets.token_hex(6)
    steps = procedure["steps"]
    now = time.time()
    s = {
        "session_id": sid,
        "procedure_id": procedure["procedure_id"],
        "procedure": {"title": procedure["title"], "steps": steps},
        "started_at": utc_timestamp(now),
        "t0": now,
        "ended_at": None,
        "completed_at": None,
        "transcript": [],
        "ticks": 0,
        "skipped": 0,
        "errors": 0,
        "last_error": None,
        # progress
        "current": 0,
        "steps_state": [{"number": st["number"], "status": "current" if i == 0 else "upcoming",
                         "started_elapsed": 0.0 if i == 0 else None, "done_at": None, "done_elapsed": None,
                         "evidence": "", "method": None, "snapshot_id": None}
                        for i, st in enumerate(steps)],
        "done_streak": 0,
        "skip_streak": 0,
        "mistake_streak": {"type": None, "count": 0},
        "flagged": [],          # [(step_number, kind)] already reported (no repeats)
        "feedback": [],         # mistakes + possible skips shown in the Feedback panel
        "last_check": None,     # latest matcher result (for the current-step card)
        "checks": [],           # recent matcher results (debugging, lab record)
        "coach_tip": None,      # latest coaching suggestion {step, text, time}
        "step_since": now,      # when the current step became current (stuck detection)
        "tip_spoken_at": None,
        "events": [],           # pending UI events (voice), handed out with each response
        "snapshots": {},        # id -> jpeg bytes (in memory until Phase D uploads to S3)
        "last_frame": None,
        "last_activity": now,   # last scribe tick / manual action (abandoned tabs stop ticking)
        "busy": threading.Lock(),
    }
    if steps and _is_paper(steps[0]):
        s["coach_tip"] = {"step": steps[0]["number"], "text": PAPER_TIP, "time": utc_timestamp(now)}
    with _lock:
        _sessions[sid] = s
    return s


def get(sid):
    s = _sessions.get(sid)
    if s is None:
        raise SessionError("session not found (the server may have restarted) - start a new session")
    return s


ACTIVE_SECONDS = 30  # a session with no tick for this long is shown as idle (tab closed/reloaded)
HIDDEN = ("busy", "t0", "last_activity", "step_since", "tip_spoken_at", "transcript", "snapshots", "last_frame", "events", "flagged",
          "done_streak", "skip_streak", "mistake_streak")


def public(s, since=0, events=None):
    """JSON-safe view. since = transcript lines the client already has."""
    out = {k: v for k, v in s.items() if k not in HIDDEN}
    out.update(
        transcript=s["transcript"][since:] if since <= len(s["transcript"]) else s["transcript"],
        line_count=len(s["transcript"]),
        elapsed=round(time.time() - s["t0"]),
        complete=s["current"] >= len(s["procedure"]["steps"]),
        active=not s["ended_at"] and time.time() - s["last_activity"] < ACTIVE_SECONDS,
        streaks={"done": s["done_streak"], "skip": s["skip_streak"], "mistake": s["mistake_streak"]["count"]},
    )
    if events is not None:
        out["events"] = events
    return out


# ---------------- transcript ----------------
def _append(s, line, now):
    t = s["transcript"]
    entry = {"time": utc_timestamp(now), "elapsed": round(now - s["t0"], 1), **line, "repeat": 1}
    if t and t[-1]["action"] == line["action"]:
        t[-1].update(repeat=t[-1]["repeat"] + 1, last_time=entry["time"], last_elapsed=entry["elapsed"],
                     objects=line["objects"] or t[-1]["objects"])
        return t[-1], False
    t.append(entry)
    if len(t) > MAX_LINES:
        del t[: len(t) - MAX_LINES]
    return entry, True


def _window(s, now):
    """Transcript since the current step started (minus a little slack), at most MATCH_WINDOW s."""
    st = s["steps_state"][s["current"]]
    since = max(now - s["t0"] - config.MATCH_WINDOW_SECONDS, (st["started_elapsed"] or 0) - 3)
    return [l for l in s["transcript"] if l.get("last_elapsed", l["elapsed"]) >= since]


def _snapshot(s):
    if not s["last_frame"]:
        return None
    sid = secrets.token_hex(4)
    s["snapshots"][sid] = s["last_frame"]
    if len(s["snapshots"]) > MAX_SNAPSHOTS:
        s["snapshots"].pop(next(iter(s["snapshots"])))
    return sid


PAPER_TIP = "This step is done on paper or a screen, so the camera can't check it. Tap Mark step done when you're finished."


def _is_paper(step):
    return step.get("camera_checkable") is False


def _say(s, kind, text, **extra):
    s["events"].append({"type": kind, "voice": text, **extra})


# ---------------- state machine ----------------
def _advance(s, now, evidence, method):
    steps = s["procedure"]["steps"]
    i = s["current"]
    st = s["steps_state"][i]
    st.update(status="done", done_at=utc_timestamp(now), done_elapsed=round(now - s["t0"], 1),
              evidence=evidence, method=method, snapshot_id=_snapshot(s))
    s["current"] = i + 1
    s["step_since"], s["tip_spoken_at"], s["coach_tip"] = now, None, None
    s["done_streak"] = s["skip_streak"] = 0
    s["mistake_streak"] = {"type": None, "count": 0}
    if s["current"] < len(steps):
        nxt = s["steps_state"][s["current"]]
        nxt.update(status="current", started_elapsed=round(now - s["t0"], 1))
        nstep = steps[s["current"]]
        paper = " This one's on paper: tap Mark step done when you're finished." if _is_paper(nstep) else ""
        if _is_paper(nstep):
            s["coach_tip"] = {"step": nstep["number"], "text": PAPER_TIP, "time": utc_timestamp(now)}
        _say(s, "step_done", f"Step {steps[i]['number']} complete. Next: {nstep['instruction']}{paper}",
             step=steps[i]["number"])
    else:
        s["completed_at"] = utc_timestamp(now)
        _say(s, "complete", f"Step {steps[i]['number']} complete. That's every step — nice work! "
                            "You can end the session to see your lab record.", step=steps[i]["number"])


def apply_check(s, r, now):
    """Feed one matcher result into the state machine."""
    steps = s["procedure"]["steps"]
    if s["current"] >= len(steps):
        return
    step_no = steps[s["current"]]["number"]
    s["last_check"] = {**r, "step": step_no, "time": utc_timestamp(now)}
    s["checks"].append({"elapsed": round(now - s["t0"], 1), "step": step_no, "status": r["status"],
                        "confidence": r["confidence"], "mistake": r["mistake"], "evidence": r["evidence"],
                        "tip": r.get("tip", "")})
    del s["checks"][: -config.CHECK_HISTORY]
    if r.get("tip"):
        s["coach_tip"] = {"step": step_no, "text": r["tip"], "time": utc_timestamp(now)}
        # stuck on this step for a while -> say the tip out loud (not too often)
        stuck = now - s["step_since"] >= config.STUCK_SECONDS
        quiet = s["tip_spoken_at"] is None or now - s["tip_spoken_at"] >= config.TIP_REPEAT_SECONDS
        if stuck and quiet and not r["mistake"]:
            s["tip_spoken_at"] = now
            _say(s, "tip", r["tip"])
    confident = r["confidence"] >= config.MATCH_MIN_CONFIDENCE

    # mistakes: same type twice in a row, confident, once per step + type
    if r["mistake"] and confident:
        ms = s["mistake_streak"]
        ms["count"] = ms["count"] + 1 if ms["type"] == r["mistake_type"] else 1
        ms["type"] = r["mistake_type"]
        key = [step_no, f"mistake:{r['mistake_type']}"]
        if ms["count"] >= config.MISTAKE_CHECKS and key not in s["flagged"]:
            s["flagged"].append(key)
            fb = {"id": secrets.token_hex(4), "kind": "mistake", "step": step_no, "time": utc_timestamp(now),
                  "elapsed": round(now - s["t0"], 1), "mistake_type": r["mistake_type"],
                  "what_went_wrong": r["what_went_wrong"], "how_to_fix": r["how_to_fix"],
                  "evidence": r["evidence"], "snapshot_id": _snapshot(s), "dismissed": False, "corrected": None}
            s["feedback"].append(fb)
            _say(s, "mistake", matcher.voice_for_mistake(r), feedback_id=fb["id"])
    else:
        s["mistake_streak"] = {"type": None, "count": 0}

    # moving on
    if config.LENIENT_ADVANCE:
        # Anything resembling the step (or already doing the next one) counts - but never while a
        # mistake is being reported for this check.
        qualifies = (not r["mistake"] and r["status"] in ("done", "in_progress", "next_step_detected")
                     and r["confidence"] >= config.DONE_MIN_CONFIDENCE)
        s["done_streak"] = s["done_streak"] + 1 if qualifies else 0
        if qualifies and s["done_streak"] >= config.DONE_CHECKS:
            _complete_step(s, step_no, now, r)
        return

    # strict mode: possible skipped step
    if r["status"] == "next_step_detected" and confident:
        s["skip_streak"] += 1
        key = [step_no, "skip"]
        if s["skip_streak"] >= config.SKIP_CHECKS and key not in s["flagged"] and s["current"] + 1 < len(steps):
            s["flagged"].append(key)
            nxt = steps[s["current"] + 1]
            s["feedback"].append({
                "id": secrets.token_hex(4), "kind": "possible_skip", "step": step_no, "time": utc_timestamp(now),
                "elapsed": round(now - s["t0"], 1), "mistake_type": "incomplete",
                "what_went_wrong": f"It looks like you moved on to step {nxt['number']} before finishing step {step_no}.",
                "how_to_fix": f"Step {step_no}: {steps[s['current']]['instruction']}",
                "evidence": r["evidence"], "snapshot_id": _snapshot(s), "dismissed": False, "corrected": None})
            _say(s, "possible_skip", f"Quick check — did you finish step {step_no}? "
                                     f"It looks like you started step {nxt['number']}.")
    else:
        s["skip_streak"] = 0

    # strict mode: advance on "done" twice in a row, confident
    if r["status"] == "done" and confident:
        s["done_streak"] += 1
        if s["done_streak"] >= config.DONE_CHECKS:
            _complete_step(s, step_no, now, r)
    else:
        s["done_streak"] = 0


def _complete_step(s, step_no, now, r):
    # mistakes flagged on this step count as corrected once the step is completed
    for fb in s["feedback"]:
        if fb["step"] == step_no and fb["corrected"] is None:
            fb["corrected"] = True
    _advance(s, now, r["evidence"] or (s["transcript"][-1]["action"] if s["transcript"] else ""), "auto")


def scribe_frames(sid, frames, now=None):
    """Scribe + matcher for one tick. Returns {line, new_line, state} or {skipped: True}."""
    s = get(sid)
    if s["ended_at"]:
        raise SessionError("session has ended")
    if not s["busy"].acquire(blocking=False):  # previous tick still running -> skip, no backlog
        s["skipped"] += 1
        return {"skipped": True}
    try:
        now = time.time() if now is None else now
        s["last_activity"] = time.time()
        tick = s["ticks"]
        s["ticks"] += 1
        if frames:
            s["last_frame"] = frames[-1]
        tag = None
        if config.MOCK_MODE:
            line, tag = scribe.mock_line(tick)
            line = {**line, "model": "mock", "ms": 0}
        else:
            try:
                line = scribe.describe(frames)
            except Exception as e:  # never break the session on one bad frame
                s["errors"] += 1
                s["last_error"] = str(e)[:200]
                return {"error": s["last_error"], "state": public(s, len(s["transcript"]), _drain(s))}
        s["last_error"] = None
        entry, new = _append(s, line, now)

        cur_step = s["procedure"]["steps"][s["current"]] if s["current"] < len(s["procedure"]["steps"]) else None
        if cur_step is not None and _is_paper(cur_step):
            # paper/screen step: the camera can't judge it - wait for "Mark step done"
            s["coach_tip"] = {"step": cur_step["number"], "text": PAPER_TIP, "time": utc_timestamp(now)}
        elif cur_step is not None:
            try:
                if config.MOCK_MODE:
                    r = matcher.mock_check(s["procedure"]["steps"], s["current"], tag, line)
                else:
                    r, _ = matcher.check(s["procedure"]["steps"], s["current"], _window(s, now))
                apply_check(s, r, now)
            except Exception as e:
                s["errors"] += 1
                s["last_error"] = f"matcher: {str(e)[:180]}"
        return {"line": entry, "new_line": new, "state": public(s, len(s["transcript"]), _drain(s))}
    finally:
        s["busy"].release()


def _drain(s):
    ev, s["events"] = s["events"], []
    return ev


# ---------------- manual controls ----------------
def mark_done(sid, now=None):
    s = get(sid)
    with s["busy"]:
        if s["current"] >= len(s["procedure"]["steps"]):
            raise SessionError("every step is already done")
        _advance(s, time.time() if now is None else now, "Marked done by the student", "manual")
        return public(s, len(s["transcript"]), _drain(s))


def undo(sid, now=None):
    """Re-open the most recent done step (it becomes current again)."""
    s = get(sid)
    with s["busy"]:
        if s["current"] == 0:
            raise SessionError("nothing to undo")
        now = time.time() if now is None else now
        if s["current"] < len(s["steps_state"]):
            s["steps_state"][s["current"]].update(status="upcoming", started_elapsed=None)
        s["current"] -= 1
        s["steps_state"][s["current"]].update(status="current", done_at=None, done_elapsed=None, evidence="",
                                              method=None, snapshot_id=None, started_elapsed=round(now - s["t0"], 1))
        s["completed_at"] = None
        s["step_since"], s["tip_spoken_at"], s["coach_tip"] = now, None, None
        s["done_streak"] = s["skip_streak"] = 0
        s["mistake_streak"] = {"type": None, "count": 0}
        step = s["procedure"]["steps"][s["current"]]
        _say(s, "undo", f"Okay, back to step {step['number']}: {step['instruction']}")
        return public(s, len(s["transcript"]), _drain(s))


def dismiss(sid, feedback_id):
    s = get(sid)
    for fb in s["feedback"]:
        if fb["id"] == feedback_id:
            fb["dismissed"] = True
            return public(s, len(s["transcript"]), [])
    raise SessionError("feedback not found")


def snapshot(sid, snap_id):
    return get(sid)["snapshots"].get(snap_id)


def list_all():
    """Every session in memory, newest first (Lab Records + Instructor View)."""
    return sorted(_sessions.values(), key=lambda s: s["t0"], reverse=True)


def end(sid):
    s = get(sid)
    s["ended_at"] = s["ended_at"] or utc_timestamp()
    for fb in s["feedback"]:  # mistakes never followed by a properly completed step stay uncorrected
        if fb["corrected"] is None:
            fb["corrected"] = False
    return s
