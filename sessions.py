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
import re
import secrets
import threading
import time

import config
import breakdown
import matcher
import scribe
from ingest import utc_timestamp

_sessions = {}
_lock = threading.Lock()
MAX_LINES = 500
MAX_SNAPSHOTS = 80      # pinned evidence frames per session
MAX_LINE_FRAMES = 40    # rolling recent frames (one per transcript line)


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
        "coach_tip": None,      # paper-step hint {step, text, time}
        "step_since": now,      # when the current step became current (stuck detection)
        "confused": {},         # step number -> "offered" | "declined" | "accepted"
        "breakdown": None,      # {step, items: [{instruction, expected_action, done, done_elapsed, evidence}], current, since}
        "events": [],           # pending UI events (voice), handed out with each response
        "snapshots": {},        # pinned evidence frames: id -> jpeg (in memory until Phase D uploads to S3)
        "frames": {},           # rolling recent frames, one per transcript line
        "last_frame": None,
        "last_frame_id": None,
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
HIDDEN = ("busy", "t0", "last_activity", "step_since", "frames", "last_frame_id", "transcript", "snapshots", "last_frame", "events", "flagged",
          "done_streak", "skip_streak", "mistake_streak")


def public(s, since=0, events=None):
    """JSON-safe view. since = transcript lines the client already has."""
    out = {k: v for k, v in s.items() if k not in HIDDEN}
    out["focus_terms"] = _focus_terms(s)
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
def _append(s, line, now, frame_id=None):
    t = s["transcript"]
    entry = {"time": utc_timestamp(now), "elapsed": round(now - s["t0"], 1), **line, "repeat": 1, "frame_id": frame_id}
    if t and t[-1]["action"] == line["action"]:
        if not t[-1].get("frame_id"):
            t[-1]["frame_id"] = frame_id
        t[-1].update(repeat=t[-1]["repeat"] + 1, last_time=entry["time"], last_elapsed=entry["elapsed"],
                     objects=line["objects"] or t[-1]["objects"])
        return t[-1], False
    t.append(entry)
    if len(t) > MAX_LINES:
        del t[: len(t) - MAX_LINES]
    return entry, True


def _window(s, now):
    """Transcript lines seen AFTER the current step started (at most MATCH_WINDOW s back).
    Lines from the previous step must never count as evidence for the new one."""
    st = s["steps_state"][s["current"]]
    since = max(now - s["t0"] - config.MATCH_WINDOW_SECONDS, st["started_elapsed"] or 0)
    first = st["started_elapsed"] or 0
    return [l for l in s["transcript"]
            if l.get("last_elapsed", l["elapsed"]) > since and (l["elapsed"] > first or l.get("last_elapsed", 0) > first)]


def _store_frame(s, frame):
    """Keep a rolling set of recent frames (one per transcript line). Returns its id."""
    if not frame:
        return None
    fid = secrets.token_hex(4)
    s["frames"][fid] = frame
    while len(s["frames"]) > MAX_LINE_FRAMES:
        s["frames"].pop(next(iter(s["frames"])))
    return fid


def _pin(s, fid):
    """Copy a frame into the permanent snapshots (step evidence, feedback) so it isn't rotated out."""
    if fid and fid in s["frames"]:
        s["snapshots"][fid] = s["frames"][fid]
        if len(s["snapshots"]) > MAX_SNAPSHOTS:
            s["snapshots"].pop(next(iter(s["snapshots"])))
        return fid
    return None


def _evidence_frame(s, evidence, lines):
    """Frame of the transcript line that best matches the evidence text; else the latest line's frame."""
    ev = (evidence or "").lower().strip(" .\"'")
    for l in reversed(lines):
        a = l["action"].lower().strip(" .")
        if ev and (a in ev or ev in a) and l.get("frame_id"):
            return l["frame_id"]
    for l in reversed(lines):
        if l.get("frame_id"):
            return l["frame_id"]
    return s["last_frame_id"]


