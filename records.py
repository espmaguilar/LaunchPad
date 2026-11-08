"""Incident record helpers. One incident = one event in a zone; it lists every item
that went missing while it was open (gloves + face cover -> one record, two items).

Record (DynamoDB / mock):
  zone_id, timestamp (id), zone_name, status OPEN|RESOLVED, fine (= sum of item fines),
  gear_type / gear_label / detector / osha_rule / severity  (first item, for old readers)
  items: [{gear, label, detector, rule, severity, fine, fine_source, status, logged_at,
           resolved_at?, fixed_at?, seconds_to_fix?, ai_reason?}]
  item status: OPEN | RESOLVED (fixed in the window: fine avoided) | FIXED_LATE (exposure stands)

Records written before items existed are read as a single item.
"""
import config


def items_of(rec):
    items = rec.get("items")
    if items:
        return items
    st = "RESOLVED" if rec.get("status") == "RESOLVED" else ("FIXED_LATE" if rec.get("fixed_at") else "OPEN")
    return [{"gear": rec["gear_type"], "label": rec.get("gear_label") or config.gear_label(rec["gear_type"]),
             "detector": rec.get("detector") or config.detector(rec["gear_type"]), "rule": rec.get("osha_rule", ""),
             "severity": rec.get("severity"), "fine": int(rec.get("fine", 0)), "fine_source": rec.get("fine_source"),
             "status": st, "seconds_to_fix": rec.get("seconds_to_fix"), "ai_reason": rec.get("ai_reason")}]


def exposure(rec):
    """Potential fine exposure still standing: items not fixed within the window."""
    return sum(int(i.get("fine", 0)) for i in items_of(rec) if i.get("status") != "RESOLVED")


def avoided(rec):
    """Fines avoided: items fixed within the window."""
    return sum(int(i.get("fine", 0)) for i in items_of(rec) if i.get("status") == "RESOLVED")


def labels(rec):
    return [i.get("label") or config.gear_label(i["gear"]) for i in items_of(rec)]


def summarize(items):
    """Top-level fields derived from the items list."""
    statuses = [i.get("status") for i in items]
    out = {"fine": sum(int(i.get("fine", 0)) for i in items),
           "status": "RESOLVED" if statuses and all(s == "RESOLVED" for s in statuses) else "OPEN"}
    if statuses and all(s in ("RESOLVED", "FIXED_LATE") for s in statuses):
        fixes = [int(i["seconds_to_fix"]) for i in items if i.get("seconds_to_fix") is not None]
        if fixes:
            out["seconds_to_fix"] = max(fixes)
        if any(s == "FIXED_LATE" for s in statuses):
            out["fixed_at"] = max(i.get("fixed_at") or i.get("resolved_at") or "" for i in items)
    return out
