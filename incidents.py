"""Turn tracker events into side effects and UI events.

Frame events (from /analyze):   violation, voice_repeat, resolved, fixed_late
Timed events (from tick thread): email (tier 2), escalation (tier 3), expired

AWS failures never break /analyze; they come back as {"type": "error"} events.
Timed events are queued and handed to the browser on its next /analyze call.
"""
import json
import threading
import time
from collections import deque

from botocore.exceptions import BotoCoreError, ClientError

import config
import reports
import store
from tracker import tracker, utc_timestamp

_ui_queue = deque(maxlen=50)
_ui_lock = threading.Lock()


def _gear(g):
    return config.GEAR_LABELS.get(g, g)


def _duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    return f"{minutes} min" if minutes != 1 else "1 min"


def _error(e):
    if isinstance(e, ClientError):
        return f"{e.response['Error']['Code']}: {e.response['Error'].get('Message', '')}"
    return str(e)


def _email_text(tier, zone, gear, ts, age, people):
    rule = config.rule_for(zone, gear)
    if tier == "escalation":
        subject = f"ESCALATION: {zone['name']} - missing {_gear(gear)} UNRESOLVED for {_duration(age)}"
        opener = (f"ESCALATION: {zone['name']}: missing {_gear(gear)} has been UNRESOLVED for {_duration(age)} "
                  f"despite a voice warning and a supervisor email.")
    else:
        subject = f"SafeSight: {zone['name']} - missing {_gear(gear)}"
        opener = (f"{zone['name']}: missing {_gear(gear)}, potential fine exposure ${rule['fine']:,}. "
                  f"The worker was warned by voice {_duration(age)} ago and it is still not fixed.")
    message = (
        f"{opener}\n\n"
        f"Rule:        {rule['label']} ({rule['severity']})\n"
        f"Potential fine exposure: ${rule['fine']:,}\n"
        f"Zone:        {zone['name']} ({zone['zone_id']})\n"
        f"First seen (UTC): {ts}\n"
        f"People:      {people} without {_gear(gear)}\n"
        f"Supervisor:  {zone.get('alert_email') or '-'}\n\n"
        f"If it is fixed within {config.RESOLVE_WINDOW_SECONDS // 60} minutes of first detection it is logged "
        f"as a fine avoided.\n"
        f"Amounts are potential fine exposure based on OSHA maximum penalties, not an assessed fine."
    )
    return subject, message


def _base(ev, zone_name, zone_id, zone_rules=None):
    gear = ev["gear"]
    return {"type": ev["type"], "gear": gear, "gear_label": _gear(gear), "zone_id": zone_id,
            "zone_name": zone_name, "timestamp": ev["timestamp"],
            "fine": config.rule_for({"rules": zone_rules}, gear)["fine"]}


# ---------------- frame events (request thread) ----------------
def process(events, zone, image_bytes, persons):
    out = []
    for ev in events:
        gear = ev["gear"]
        rule = config.rule_for(zone, gear)
        base = _base(ev, zone["name"], zone["zone_id"], zone.get("rules"))

        if ev["type"] == "violation":
            item = {
                "zone_id": zone["zone_id"],
                "timestamp": ev["timestamp"],
                "zone_name": zone["name"],
                "gear_type": gear,
                "osha_rule": rule["label"],
                "severity": rule["severity"],
                "fine": rule["fine"],
                "fine_source": rule["source"],
                "status": "OPEN",
                "image_key": store.image_key(zone["zone_id"], ev["timestamp"]),
                "report": "",
                "report_status": "PENDING",
                "people": ev["people"],
                # Boxes so the incident view can redraw them on the photo (JSON string: no floats in DynamoDB)
                "detections": json.dumps([{"box": p["box"], "status": p["status"]} for p in persons]),
                "voice_warnings": 1,
                "emails_sent": 0,
                "sample": False,
            }
            try:
                store.save_violation(item, image_bytes)
            except (ClientError, BotoCoreError, OSError) as e:
                tracker.forget(zone["zone_id"], gear)  # retry on the next streak
                out.append({**base, "type": "error", "message": f"Could not save violation: {_error(e)}"})
                continue
            reports.submit(item)
            out.append({**base, "voice": True, "people": ev["people"]})

        elif ev["type"] == "voice_repeat":
            _safe_update(zone["zone_id"], ev["timestamp"], voice_warnings=config.VOICE_MAX_PLAYS)
            out.append({**base, "voice": True})

        elif ev["type"] in ("resolved", "fixed_late"):
            fields = {"resolved_at" if ev["type"] == "resolved" else "fixed_at": utc_timestamp(time.time()),
                      "seconds_to_fix": ev["seconds_to_fix"]}
            if ev["type"] == "resolved":
                fields["status"] = "RESOLVED"  # fixed late -> stays OPEN, exposure stands
            err = _safe_update(zone["zone_id"], ev["timestamp"], **fields)
            if err:
                out.append({**base, "type": "error", "message": f"Could not mark resolved: {err}"})
                continue
            out.append({**base, "seconds_to_fix": ev["seconds_to_fix"], "emails_sent": ev["emails_sent"]})
    return out


def _safe_update(zone_id, ts, **fields):
    try:
        store.update_violation(zone_id, ts, **fields)
        return None
    except (ClientError, BotoCoreError, OSError) as e:
        print(f"[incidents] update failed for {zone_id} {ts}: {_error(e)}")
        return _error(e)


# ---------------- timed events (background thread) ----------------
def process_timed(events):
    for ev in events:
        meta = ev.get("meta", {})
        zone = {"zone_id": ev["zone_id"], "name": meta.get("zone_name", ev["zone_id"]),
                "alert_email": meta.get("alert_email", ""), "rules": meta.get("rules")}
        base = _base(ev, zone["name"], zone["zone_id"], zone["rules"])

        if ev["type"] in ("email", "escalation"):
            subject, message = _email_text(ev["type"], zone, ev["gear"], ev["timestamp"], ev["age"],
                                           meta.get("people", 1))
            try:
                store.send_alert(subject, message)
                base["sent"] = True
            except (ClientError, BotoCoreError, OSError) as e:
                base["sent"] = False
                base["alert_error"] = _error(e)
            n = 1 if ev["type"] == "email" else 2
            field = "emailed_at" if ev["type"] == "email" else "escalated_at"
            _safe_update(zone["zone_id"], ev["timestamp"], emails_sent=n, **{field: utc_timestamp(time.time())})
            base["age"] = ev["age"]
        _push_ui(base)


def _push_ui(ev):
    with _ui_lock:
        _ui_queue.append(ev)


def drain_ui():
    """Timed events for the browser (handed out once)."""
    with _ui_lock:
        out = list(_ui_queue)
        _ui_queue.clear()
    return out


# ---------------- background ticker ----------------
_thread = None
_stop = threading.Event()


def _run():
    while not _stop.wait(config.TICK_SECONDS):
        try:
            events = tracker.tick()
            if events:
                process_timed(events)
        except Exception as e:  # keep the loop alive no matter what
            print(f"[escalation] tick failed: {e}")


def start_ticker():
    """Start the escalation thread once per process (called from FastAPI startup)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="safesight-escalation", daemon=True)
    _thread.start()


def stop_ticker():
    _stop.set()
