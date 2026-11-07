"""Offline checks for incident reports. Bedrock is stubbed - no AWS.
Run: python test_reports.py"""
import json
import sys
import types

import config
import reports

ITEM = {
    "zone_id": "chem-lab", "zone_name": "Chem Lab", "timestamp": "2026-11-07T20:00:00.000Z",
    "gear_type": "HAND_COVER", "fine": 16550, "people": 1,
}

# 1. Template narrative covers every gear type and never says "will be fined"
for gear in config.GEAR_TYPES:
    n = reports.template_narrative({**ITEM, "gear_type": gear})
    assert all(n[k] for k in ("summary", "risk", "fix", "prevention")), n
    assert "will be fined" not in json.dumps(n).lower()

# 2. Parser tolerates code fences / chatter around the JSON
txt = 'Here you go:\n```json\n{"summary": "s", "risk": "r", "fix": "f", "prevention": "p"}\n```'
assert reports._parse_json(txt) == {"summary": "s", "risk": "r", "fix": "f", "prevention": "p"}

# 3. Bedrock path with a stubbed converse response (reasoning block + text block)
calls = {}


def fake_converse(**kwargs):
    calls.update(kwargs)
    return {"output": {"message": {"content": [
        {"reasoningContent": {"reasoningText": {"text": "thinking..."}}},
        {"text": json.dumps({"summary": "A worker lacked gloves.", "risk": "Burns.",
                             "fix": "Put on gloves.", "prevention": "Training."})},
    ]}}}


sys.modules["aws_clients"] = types.SimpleNamespace(bedrock=types.SimpleNamespace(converse=fake_converse))
config.MOCK_MODE = False
config.BEDROCK_MODEL_ID = "anthropic.claude-opus-5-5"
r = reports.build_report(ITEM)
assert r["summary"] == "A worker lacked gloves." and "Bedrock" in r["generated_by"], r
assert "temperature" not in calls["inferenceConfig"], calls  # current Claude models reject it
facts = json.loads(calls["messages"][0]["content"][0]["text"])
assert facts["potential_fine_exposure_usd"] == 16550 and facts["osha_rule"].startswith("29 CFR 1910.138")

# 4. Bedrock garbage / failure -> template, never an empty report
sys.modules["aws_clients"].bedrock.converse = lambda **k: {"output": {"message": {"content": [{"text": "no json"}]}}}
r = reports.build_report(ITEM)
assert r["generated_by"].startswith("template") and r["summary"], r

print("all report checks passed")