def _snapshot(s, evidence="", lines=None):
    return _pin(s, _evidence_frame(s, evidence, lines if lines is not None else s["transcript"][-3:]))


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
    window = _window(s, now) or s["transcript"][-1:]
    st.update(status="done", done_at=utc_timestamp(now), done_elapsed=round(now - s["t0"], 1),
              evidence=evidence, method=method, snapshot_id=_snapshot(s, evidence, window))
    s["current"] = i + 1
    s["step_since"], s["coach_tip"], s["breakdown"] = now, None, None
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
                        })
    del s["checks"][: -config.CHECK_HISTORY]
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
                  "evidence": r["evidence"], "snapshot_id": _snapshot(s, r["evidence"], _window(s, now)),
                  "dismissed": False, "corrected": None}
            s["feedback"].append(fb)
            _say(s, "mistake", matcher.voice_for_mistake(r), feedback_id=fb["id"])
    else:
        s["mistake_streak"] = {"type": None, "count": 0}

    # moving on
    if config.ADVANCE_MODE == "instant" and not config.LENIENT_ADVANCE:
        qualifies = (not r["mistake"] and r["status"] in ("done", "in_progress", "next_step_detected")
                     and r["confidence"] >= config.DONE_MIN_CONFIDENCE)
        hit = _key_object_line(steps[s["current"]], _window(s, now)) if qualifies else None
        s["checks"][-1]["confirm"] = (f"key object seen: {hit}" if hit else
                                      "no line names the step's key object" if qualifies else None)
        if hit:
            _complete_step(s, step_no, now, {**r, "evidence": r["evidence"] or hit})
        return
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
                "evidence": r["evidence"], "snapshot_id": _snapshot(s, r["evidence"], _window(s, now)),
                "dismissed": False, "corrected": None})
            _say(s, "possible_skip", f"Quick check — did you finish step {step_no}? "
                                     f"It looks like you started step {nxt['number']}.")
    else:
        s["skip_streak"] = 0

    # careful mode: "done" (not just in progress), confident, no mistake, at least MIN_STEP_SECONDS on the step,
    # confirmed by a strict look at the frame - on DONE_CHECKS checks in a row (the finished state has to hold)
    # ("in_progress" >= IN_PROGRESS_MIN_CONFIDENCE also goes to the vision check: compound steps like "unscrew the
    #  cap and set it down" stay in_progress in the matcher even when the main action is clearly happening)
    candidate = not r["mistake"] and ((r["status"] == "done" and r["confidence"] >= config.DONE_MIN_CONFIDENCE) or
                                      (r["status"] == "in_progress" and r["confidence"] >= config.IN_PROGRESS_MIN_CONFIDENCE
                                       and _can_confirm(s)))  # only when the frame really gets checked
    if candidate:
        on_step = now - s["t0"] - (s["steps_state"][s["current"]]["started_elapsed"] or 0)
        if on_step < config.MIN_STEP_SECONDS and not config.MOCK_MODE:  # (the mock demo script has its own timing)
            return  # too early to judge: don't count it, don't reset either
        ok, why = _confirm_finished(s, steps[s["current"]], now)
        s["checks"][-1]["confirm"] = why
        if not ok:
            s["done_streak"] = 0
            return
        s["done_streak"] += 1
        if s["done_streak"] >= config.DONE_CHECKS:
            _complete_step(s, step_no, now, r)
    else:
        s["done_streak"] = 0


_GENERIC = {"table", "bench", "workspace", "camera", "hand", "hands", "area", "surface", "desk", "center", "centre",
            "student", "front", "side", "top", "bottom", "lab", "space", "view", "work", "thing", "item", "materials",
            # body parts: "raising hands to head" must never count as "headphones on"
            "head", "ear", "ears", "face", "arm", "arms", "finger", "fingers", "eye", "eyes", "body", "back", "both"}
_ALIASES = {"iphone": {"phone", "smartphone", "cellphone"}, "smartphone": {"phone"}, "specimen": {"stuffed", "animal", "toy"},
            "reagent": {"bottle"}, "beaker": {"cup", "glass"}, "headphone": {"headset", "earphone"}}


def _key_words(step):
    """Words that name the step's key objects: key_terms objects + visual cues + aliases in parentheses,
    without generic scenery words. "Place the specimen (stuffed animal)..." -> {"stuffed", "animal"}."""
    text = " ".join(scribe.key_terms(step)["objects"] + list(step.get("visual_cues") or [])
                    + re.findall(r"\(([^)]*)\)", str(step.get("instruction") or "")))
    words = {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"[a-z]+", text.lower())}
    words = {w for w in words if len(w) > 2 and w not in _GENERIC and w not in scribe._COLORS}
    return words.union(*(_ALIASES.get(w, set()) for w in words))


def _key_object_line(step, lines):
    """The newest transcript line that names one of the step's key objects (None if there is none)."""
    keys = _key_words(step)
    for l in reversed(lines):
        words = {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"[a-z]+", l["action"].lower())}
        words |= {w[:-1] if w.endswith("s") and len(w) > 4 else w for o in l.get("objects") or [] for w in re.findall(r"[a-z]+", o.lower())}
        if keys & words:
            return l["action"]
    return None


def _can_confirm(s):
    return not config.MOCK_MODE and config.CONFIRM_WITH_VISION and bool(s.get("last_frame"))


def _confirm_finished(s, step, now):
    """Strict vision gate before a step completes. Returns (ok, short reason for the check history)."""
    if not _can_confirm(s):
        return True, "not checked"
    try:
        done, conf, evidence = matcher.finished_check(s["last_frame"], step, _window(s, now))
    except Exception as e:  # a failed check must never tick a step
        return False, f"error: {str(e)[:60]}"
    ok = done and conf >= config.FINISH_MIN_CONFIDENCE
    return ok, f"{'yes' if ok else 'no'} {conf:.2f}: {evidence}"


