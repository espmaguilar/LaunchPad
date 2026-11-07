"""Offline checks for dashboard stats + weekly template. Run: python test_stats.py"""
from datetime import datetime, timezone

import config
import reports
import stats

config.MOCK_MODE = True
NOW = datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc)  # 12:00 PST
PST = 480  # JS getTimezoneOffset() for UTC-8


def v(ts, zone, gear, status, fix=None, sample=False):
    it = {"timestamp": ts, "zone_name": zone, "gear_type": gear, "status": status,
          "fine": 16550, "sample": sample}
    if fix is not None:
        it["seconds_to_fix"] = fix
    return it


items = [
    v("2026-11-07T19:30:00.000Z", "Chem Lab", "HAND_COVER", "OPEN"),           # today 11:30
    v("2026-11-07T18:10:00.000Z", "Chem Lab", "FACE_COVER", "RESOLVED", 40),   # today 10:10
    v("2026-11-07T17:05:00.000Z", "Clean Room", "HEAD_COVER", "RESOLVED", 20), # today 09:05
    v("2026-11-07T05:00:00.000Z", "Clean Room", "HAND_COVER", "OPEN"),         # UTC today, but Nov 6 21:00 PST
    v("2026-11-03T12:00:00.000Z", "Clean Room", "FACE_COVER", "OPEN", sample=True),
    v("2026-10-20T12:00:00.000Z", "Construction Site", "HEAD_COVER", "OPEN"),  # > 7 days ago
]

st = stats.compute(items, PST, now=NOW)
t = st["today"]
assert t["violations"] == 3, t                       # Nov 6 21:00 PST is NOT today
assert t["fine_exposure"] == 16550 and t["fines_avoided"] == 33100, t
assert st["by_hour"][11] == 1 and st["by_hour"][10] == 1 and st["by_hour"][9] == 1 and sum(st["by_hour"]) == 3
assert st["week"]["violations"] == 5, st["week"]     # excludes Oct 20
assert st["riskiest_zone"]["zone"] == "Clean Room" and st["riskiest_zone"]["violations"] == 3
assert st["week"]["avg_seconds_to_fix"] == 30
assert st["by_zone"]["Chem Lab"] == {"FACE_COVER": 1, "HAND_COVER": 1, "HEAD_COVER": 0}
assert "Construction Site" not in st["by_zone"]
assert st["includes_sample"] is True

# Same data in UTC: the 05:00Z record now counts as today
assert stats.compute(items, 0, now=NOW)["today"]["violations"] == 4

# Empty data doesn't crash
empty = stats.compute([], PST, now=NOW)
assert empty["riskiest_zone"] is None and empty["today"]["fine_exposure"] == 0

# Weekly report (template path in mock mode) uses only computed numbers
r = reports.weekly_report(st)
assert len(r["recommendations"]) == 3 and "Clean Room" in " ".join(r["highlights"]), r
assert "$33,100" in r["summary"] and "will be fined" not in str(r).lower(), r["summary"]
assert reports.weekly_report(empty)["summary"].startswith("No PPE violations")

print("all stats checks passed")
