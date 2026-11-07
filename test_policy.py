"""Offline checks for policy ingestion. Bedrock is stubbed - no AWS.
Run: python test_policy.py"""
import json
import sys
import types

import config
import policy

# 1. Gear mapping (camera types vs not camera-monitored)
cases = {
    "Chemical-resistant nitrile gloves": "HAND_COVER",
    "Cut-resistant gloves (ANSI A4)": "HAND_COVER",
    "Half-face respirator or N95 mask": "FACE_COVER",
    "Surgical face mask": "FACE_COVER",
    "Hard hat (ANSI Z89.1)": "HEAD_COVER",
    "Safety helmet": "HEAD_COVER",
    "Head cover (bouffant cap)": "HEAD_COVER",
    "Indirect-vent chemical splash goggles": "UNMONITORED",
    "Safety glasses with side shields": "UNMONITORED",
    "Face shield": "UNMONITORED",          # not a mask: must not map to FACE_COVER
    "High-visibility safety vest": "UNMONITORED",
    "Steel-toe safety boots": "UNMONITORED",
}
for item, want in cases.items():
    assert policy.map_gear(item) == want, (item, policy.map_gear(item))

# 2. Bedrock stub: first reply is broken JSON, second is valid -> one retry succeeds
RAW = {"zones": [
    {"name": "Chem Lab", "supervisor_email": "chem@x.example", "gear": [
        {"item": "nitrile gloves", "rule_reference": "Sec. 4.1(a), 29 CFR 1910.138", "severity": "Serious", "fine_usd": 18500},
        {"item": "N95 mask", "rule_reference": None, "severity": None, "fine_usd": None},
        {"item": "splash goggles", "rule_reference": "Sec. 4.1(c)", "severity": "Serious", "fine_usd": "$16,550"},
    ]},
    {"name": "Loading Dock", "supervisor_email": None, "gear": [
        {"item": "hard hat", "rule_reference": "Sec. 4.4(a)", "severity": "Serious", "fine_usd": None},
        {"item": "hi-vis vest", "rule_reference": "Sec. 4.4(b)", "severity": "Other-than-serious", "fine_usd": 4000},
    ]},
]}
replies = ["Sure! Here is the data: {zones: [broken", "```json\n" + json.dumps(RAW) + "\n```"]
calls = []


def fake_converse(**kw):
    calls.append(kw)
    return {"output": {"message": {"content": [{"text": replies[len(calls) - 1]}]}}}


sys.modules["aws_clients"] = types.SimpleNamespace(bedrock=types.SimpleNamespace(converse=fake_converse))
config.MOCK_MODE = False
config.BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-5-5"
raw, source = policy.ai_extract("policy text")
assert len(calls) == 2 and "retry" in source, source
assert calls[1]["messages"][-1]["role"] == "user"            # retry asks again; no assistant prefill
assert all("temperature" not in c["inferenceConfig"] for c in calls)

# 2b. Two bad replies -> clear error (caller falls back to manual entry)
calls.clear()
replies[:] = ["nope", "still nope"]
try:
    policy.ai_extract("policy text")
    raise AssertionError("expected PolicyError")
except policy.PolicyError as e:
    assert "after a retry" in str(e)

# 3. Draft: OSHA defaults filled + labelled; policy fines kept; unmonitored kept
config.ALERT_EMAIL = "fallback@x.example"
d = policy.to_draft(raw)
chem = {g["item"]: g for g in d[0]["gear"]}
assert chem["nitrile gloves"]["fine"] == 18500 and chem["nitrile gloves"]["fine_source"] == "policy"
assert chem["N95 mask"]["gear_type"] == "FACE_COVER"
assert chem["N95 mask"]["fine"] == 16550 and chem["N95 mask"]["fine_source"] == "OSHA default"
assert chem["N95 mask"]["rule"].startswith("29 CFR 1910.134")
assert chem["splash goggles"]["gear_type"] == "UNMONITORED" and chem["splash goggles"]["fine"] == 16550
assert d[1]["alert_email"] == "fallback@x.example" and d[1]["zone_id"] == "loading-dock"

# 4. Approve: zone records with per-zone rules; unmonitored kept separately
zones = policy.validate_approved(d)
cz = zones[0]
assert cz["zone_id"] == "chem-lab" and cz["required"] == ["HAND_COVER", "FACE_COVER"]
assert cz["rules"]["HAND_COVER"]["fine"] == 18500
assert [u["item"] for u in cz["unmonitored"]] == ["splash goggles"]
r = config.rule_for(cz, "HAND_COVER")
assert r["fine"] == 18500 and r["label"].startswith("Sec. 4.1(a)") and r["source"] == "policy"
assert config.rule_for(cz, "HEAD_COVER")["fine"] == 16550     # not in policy -> OSHA table
assert config.rule_for(None, "HEAD_COVER")["source"] == "OSHA default"

# 5. Validation errors are readable
for bad, msg in [([], "at least one zone"),
                 ([{"name": "", "gear": []}], "needs a name"),
                 ([{"name": "A", "gear": []}, {"name": "a", "gear": []}], "both named"),
                 ([{"name": "A", "gear": [{"item": "gloves", "gear_type": "HAND_COVER", "fine": -5}]}], "between"),
                 ([{"name": "A", "gear": [{"item": "x", "gear_type": "GOGGLES"}]}], "unknown gear type")]:
    try:
        policy.validate_approved(bad)
        raise AssertionError(f"expected error: {msg}")
    except policy.PolicyError as e:
        assert msg in str(e), (msg, str(e))

# 6. Sample PDF is readable by the pypdf fallback and contains every zone
from pathlib import Path
pdf = (Path(__file__).parent / "demo" / "sample_safety_policy.pdf").read_bytes()
text = policy._pypdf(pdf)
for z in ("Chemistry Laboratory", "Fabrication Shop", "Clean Room", "Loading Dock", "$18,500", "goggles"):
    assert z in text, z

print("all policy checks passed")
