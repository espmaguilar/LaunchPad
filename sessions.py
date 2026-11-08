"""Lab sessions: one run of a procedure, with its live Scribe transcript.

Kept in memory (Phase B). Phase D/E persist sessions, deviations and the lab record.

No backlog: each session has a lock that is acquired WITHOUT waiting. If the previous
scribe call is still running when new frames arrive, the frames are skipped - so the
transcript always describes "now", never a queue of stale frames.

Consecutive identical actions are merged into one line (repeat count + last time), so
"No visible action." doesn't flood the transcript.
"""
import secrets
import threading
import time

import config
import scribe
from ingest import utc_timestamp

_sessions = {}
_lock = threading.Lock()
MAX_LINES = 500


class SessionError(Exception):
    pass


def start(procedure):
    sid = secrets.token_hex(6)
    s = {
        "session_id": sid,
        "procedure_id": procedure["procedure_id"],
        "procedure": {"title": procedure["title"], "steps": procedure["steps"]},
        "started_at": utc_timestamp(),
        "t0": time.time(),
        "ended_at": None,
        "transcript": [],
        "ticks": 0,
        "skipped": 0,
        "errors": 0,
        "last_error": None,
        "busy": threading.Lock(),
    }
    with _lock:
        _sessions[sid] = s
    return s


def get(sid):
    s = _sessions.get(sid)
    if s is None:
        raise SessionError("session not found (the server may have restarted) - start a new session")
    return s


def public(s, since=0):
    """JSON-safe view. since = number of transcript lines the client already has."""
    return {k: v for k, v in s.items() if k not in ("busy", "t0", "transcript")} | {
        "transcript": s["transcript"][since:] if since <= len(s["transcript"]) else s["transcript"],
        "line_count": len(s["transcript"]),
        "elapsed": round(time.time() - s["t0"]),
    }


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


def scribe_frames(sid, frames, now=None):
    """Run the scribe on the latest frames. Returns {line, new_line} or {skipped: True}."""
    s = get(sid)
    if s["ended_at"]:
        raise SessionError("session has ended")
    if not s["busy"].acquire(blocking=False):  # previous call still running -> skip, no backlog
        s["skipped"] += 1
        return {"skipped": True}
    try:
        tick = s["ticks"]
        s["ticks"] += 1
        if config.MOCK_MODE:
            line = {**scribe.mock_line(tick), "model": "mock", "ms": 0}
        else:
            try:
                line = scribe.describe(frames)
            except Exception as e:  # never break the session on one bad frame
                s["errors"] += 1
                s["last_error"] = str(e)[:200]
                return {"error": s["last_error"]}
        s["last_error"] = None
        entry, new = _append(s, line, time.time() if now is None else now)
        return {"line": entry, "new_line": new}
    finally:
        s["busy"].release()


def end(sid):
    s = get(sid)
    s["ended_at"] = s["ended_at"] or utc_timestamp()
    return s