def _complete_step(s, step_no, now, r):
    # mistakes flagged on this step count as corrected once the step is completed
    for fb in s["feedback"]:
        if fb["step"] == step_no and fb["corrected"] is None:
            fb["corrected"] = True
    _advance(s, now, r["evidence"] or (s["transcript"][-1]["action"] if s["transcript"] else ""), "auto")


def _focus_terms(s):
    """What the camera should watch right now: the current sub-step (step-by-step mode) or the current step."""
    steps = s["procedure"]["steps"]
    if s["current"] >= len(steps):
        return {"action": "", "objects": []}
    step = steps[s["current"]]
    b = s.get("breakdown")
    if b and b["current"] < len(b["items"]):
        sub = scribe.key_terms({"instruction": b["items"][b["current"]]["instruction"]})
        whole = scribe.key_terms(step)
        return {"action": sub["action"], "objects": list(dict.fromkeys(sub["objects"] + whole["objects"]))[:5]}
    return scribe.key_terms(step)


def scribe_frames(sid, frames, now=None, motion=""):
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
        frame_id = None
        if frames:
            s["last_frame"] = frames[-1]
            frame_id = s["last_frame_id"] = _store_frame(s, frames[-1])
        tag = None
        if config.MOCK_MODE:
            line, tag = scribe.mock_line(tick)
            line = {**line, "model": "mock", "ms": 0}
        else:
            try:
                line = scribe.describe(frames, motion, _focus_terms(s))
            except Exception as e:  # never break the session on one bad frame
                s["errors"] += 1
                s["last_error"] = str(e)[:200]
                return {"error": s["last_error"], "state": public(s, len(s["transcript"]), _drain(s))}
        s["last_error"] = None
        entry, new = _append(s, line, now, frame_id)

        cur_step = s["procedure"]["steps"][s["current"]] if s["current"] < len(s["procedure"]["steps"]) else None
        if cur_step is not None and _is_paper(cur_step):
            # paper/screen step: the camera can't judge it - wait for "Mark step done"
            s["coach_tip"] = {"step": cur_step["number"], "text": PAPER_TIP, "time": utc_timestamp(now)}
        elif cur_step is not None and s["breakdown"] and s["breakdown"]["step"] == cur_step["number"]:
            # confused student: check the current sub-step instead of the whole step
            try:
                _check_substep(s, cur_step, now, tag)
            except Exception as e:
                s["errors"] += 1
                s["last_error"] = f"substep: {str(e)[:180]}"
        elif cur_step is not None and not config.MOCK_MODE and not _window(s, now):
            _maybe_offer_help(s, now)  # nothing new seen since this step started: nothing to judge yet
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
            _maybe_offer_help(s, now)
        return {"line": entry, "new_line": new, "state": public(s, len(s["transcript"]), _drain(s))}
    finally:
        s["busy"].release()


# ---------------- "Feeling confused?" ----------------
def _maybe_offer_help(s, now):
    """Still on the same camera step after CONFUSED_AFTER_SECONDS -> offer a breakdown (once per step)."""
    if s["current"] >= len(s["procedure"]["steps"]):
        return
    step = s["procedure"]["steps"][s["current"]]
    if _is_paper(step) or step["number"] in s["confused"]:
        return
    if now - s["step_since"] >= config.CONFUSED_AFTER_SECONDS:
        s["confused"][step["number"]] = "offered"
        s["events"].append({"type": "confused_offer", "voice": "", "step": step["number"]})


def answer_confused(sid, yes, now=None):
    """Student answered the pop-up. No -> never ask again for this step. Yes -> sub-steps."""
    s = get(sid)
    with s["busy"]:
        now = time.time() if now is None else now
        if s["current"] >= len(s["procedure"]["steps"]):
            raise SessionError("every step is already done")
        step = s["procedure"]["steps"][s["current"]]
        if not yes:
            s["confused"][step["number"]] = "declined"
            return public(s, len(s["transcript"]), _drain(s))
        items = breakdown.make_substeps(step, _window(s, now))
        s["confused"][step["number"]] = "accepted"
        s["breakdown"] = {"step": step["number"], "current": 0, "since": round(now - s["t0"], 1),
                          "items": [{**it, "done": False, "done_elapsed": None, "evidence": ""} for it in items]}
        _say(s, "breakdown", f"No problem — let's take step {step['number']} one small piece at a time. "
                             f"First: {items[0]['instruction']}")
        return public(s, len(s["transcript"]), _drain(s))


def _substep_window(s, now):
    b = s["breakdown"]
    since = max(now - s["t0"] - config.MATCH_WINDOW_SECONDS, b["since"] - 2)
    return [l for l in s["transcript"] if l.get("last_elapsed", l["elapsed"]) >= since]


