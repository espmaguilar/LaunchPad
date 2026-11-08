"""Procedure ingestion: lab manual PDF -> draft steps -> (student/instructor approves) -> saved.

1. store the PDF     S3 labscribe/manuals/{timestamp}.pdf (mock: mock_data/)
2. extract text      Textract (pypdf fallback)                         -> ingest.py
3. structure it      Bedrock -> strict JSON steps[] (validated, one retry on bad JSON)
                     MOCK_MODE / Bedrock unavailable: heuristic "Step N:" parser (flagged)
4. review            editable step list; nothing is saved until "Approve"
"""
import json
import re

from botocore.exceptions import BotoCoreError, ClientError

import config

SYSTEM_PROMPT = """You turn a lab manual into a step-by-step procedure that a camera-based lab partner \
can follow. The camera sees the bench, the student's hands and the equipment.

Return ONLY a JSON object, no prose, no markdown fences, exactly this shape:
{"title": "<experiment name as written>",
 "steps": [
   {"number": 1,
    "section": "<part/section heading this step belongs to, e.g. 'Part 1: Standing waves in strings', or ''>",
    "instruction": "<the step, as the manual says it, one or two sentences>",
    "camera_checkable": <true if a camera on the bench could SEE this step being done (handling equipment,
                         adjusting, measuring with a ruler, pouring, connecting); false for calculations,
                         plotting/graphing, sketching, writing answers or reading values off a screen only>,
    "expected_action": "<what the camera should SEE when the step is done: short, visual, <= 12 words; '' if not camera_checkable>",
    "visual_cues": ["<objects, colors, containers visible in this step>"],
    "safety_critical": <true if the manual marks it as a safety step, or it involves PPE, heat, sharps or chemicals that can splash>,
    "required_ppe": ["<PPE the manual requires for this step, e.g. 'safety goggles', 'gloves', 'lab coat'>"]}
 ]}

Rules:
- Real lab manuals mix theory, equations, data tables, questions and procedure. Extract ONLY the things the
  student actually does, in the manual's order. Skip background, theory, equations, tables to fill in and
  pre-lab/post-lab questions unless they are an action ("Sketch the three lowest modes" is a paper step).
- One entry per action. Split "Do A, then B" into two steps when both are separate physical actions.
- expected_action must be observable by a camera (e.g. "blue food coloring dripped into the cup",
  "ruler held along the string"), not a thought.
- visual_cues: 1-5 short nouns/colors ("clear cup", "function generator", "white powder").
- required_ppe: only PPE the manual states; [] if none. Never invent PPE or steps.
- If the document contains no procedure at all (e.g. it's only a diagram or a list of formulas), return
  {"title": "<title>", "steps": []}."""


class ProcedureError(Exception):
    pass


class NoStepsFound(ProcedureError):
    """Bedrock read the document fine but found no procedure in it (e.g. a diagram)."""


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "procedure"


# ---------------- Bedrock ----------------
def _converse(messages):
    from aws_clients import bedrock

    resp = bedrock.converse(
        modelId=config.BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={"maxTokens": 6000},  # no temperature: current Claude models reject it
    )
    return "".join(b["text"] for b in resp["output"]["message"]["content"] if "text" in b)


def _parse(text):
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found")
    data = json.loads(cleaned[start:end + 1])
    if not isinstance(data.get("steps"), list):
        raise ValueError('"steps" must be a list')
    for i, s in enumerate(data["steps"], 1):
        if not isinstance(s, dict) or not str(s.get("instruction") or "").strip():
            raise ValueError(f"step {i} has no instruction")
    return data


def ai_extract(text):
    """Returns (raw, source). Retries once if the JSON doesn't validate."""
    if config.MOCK_MODE or not config.BEDROCK_MODEL_ID:
        raise ProcedureError("AI extraction needs Bedrock (mock mode / no model configured)")
    messages = [{"role": "user", "content": [{"text": "Lab manual text:\n\n" + text[:60000]}]}]
    try:
        out = _converse(messages)
        try:
            data = _parse(out)
            if not data["steps"]:
                raise NoStepsFound("Bedrock found no procedure steps in this document")
            return data, f"Amazon Bedrock ({config.BEDROCK_MODEL_ID})"
        except (ValueError, json.JSONDecodeError) as e:
            messages += [
                {"role": "assistant", "content": [{"text": out}]},
                {"role": "user", "content": [{"text": f"That was not valid JSON for the required shape ({e}). "
                                                      "Return ONLY the JSON object."}]},
            ]
            out = _converse(messages)
            data = _parse(out)
            if not data["steps"]:
                raise NoStepsFound("Bedrock found no procedure steps in this document")
            return data, f"Amazon Bedrock ({config.BEDROCK_MODEL_ID}, after 1 retry)"
    except (ValueError, json.JSONDecodeError) as e:
        raise ProcedureError(f"Bedrock did not return valid JSON after a retry: {e}")
    except (ClientError, BotoCoreError) as e:
        code = e.response["Error"]["Code"] if isinstance(e, ClientError) else str(e)
        raise ProcedureError(f"Bedrock unavailable ({code})")


