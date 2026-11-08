"""'Feeling confused?' - break one procedure step into tiny, individually checked sub-steps.

When a student is stuck on a step and asks for help, Bedrock splits that step into 2-5
small physical actions, each with its own visual check ("Find the water bottle" ->
camera sees: "water bottle visible"). The session then checks the sub-steps in order with
the focused yes/no check (matcher.confirm_done); finishing them all unlocks the next step.
MOCK_MODE: a simple deterministic split.
"""
import json
import re

import config
import vision

SYSTEM_PROMPT = """You help a confused student with ONE step of a lab procedure by splitting it into tiny physical \
actions they can do one at a time, in order. A camera checks each one.

Return ONLY JSON, no prose, no markdown:
{"substeps": [{"instruction": "<short, friendly, starts with a verb, <= 10 words>",
               "expected_action": "<ONE simple visible condition, <= 8 words, e.g. 'hand holding the water bottle'>"}]}

Rules:
- 2 to 5 substeps that together complete the WHOLE step. Start from what the student is doing now (see
  transcript), e.g. the first substep can be just finding or picking up the object.
- Cover every action in the step (e.g. "add drops" AND "stir"). The LAST substep must be the step's own
  completion, and its expected_action must match the step's "Done when the camera sees" condition.
- Every substep must be visible to a camera (no "think about", no "read the manual").
- expected_action is a single condition about one object/hand - never "X and the other hand holding Y".
- Keep the step's own object names, including aliases in parentheses (e.g. "reagent bottle (water bottle)").
- Use only materials and amounts from the step. Never invent new ones. Encouraging, plain words."""


def _clean(items):
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        instr = re.sub(r"\s+", " ", str(it.get("instruction") or "")).strip()[:120]
        exp = re.sub(r"\s+", " ", str(it.get("expected_action") or "")).strip()[:100]
        if instr:
            out.append({"instruction": instr, "expected_action": exp or instr})
    return out[: config.MAX_SUBSTEPS]


def mock_substeps(step):
    """Deterministic split for MOCK_MODE: find the object, then do the action."""
    cue = (step.get("visual_cues") or [None])[0] or "the materials"
    return [
        {"instruction": f"Find {cue} and hold it up", "expected_action": f"{cue} held up in a hand"},
        {"instruction": "Move it over the bench in view of the camera", "expected_action": f"{cue} over the bench"},
        {"instruction": step["instruction"], "expected_action": step.get("expected_action") or step["instruction"]},
    ]


def make_substeps(step, transcript_lines):
    """Returns a list of {instruction, expected_action}. Raises ValueError if unusable."""
    if config.MOCK_MODE:
        return mock_substeps(step)
    from aws_clients import bedrock

    seen = "\n".join(f"- {l['action']}" for l in transcript_lines[-5:]) or "- (nothing yet)"
    cues = ", ".join(step.get("visual_cues") or []) or "-"
    prompt = (f"Step {step['number']}: {step['instruction']}\n"
              f"Done when the camera sees: {step.get('expected_action') or '-'}\nObjects: {cues}\n\n"
              f"What the camera saw recently:\n{seen}")
    for attempt in range(2):
        resp = bedrock.converse(
            modelId=config.BEDROCK_MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": prompt if attempt == 0 else prompt + "\n\nReturn ONLY the JSON object."}]}],
            inferenceConfig={"maxTokens": 500},
        )
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        for obj in vision.parse_all(text):
            items = _clean(obj.get("substeps"))
            if len(items) >= 2:
                return items
    raise ValueError("couldn't break this step down - try Mark step done or ask the coach")


def as_json(items):
    return json.dumps(items)
