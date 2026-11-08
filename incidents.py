"""Turn tracker events into side effects and UI events.

Frame events (from /analyze):   violation (incident + voice), resolved, fixed_late
Timed events (from tick thread): notify (one supervisor message per zone), expired

AWS failures never break /analyze; they come back as {"type": "error"} events.
Timed events are queued and handed to the browser on its next /analyze call.
"""
import json
import threading
import time
from collections import deque

from botocore.exceptions import BotoCoreError, ClientError

import config
import notify
import records
import reports
import store
from tracker import tracker, utc_timestamp

_ui_queue = deque(maxlen=50)
_ui_lock = threading.Lock()
_record_lock = threading.RLock()  # all read-modify-write of incident records (items list)


def _gear(g, zone=None):
    return config.gear_label(g, zone)


def _issue(g, zone):
    """'missing gloves' for Rekognition gear, 'AI vision: No phones' for AI rules."""
    return f"AI vision: {_gear(g, zone)}" if config.is_ai(g) else f"missing {_gear(g, zone)}"


def zone_meta(zone, people=1):
    """What the tracker keeps with an open incident (used later for notifications)."""
    return {"zone_name": zone["name"], "alert_email": zone.get("alert_email", ""), "rules": zone.get("rules"),
            "unmonitored": zone.get("unmonitored"), "people": people}


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


def _notify_text(zone, items, people):
    """One message for every missing item in the zone. Returns (subject, message, sms)."""
    rules = [(i, config.rule_for(zone, i["gear"])) for i in items]
    gears = "; ".join(_issue(i["gear"], zone) for i in items)
    total = sum(r["fine"] for _, r in rules)
    longest = max(i["age"] for i in items)
    subject = f"SafeSight: {zone['name']} - {gears}"
    lines = "\n".join(
        f"  - {_gear(i['gear'], zone)} [{config.detector(i['gear'])}]: {r['label']} ({r['severity']}), "
        f"potential fine exposure ${r['fine']:,}, voice warning at {i['timestamp']} UTC"
        for i, r in rules)
    message = (
        f"{zone['name']}: {gears} for {_duration(longest)}, "
        f"potential fine exposure ${total:,}.\n"
        f"A voice warning was played on site and it is still not fixed.\n\n"
        f"{lines}\n\n"
        f"Zone:        {zone['name']} ({zone['zone_id']})\n"
        f"People:      {people} without required gear\n"
        f"Supervisor:  {zone.get('alert_email') or '-'}\n\n"
        f"If it is fixed within {config.RESOLVE_WINDOW_SECONDS // 60} minutes of the voice warning it is "
        f"logged as a fine avoided.\n"
        f"Amounts are potential fine exposure based on OSHA maximum penalties, not an assessed fine."
    )
    sms = (f"SafeSight: {zone['name']} - {gears} for {_duration(longest)}. "
           f"Potential fine exposure ${total:,}. Voice warning ignored.")[:160]
    return subject, message, sms


def _base(ev, zone):
    gear = ev["gear"]
    return {"type": ev["type"], "gear": gear, "gear_label": _gear(gear, zone), "detector": config.detector(gear),
            "zone_id": zone["zone_id"], "zone_name": zone["name"], "timestamp": ev["timestamp"],
            "fine": config.rule_for(zone, gear)["fine"]}


# ---------------- frame events (request thread) ----------------
def _new_item(zone, gear, ai_results):
    rule = config.rule_for(zone, gear)
    item = {"gear": gear, "label": _gear(gear, zone), "detector": config.detector(gear), "rule": rule["label"],
            "severity": rule["severity"], "fine": rule["fine"], "fine_source": rule["source"], "status": "OPEN",
            "logged_at": utc_timestamp(time.time()), "notified": False}
    if config.is_ai(gear):
        r = (ai_results or {}).get(gear) or {}
        item["ai_reason"] = r.get("reason", "")
    return item