# ---------------- heuristic fallback (mock mode) ----------------
PPE_WORDS = {"goggle": "safety goggles", "glove": "gloves", "lab coat": "lab coat", "apron": "apron", "mask": "face mask"}
SAFETY_WORDS = ("safety", "goggle", "glove", "hot", "heat", "flame", "acid", "base", "splash", "sharp", "caution", "warning")


def _is_heading(line):
    """'QUESTIONS', 'Questions', 'Clean-up' - short, no sentence punctuation."""
    return len(line) <= 30 and not re.search(r"[.,;:]$", line) and len(line.split()) <= 3 and line[0].isupper()


def heuristic_extract(text):
    """'Step N: ...' / 'N. ...' lines -> steps. Used in MOCK_MODE or when Bedrock is unavailable.
    expected_action/visual_cues are left for the reviewer (no AI to infer them)."""
    lines = [l.strip() for l in text.splitlines()]
    title = next((l for l in lines if l and not re.match(r"^(step\s*)?\d+[.):]", l, re.I)), "Untitled procedure")
    steps, cur = [], None
    for l in lines:
        m = re.match(r"^(?:step\s*)?(\d{1,2})\s*[.):\-]\s*(.+)$", l, re.I)
        if m:
            cur = {"instruction": m.group(2)}
            steps.append(cur)
        elif cur is not None and l and _is_heading(l):
            cur = None  # a section heading ("Questions") ends the step list
        elif cur is not None and l and len(cur["instruction"]) < 250:
            cur["instruction"] += " " + l  # wrapped line
        elif not l:
            cur = None
    for s in steps:
        low = s["instruction"].lower()
        s["required_ppe"] = sorted({v for k, v in PPE_WORDS.items() if k in low})
        s["safety_critical"] = any(w in low for w in SAFETY_WORDS)
        s["expected_action"] = ""
        s["visual_cues"] = []
        s["camera_checkable"] = not re.match(r"^(calculate|compute|plot|graph|sketch|draw|record|write|answer|"
                                             r"determine|find|use your|derive|explain)\b", low)
    if not steps:
        raise ProcedureError("No numbered steps found. Add them manually below.")
    return {"title": title, "steps": steps}


# ---------------- normalize / validate ----------------
def _str_list(v, limit=8, each=40):
    if isinstance(v, str):
        v = [x for x in re.split(r"[,;]", v)]
    out = []
    for x in v or []:
        x = str(x).strip()
        if x and x.lower() not in (o.lower() for o in out):
            out.append(x[:each])
    return out[:limit]


def _bool(v):
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")


def normalize(raw):
    """Model/heuristic output -> editable draft (numbers re-assigned 1..n)."""
    steps = []
    for s in (raw.get("steps") or [])[: config.MAX_STEPS]:
        instr = re.sub(r"\s+", " ", str(s.get("instruction") or "")).strip()
        if not instr:
            continue
        camera = s.get("camera_checkable")
        camera = True if camera is None else _bool(camera)
        steps.append({
            "section": re.sub(r"\s+", " ", str(s.get("section") or "")).strip()[:80],
            "instruction": instr[: config.MAX_INSTRUCTION_CHARS],
            "camera_checkable": camera,
            "expected_action": str(s.get("expected_action") or "").strip()[: config.MAX_EXPECTED_ACTION_CHARS],
            "visual_cues": _str_list(s.get("visual_cues")),
            "safety_critical": _bool(s.get("safety_critical")),
            "required_ppe": [p.lower() for p in _str_list(s.get("required_ppe"), limit=5)],
        })
    for i, s in enumerate(steps, 1):
        s["number"] = i
    return {"title": str(raw.get("title") or "Untitled procedure").strip()[:120], "steps": steps}


def validate(draft):
    """Approve-time checks with readable messages. Returns the cleaned procedure body."""
    title = str((draft or {}).get("title") or "").strip()
    if not title:
        raise ProcedureError("Give the procedure a name.")
    clean = normalize({"title": title, "steps": draft.get("steps") or []})
    if not clean["steps"]:
        raise ProcedureError("Add at least one step with an instruction.")
    if len(draft.get("steps") or []) > config.MAX_STEPS:
        raise ProcedureError(f"A procedure can have at most {config.MAX_STEPS} steps.")
    for s in clean["steps"]:
        if s["safety_critical"] and not s["required_ppe"]:
            pass  # allowed (e.g. "turn off the burner"); the PPE check only runs when PPE is listed
    return clean