def _tick_substep(s, step, now, evidence, how="auto"):
    b = s["breakdown"]
    it = b["items"][b["current"]]
    it.update(done=True, done_elapsed=round(now - s["t0"], 1), evidence=evidence, method=how)
    b["current"] += 1
    b["since"] = round(now - s["t0"], 1)
    if b["current"] >= len(b["items"]):
        _complete_step(s, step["number"], now, {"evidence": "All sub-steps done"})
        return
    _say(s, "substep_done", f"Nice. Next: {b['items'][b['current']]['instruction']}")


def _check_substep(s, step, now, tag=None):
    b = s["breakdown"]
    it = b["items"][b["current"]]
    ev = ""
    if config.MOCK_MODE:
        seen, conf = True, 0.9  # mock: one sub-step per tick
    else:
        # vision check on the latest frame (+ recent scribe lines and the parent step for object aliases)
        seen, conf, ev = matcher.substep_check(s["last_frame"], it, step, _substep_window(s, now))
    s["last_check"] = {"status": "done" if seen else "in_progress", "confidence": round(conf, 2), "evidence": ev,
                       "mistake": False, "mistake_type": "", "what_went_wrong": "", "how_to_fix": "",
                       "step": step["number"], "time": utc_timestamp(now), "substep": b["current"] + 1}
    s["checks"].append({"elapsed": round(now - s["t0"], 1), "step": step["number"], "substep": b["current"] + 1,
                        "status": "done" if seen else "in_progress", "confidence": round(conf, 2), "mistake": False,
                        "evidence": ev})
    del s["checks"][: -config.CHECK_HISTORY]
    if seen and conf >= config.SUBSTEP_MIN_CONFIDENCE:
        if s["last_frame_id"]:
            it["snapshot_id"] = _pin(s, s["last_frame_id"])
        _tick_substep(s, step, now, ev or (s["transcript"][-1]["action"] if s["transcript"] else ""))


def exit_breakdown(sid):
    """Leave step-by-step mode: back to normal tracking of the whole step (no new pop-up for it)."""
    s = get(sid)
    with s["busy"]:
        if s["breakdown"]:
            s["breakdown"] = None
            s["step_since"] = time.time()
        return public(s, len(s["transcript"]), _drain(s))


def mark_substep_done(sid, now=None):
    """Manual override for the current sub-step."""
    s = get(sid)
    with s["busy"]:
        if not s["breakdown"]:
            raise SessionError("no sub-steps right now")
        step = s["procedure"]["steps"][s["current"]]
        _tick_substep(s, step, time.time() if now is None else now, "Marked done by the student", "manual")
        return public(s, len(s["transcript"]), _drain(s))


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
        s["step_since"], s["coach_tip"], s["breakdown"] = now, None, None
        s["done_streak"] = s["skip_streak"] = 0
        s["mistake_streak"] = {"type": None, "count": 0}
        step = s["procedure"]["steps"][s["current"]]
        _say(s, "undo", f"Okay, back to step {step['number']}: {step['instruction']}")
        return public(s, len(s["transcript"]), _drain(s))


def restart_step(sid, now=None):
    """Start the current step over: earlier camera lines no longer count, streaks/breakdown reset, and the
    'Feeling confused?' offer and mistake warnings for this step can come back."""
    s = get(sid)
    with s["busy"]:
        if s["current"] >= len(s["steps_state"]):
            raise SessionError("all steps are done - use Undo to re-open the last one")
        now = time.time() if now is None else now
        step = s["procedure"]["steps"][s["current"]]
        s["steps_state"][s["current"]]["started_elapsed"] = round(now - s["t0"], 1)
        s["step_since"], s["coach_tip"], s["breakdown"] = now, None, None
        s["done_streak"] = s["skip_streak"] = 0
        s["mistake_streak"] = {"type": None, "count": 0}
        s["confused"].pop(step["number"], None)
        s["flagged"] = [k for k in s["flagged"] if k[0] != step["number"]]
        _say(s, "restart", f"No problem, let's start step {step['number']} again: {step['instruction']}")
        return public(s, len(s["transcript"]), _drain(s))


def dismiss(sid, feedback_id):
    """'Got it' on a coach card. Also exits the step-by-step breakdown if one is open."""
    s = get(sid)
    for fb in s["feedback"]:
        if fb["id"] == feedback_id:
            fb["dismissed"] = True
            if s["breakdown"]:
                s["breakdown"] = None
                s["step_since"] = time.time()
            return public(s, len(s["transcript"]), [])
    raise SessionError("feedback not found")


def snapshot(sid, snap_id):
    s = get(sid)
    return s["snapshots"].get(snap_id) or s["frames"].get(snap_id)


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