def _current_incident(zone_id, gear, now_ts):
    """The zone's open incident this violation belongs to: the one the tracker has open
    for another item, else a recent OPEN record (server restarted mid-incident)."""
    ts = tracker.group_timestamp(zone_id, exclude=gear)
    try:
        if ts:
            rec = store.get_violation(zone_id, ts)
            if rec and rec.get("status") == "OPEN":
                return rec
        since = utc_timestamp(_epoch(now_ts) - config.EXPIRE_SECONDS)
        rec = store.find_open(zone_id, since)
        return rec if rec and rec["timestamp"] != now_ts else None
    except (ClientError, BotoCoreError, OSError) as e:
        print(f"[incidents] open-incident lookup failed ({_error(e)}); logging a new one")
        return None


def _join(rec, zone, gear, ev, ai_results, base):
    """Add this violation to an existing incident. Returns a UI event, or None to log a new one."""
    items = [dict(i) for i in records.items_of(rec)]
    cur = next((i for i in items if i["gear"] == gear), None)
    if cur and cur.get("status") != "OPEN":
        return None  # this item was already fixed in that incident: it's a new event
    ts = rec["timestamp"]
    fields = {"voice_warnings": int(rec.get("voice_warnings", 1) or 1) + 1}
    if cur is None:  # another item went missing during the open incident -> same incident
        items.append(_new_item(zone, gear, ai_results))
        fields.update(items=items, report_status="PENDING", **records.summarize(items))
    err = _safe_update(zone["zone_id"], ts, **fields)
    if err:
        return {**base, "type": "error", "message": f"Could not update incident: {err}"}
    # Already notified? Per-item flag; records from before that flag fall back to the incident count.
    was_notified = bool(cur and (cur.get("notified") or ("notified" not in cur and int(rec.get("emails_sent", 0) or 0) > 0)))
    tracker.relink(zone["zone_id"], gear, ts, notified=was_notified, logged_at=_epoch(ts))
    if cur is None:
        reports.submit({**rec, **fields})  # rewrite the report with every item
    total = records.summarize(items)["fine"]
    return {**base, "timestamp": ts, "voice": True, "people": ev["people"], "missing_for": ev["missing_for"],
            "reattached": cur is not None, "joined": cur is None, "incident_fine": total}


