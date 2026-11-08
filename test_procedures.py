"""Offline checks for procedure ingestion (Phase A). Bedrock is stubbed - no AWS.
Run: python test_procedures.py"""
import json
import pathlib
import sys
import tempfile
import types

import config
import ingest
import procedures

# 1. Bedrock stub: broken JSON first, valid second -> one retry succeeds
RAW = {"title": "Color-Change Reaction", "steps": [
    {"number": 1, "instruction": "Put on safety goggles.", "expected_action": "student wearing goggles",
     "visual_cues": ["goggles"], "safety_critical": True, "required_ppe": ["Safety Goggles"]},
    {"number": 2, "instruction": "  Fill the cup   halfway with water. ", "expected_action": "water in clear cup",
     "visual_cues": "clear cup, water, clear cup", "safety_critical": "false", "required_ppe": []},
]}
replies = ["Here you go: {steps: [oops", "```json\n" + json.dumps(RAW) + "\n```"]
calls = []


def fake_converse(**kw):
    calls.append(kw)
    return {"output": {"message": {"content": [{"text": replies[len(calls) - 1]}]}}}


sys.modules["aws_clients"] = types.SimpleNamespace(bedrock=types.SimpleNamespace(converse=fake_converse))
config.MOCK_MODE, config.BEDROCK_MODEL_ID = False, "test-model"
raw, source = procedures.ai_extract("manual text")
assert len(calls) == 2 and "retry" in source, source
assert all("temperature" not in c["inferenceConfig"] for c in calls)
assert calls[1]["messages"][-1]["role"] == "user"   # retry asks again, no assistant prefill

# 1b. two bad replies -> clear error (upload falls back to the heuristic parser)
calls.clear()
replies[:] = ["nope", "still nope"]
try:
    procedures.ai_extract("manual text")
    raise AssertionError("expected ProcedureError")
except procedures.ProcedureError as e:
    assert "after a retry" in str(e) and not isinstance(e, procedures.NoStepsFound)

# 1c. valid JSON but no steps (a diagram-only PDF) -> NoStepsFound, no retry
calls.clear()
replies[:] = ['{"title": "String modes", "steps": []}']
try:
    procedures.ai_extract("n=1 lambda=2L AN N")
    raise AssertionError("expected NoStepsFound")
except procedures.NoStepsFound:
    assert len(calls) == 1

# 2. normalize: whitespace, duplicate cues, string booleans, lower-case PPE, renumbering
d = procedures.normalize(raw)
assert [s["number"] for s in d["steps"]] == [1, 2]
s1, s2 = d["steps"]
assert s1["required_ppe"] == ["safety goggles"] and s1["safety_critical"] is True
assert s2["instruction"] == "Fill the cup halfway with water." and s2["visual_cues"] == ["clear cup", "water"]
assert s2["safety_critical"] is False

# 3. validate: readable errors; reorder/delete in the editor just renumbers
for bad, msg in [({"title": "", "steps": RAW["steps"]}, "name"),
                 ({"title": "X", "steps": [{"instruction": "  "}]}, "at least one step"),
                 ({"title": "X", "steps": [{"instruction": "a"}] * (config.MAX_STEPS + 1)}, "at most")]:
    try:
        procedures.validate(bad)
        raise AssertionError(msg)
    except procedures.ProcedureError as e:
        assert msg in str(e), (msg, e)
v = procedures.validate({"title": "Lab", "steps": [RAW["steps"][1], RAW["steps"][0]]})  # reordered
assert [s["instruction"][:4] for s in v["steps"]] == ["Fill", "Put "] and v["steps"][1]["number"] == 2

# 4. demo manual: readable by pypdf; heuristic parser (mock mode) finds all 6 steps + both safety steps
pdf = (pathlib.Path(__file__).parent / "demo" / "color_change_lab.pdf").read_bytes()
text = ingest.pypdf_text(pdf)
h = procedures.normalize(procedures.heuristic_extract(text))
assert len(h["steps"]) == 6, [s["instruction"] for s in h["steps"]]
assert h["steps"][0]["safety_critical"] and h["steps"][0]["required_ppe"] == ["safety goggles"]
assert h["steps"][2]["instruction"].endswith("color is even.")      # wrapped line joined
assert h["steps"][5]["instruction"].endswith("rinse the cup.")      # "Questions" heading not glued on
assert [s["safety_critical"] for s in h["steps"]] == [True, False, False, False, True, False]

# 5. mock store round trip
import store
config.MOCK_MODE = True
store.MOCK_DIR = pathlib.Path(tempfile.mkdtemp())
p = {"procedure_id": "lab-1", "title": "Lab", "steps": v["steps"], "approved_at": "2026-11-08T00:00:00.000Z"}
store.save_procedure(p)
store.save_procedure({**p, "title": "Lab v2"})               # same id -> replaced, not duplicated
assert [x["title"] for x in store.list_procedures()] == ["Lab v2"]
assert store.get_procedure("lab-1")["steps"][0]["number"] == 1
store.delete_procedure("lab-1")
assert store.list_procedures() == [] and store.get_procedure("lab-1") is None

# 6. camera_checkable + section carried through; paper steps get no expected action requirement
n = procedures.normalize({"title": "Lab 7", "steps": [
    {"section": "Part 1: Strings", "instruction": "Measure the string length.", "camera_checkable": True, "expected_action": "ruler along string"},
    {"section": "Part 1: Strings", "instruction": "Calculate the wave speed.", "camera_checkable": "false"},
    {"instruction": "Connect the speaker."}]})
assert [x["camera_checkable"] for x in n["steps"]] == [True, False, True]       # missing -> camera-checkable
assert n["steps"][0]["section"] == "Part 1: Strings" and n["steps"][2]["section"] == ""
h = procedures.normalize(procedures.heuristic_extract("Lab\n1. Measure the length.\n2. Calculate the speed.\n3. Plot the data."))
assert [x["camera_checkable"] for x in h["steps"]] == [True, False, False]

print("all procedure checks passed")
