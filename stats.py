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
        return sum(int(it["fine"]) for it, _ in rs if it["status"] == status)

    # Riskiest zone (7 days)
    zone_count = Counter(it["zone_name"] for it, _ in week_rows)
    zone_exposure = Counter()
    for it, _ in week_rows:
        if it["status"] == "OPEN":
            zone_exposure[it["zone_name"]] += int(it["fine"])
    riskiest = None
    if zone_count:
        name = max(zone_count, key=lambda z: (zone_count[z], zone_exposure[z]))
        riskiest = {"zone": name, "violations": zone_count[name], "open_exposure": zone_exposure[name]}

    # Violations by local hour today
    by_hour = [0] * 24
    for _, t in today_rows:
        by_hour[t.hour] += 1

    # Violations by zone x gear (7 days)
    by_zone = defaultdict(lambda: {g: 0 for g in config.GEAR_TYPES})
    for it, _ in week_rows:
        by_zone[it["zone_name"]][it["gear_type"]] += 1

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
            "by_gear": dict(Counter(it["gear_type"] for it, _ in week_rows)),
        },
        "riskiest_zone": riskiest,
        "by_hour": by_hour,
        "by_zone": {z: by_zone[z] for z in sorted(by_zone)},
        "includes_sample": any(it.get("sample") for it, _ in week_rows),
    }