def process(events, zone, image_bytes, persons, ai_results=None):
    out = []
    for ev in events:
        gear = ev["gear"]
        base = _base(ev, zone)

        if ev["type"] == "violation":
            with _record_lock:
                rec = _current_incident(zone["zone_id"], gear, ev["timestamp"])
                joined = _join(rec, zone, gear, ev, ai_results, base) if rec else None
                if joined:
                    out.append(joined)
                    continue
                item = _new_item(zone, gear, ai_results)
                record = {
                    "zone_id": zone["zone_id"],
                    "timestamp": ev["timestamp"],
                    "zone_name": zone["name"],
                    # first item, kept at top level for older readers / sorting
                    "gear_type": gear,
                    "gear_label": item["label"],
                    "detector": item["detector"],
                    "osha_rule": item["rule"],
                    "severity": item["severity"],
                    "fine_source": item["fine_source"],
                    "items": [item],
                    **records.summarize([item]),
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
                if config.is_ai(gear):
                    record["ai_reason"] = item.get("ai_reason", "")
                    record["ai_confidence"] = str(((ai_results or {}).get(gear) or {}).get("confidence", ""))
                try:
                    store.save_violation(record, image_bytes)
                except (ClientError, BotoCoreError, OSError) as e:
                    tracker.forget(zone["zone_id"], gear)  # retry on the next streak
                    out.append({**base, "type": "error", "message": f"Could not save violation: {_error(e)}"})
                    continue
            reports.submit(record)
            out.append({**base, "voice": True, "people": ev["people"], "missing_for": ev["missing_for"],
                        "incident_fine": record["fine"]})

        elif ev["type"] in ("resolved", "fixed_late"):
            err = _mark_item(zone["zone_id"], ev["timestamp"], gear,
                             "RESOLVED" if ev["type"] == "resolved" else "FIXED_LATE", ev["seconds_to_fix"])
            if err:
                out.append({**base, "type": "error", "message": f"Could not mark resolved: {err}"})
                continue
            out.append({**base, "seconds_to_fix": ev["seconds_to_fix"], "notified": ev["notified"]})
    return out


def _mark_item(zone_id, ts, gear, status, seconds_to_fix, by=None):
    """Set one item's outcome; the incident is RESOLVED once every item is."""
    with _record_lock:
        try:
            rec = store.get_violation(zone_id, ts)
        except (ClientError, BotoCoreError, OSError) as e:
            return _error(e)
        if rec is None:
            return "incident not found"
        now = utc_timestamp(time.time())
        items = [dict(i) for i in records.items_of(rec)]
        for i in items:
            if i["gear"] == gear or gear is None:
                if gear is None and i.get("status") != "OPEN":
                    continue  # manual resolve: leave items that already have an outcome
                i["status"] = status
                i["resolved_at" if status == "RESOLVED" else "fixed_at"] = now
                if seconds_to_fix is not None:
                    i["seconds_to_fix"] = seconds_to_fix
        fields = {"items": items, **records.summarize(items)}
        if fields["status"] == "RESOLVED":
            fields["resolved_at"] = now
            if by:
                fields["resolved_by"] = by
        return _safe_update(zone_id, ts, **fields)


def resolve_all(zone_id, ts, by="manager"):
    """Manager closes the whole incident from the Incidents page."""
    return _mark_item(zone_id, ts, None, "RESOLVED", None, by=by)


def _epoch(ts):
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


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
                "alert_email": meta.get("alert_email", ""), "rules": meta.get("rules"),
                "unmonitored": meta.get("unmonitored")}

        if ev["type"] == "notify":
            items = ev["items"]
            subject, message, sms = _notify_text(zone, items, meta.get("people", 1))
            out = {"type": "notify", "zone_id": zone["zone_id"], "zone_name": zone["name"],
                   "gears": [i["gear"] for i in items], "gear_label": ", ".join(_gear(i["gear"], zone) for i in items),
                   "fine": sum(config.rule_for(zone, i["gear"])["fine"] for i in items),
                   "age": max(i["age"] for i in items), "timestamp": items[0]["timestamp"]}
            try:
                out["channel"] = notify.send(subject, message, sms)
                out["sent"] = True
            except (ClientError, BotoCoreError, OSError) as e:
                out["sent"] = False
                out["alert_error"] = _error(e)
            for ts in dict.fromkeys(i["timestamp"] for i in items):  # one update per incident
                gears = {i["gear"] for i in items if i["timestamp"] == ts}
                with _record_lock:
                    try:
                        rec = store.get_violation(zone["zone_id"], ts)
                    except (ClientError, BotoCoreError, OSError):
                        rec = None
                    if rec is None:
                        continue
                    its = [dict(x, notified=True) if x["gear"] in gears else dict(x) for x in records.items_of(rec)]
                    _safe_update(zone["zone_id"], ts, items=its, emails_sent=int(rec.get("emails_sent", 0) or 0) + 1,
                                 emailed_at=utc_timestamp(time.time()))
            _push_ui(out)
        else:  # expired
            _push_ui(_base(ev, zone))


# ---------------- AI-vision results (vision thread) ----------------
def on_vision_result(zone, results, image_bytes, parsed):
    """Feed an AI-vision check into the same tracker -> incident pipeline."""
    events = tracker.update_ai(zone["zone_id"], results, meta=zone_meta(zone))
    for ev in process(events, zone, image_bytes, [], ai_results=parsed):
        _push_ui(ev)


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
            print(f"[notify] tick failed: {e}")


def start_ticker():
    """Start the notification thread once per process (called from FastAPI startup)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="safesight-notify", daemon=True)
    _thread.start()


def stop_ticker():
    _stop.set()
