"""Dashboard numbers, computed from violation records (never by the LLM).

Definitions (shown to judges, keep them simple):
  fine exposure today  = sum of potential fines for today's violations still OPEN
  fines avoided today  = sum of potential fines for today's violations RESOLVED
                         (fixed within the 5-minute window)
  riskiest zone        = zone with the most violations in the last 7 days
                         (ties broken by open exposure)
"today" uses the viewer's local time zone (tz_offset from the browser).
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import config
from records import avoided, exposure, items_of


def _local(ts, offset):
    utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (utc - offset).replace(tzinfo=None)


def compute(items, tz_offset_minutes=0, now=None):
    offset = timedelta(minutes=tz_offset_minutes)  # JS getTimezoneOffset(): UTC - local
    now = (now or datetime.now(timezone.utc))
    local_now = (now - offset).replace(tzinfo=None)
    today = local_now.date()
    week_start = local_now - timedelta(days=7)

    rows = [(it, _local(it["timestamp"], offset)) for it in items]
    today_rows = [(it, t) for it, t in rows if t.date() == today]
    week_rows = [(it, t) for it, t in rows if t >= week_start]

    def total(rs, status):
        """OPEN -> exposure still standing; RESOLVED -> fines avoided (per item)."""
        return sum((exposure if status == "OPEN" else avoided)(it) for it, _ in rs)

    # Riskiest zone (7 days)
    zone_count = Counter(it["zone_name"] for it, _ in week_rows)
    zone_exposure = Counter()
    for it, _ in week_rows:
        zone_exposure[it["zone_name"]] += exposure(it)
    riskiest = None
    if zone_count:
        name = max(zone_count, key=lambda z: (zone_count[z], zone_exposure[z]))
        riskiest = {"zone": name, "violations": zone_count[name], "open_exposure": zone_exposure[name]}

    # Violations by local hour today
    by_hour = [0] * 24
    for _, t in today_rows:
        by_hour[t.hour] += 1

    # Violations by zone x gear (7 days)
    # AI-vision rules are grouped into one "AI_VISION" series (free-text rules vary per zone)
    by_zone = defaultdict(lambda: {g: 0 for g in config.GEAR_TYPES + ["AI_VISION"]})
    for it, _ in week_rows:
        for i in items_of(it):
            g = "AI_VISION" if config.is_ai(i["gear"]) else i["gear"]
            by_zone[it["zone_name"]][g] = by_zone[it["zone_name"]].get(g, 0) + 1

    fix_times = [int(it["seconds_to_fix"]) for it, _ in week_rows
                 if it["status"] == "RESOLVED" and it.get("seconds_to_fix") is not None]

    return {
        "today": {
            "violations": len(today_rows),
            "open": sum(1 for it, _ in today_rows if it["status"] == "OPEN"),
            "resolved": sum(1 for it, _ in today_rows if it["status"] == "RESOLVED"),
            "fine_exposure": total(today_rows, "OPEN"),
            "fines_avoided": total(today_rows, "RESOLVED"),
        },
        "week": {
            "violations": len(week_rows),
            "resolved": sum(1 for it, _ in week_rows if it["status"] == "RESOLVED"),
            "fine_exposure": total(week_rows, "OPEN"),
            "fines_avoided": total(week_rows, "RESOLVED"),
            "avg_seconds_to_fix": round(sum(fix_times) / len(fix_times)) if fix_times else None,
            "by_gear": dict(Counter(i["gear"] for it, _ in week_rows for i in items_of(it))),
        },
        "riskiest_zone": riskiest,
        "by_hour": by_hour,
        "by_zone": {z: by_zone[z] for z in sorted(by_zone)},
        "includes_sample": any(it.get("sample") for it, _ in week_rows),
    }


def dashboard(items, tz_offset_minutes=0, days=7, now=None):
    """Numbers for the Dashboard page over a range (7 / 30 / 90 days), plus the same
    numbers for the previous range so the UI can show trends. All computed here."""
    offset = timedelta(minutes=tz_offset_minutes)
    now = (now or datetime.now(timezone.utc))
    local_now = (now - offset).replace(tzinfo=None)
    start = (local_now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_start = start - timedelta(days=days)

    rows = [(it, _local(it["timestamp"], offset)) for it in items]
    cur = [(it, t) for it, t in rows if t >= start]
    prev = [(it, t) for it, t in rows if prev_start <= t < start]

    def summary(rs):
        n = len(rs)
        resolved = [it for it, _ in rs if it["status"] == "RESOLVED"]
        fixes = [int(it["seconds_to_fix"]) for it in resolved if it.get("seconds_to_fix") is not None]
        return {
            "violations": n,
            "open": sum(1 for it, _ in rs if it["status"] == "OPEN"),
            "resolved": len(resolved),
            "fine_exposure": sum(exposure(it) for it, _ in rs),
            "fines_avoided": sum(avoided(it) for it, _ in rs),
            "resolution_rate": round(100 * len(resolved) / n, 1) if n else None,
            "avg_seconds_to_fix": round(sum(fixes) / len(fixes)) if fixes else None,
        }

    by_hour = [0] * 24
    for _, t in cur:
        by_hour[t.hour] += 1

    zone_count = Counter(it["zone_name"] for it, _ in cur)
    issue_count = Counter((i.get("detector") or config.detector(i["gear"]), i.get("label") or config.gear_label(i["gear"]))
                          for it, _ in cur for i in items_of(it))

    daily = []
    for i in range(days):
        d = (start + timedelta(days=i)).date()
        day_rows = [it for it, t in cur if t.date() == d]
        daily.append({
            "date": d.isoformat(),
            "violations": len(day_rows),
            "exposure": sum(exposure(it) for it in day_rows),
            "avoided": sum(avoided(it) for it in day_rows),
        })

    return {
        "days": days,
        "start": start.date().isoformat(),
        "end": local_now.date().isoformat(),
        "current": summary(cur),
        "previous": summary(prev),
        "by_hour": by_hour,
        "by_zone": [{"zone": z, "violations": n} for z, n in zone_count.most_common()],
        "by_issue": [{"label": label, "detector": det, "violations": n}
                     for (det, label), n in issue_count.most_common()],
        "daily": daily,
        "includes_sample": any(it.get("sample") for it, _ in cur),
    }
