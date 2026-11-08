"""Step matcher: does the recent transcript show the current step being done - or a mistake?

Text-only Bedrock call per scribe tick. Input: the whole step list (for step numbers), the
current and next step, and the transcript since the current step started (max ~15 s).
Output (strict JSON):
  {status: done|in_progress|not_started|next_step_detected|wrong_action, confidence, evidence,
   mistake, mistake_type: wrong_order|wrong_material|wrong_technique|incomplete|safety,
   what_went_wrong, how_to_fix}

The state machine in sessions.py decides what to do with it (2 consecutive confident checks
before advancing or flagging). MOCK_MODE: the scripted scribe line carries a tag saying which
demo step it belongs to, so the mock matcher is deterministic.
"""
import json
import re

import config
import vision

STATUSES = ("done", "in_progress", "not_started", "next_step_detected", "wrong_action")
MISTAKE_TYPES = ("wrong_order", "wrong_material", "wrong_technique", "incomplete", "safety")

SYSTEM_PROMPT = """You are LabScribe, a friendly lab partner watching a student follow a lab procedure. \
A camera scribe writes one line every few seconds describing the physical actions it sees. \
Judge ONLY the CURRENT step from the recent transcript.

status:
- "done": be GENEROUS. If the transcript shows anything resembling the current step - even partly,
  approximately, or with different objects/wording - call it done. The camera can't count drops, measure
  amounts or judge "until even/dissolved", so never wait for those. Only withhold "done" if nothing in the
  transcript relates to the step at all.
- "in_progress": the student is clearly working on the current step but it isn't finished.
- "not_started": nothing in the transcript relates to the current step yet (idle, unrelated movement).
- "next_step_detected": the transcript shows the NEXT step's action while the current one isn't done.
- "wrong_action": the student is doing something that contradicts the procedure.

mistake: true only if the transcript clearly shows something the manual says not to do, e.g. a material or
action that belongs to a LATER step done now (always wrong_order, even though it is also a different
material), a material that appears in no step at all (wrong_material), a different material than the step says
(wrong_material), the right material handled the wrong way (wrong_technique), moving on before the step
is finished (incomplete), or a safety step ignored (safety). Idle frames are never a mistake. If unsure,
mistake=false.
- what_went_wrong: one short sentence to the student, starting with "You", saying what they did.
  Example: "You added vinegar before the food coloring."
- how_to_fix: one short sentence that cites the manual by step number.
  Example: "Step 3 says to add 3 drops of food coloring first."
- Tone: encouraging coach, never harsh. Both are "" when mistake is false.
- expected_seen: true if the transcript shows what the CURRENT step's "DONE WHEN the camera sees" describes
  (judge just that one visual condition, nothing else), else false.
- evidence: the transcript words that justify the status (short quote), or "".
- confidence: 0.0-1.0, calibrated (0.5 = unsure).

Return ONLY JSON, no prose, no markdown:
{"status": "...", "expected_seen": false, "confidence": 0.0, "evidence": "...", "mistake": false,
 "mistake_type": "", "what_went_wrong": "", "how_to_fix": ""}"""


def _step_line(s):
    extra = f" [DONE WHEN the camera sees: {s['expected_action']}]" if s.get("expected_action") else ""
    return f"{s['number']}. {s['instruction']}{extra}"


def build_prompt(steps, current_idx, transcript_lines):
    cur = steps[current_idx]
    nxt = steps[current_idx + 1] if current_idx + 1 < len(steps) else None
    lines = "\n".join(
        f"[{int(l['elapsed']) // 60:02d}:{int(l['elapsed']) % 60:02d}] {l['action']}"
        + (f" (seen {l['repeat']} times)" if l.get("repeat", 1) > 1 else "")
        for l in transcript_lines) or "(no transcript yet)"
    return (
        "Procedure:\n" + "\n".join(_step_line(s) for s in steps) +
        f"\n\nCURRENT step: {_step_line(cur)}" +
        (f"\nNEXT step: {_step_line(nxt)}" if nxt else "\nNEXT step: (none, this is the last step)") +
        f"\n\nRecent transcript (oldest first):\n{lines}"
    )


def normalize(d):
    """Model JSON -> clean result. Unknown status -> in_progress at low confidence."""
    if not isinstance(d, dict):
        raise ValueError("not an object")
    status = str(d.get("status") or "").strip().lower()
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    if status not in STATUSES:
        status, conf = "in_progress", min(conf, 0.4)
    mistake = d.get("mistake") is True or str(d.get("mistake")).lower() == "true"
    seen = d.get("expected_seen") is True or str(d.get("expected_seen")).lower() == "true"
    # Compound steps ("add ... and stir"): models often keep saying in_progress even when the
    # step's visual done-condition is plainly in the transcript. That condition is the test.
    if seen and status == "in_progress" and not mistake:
        status = "done"
    mtype = str(d.get("mistake_type") or "").strip().lower()
    wwg = re.sub(r"\s+", " ", str(d.get("what_went_wrong") or "")).strip()[:200]
    fix = re.sub(r"\s+", " ", str(d.get("how_to_fix") or "")).strip()[:200]
    if mistake and (mtype not in MISTAKE_TYPES or not wwg):
        mistake = False  # an unexplained "mistake" is not shown to the student
    return {"status": status, "confidence": round(conf, 2),
            "evidence": re.sub(r"\s+", " ", str(d.get("evidence") or "")).strip()[:200],
            "mistake": mistake, "mistake_type": mtype if mistake else "",
            "what_went_wrong": wwg if mistake else "", "how_to_fix": fix if mistake else ""}


STOP = {"the", "and", "with", "into", "from", "your", "until", "then", "this", "that", "for", "cup", "clear",
        "water", "spoon", "add", "stir", "pour", "into", "onto", "before", "after", "step", "you", "while"}


def _words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3 and w not in STOP}


def refine(r, steps, current_idx):
    """A 'wrong material' that is really a LATER step's material is a wrong_order mistake."""
    if r["mistake"] and r["mistake_type"] == "wrong_material":
        earlier = set().union(*(_words(s["instruction"] + " " + " ".join(s.get("visual_cues") or []))
                               for s in steps[: current_idx + 1]))
        later = set().union(*(_words(s["instruction"] + " " + " ".join(s.get("visual_cues") or []))
                             for s in steps[current_idx + 1:])) - earlier
        if later & _words(r["what_went_wrong"]):
            r = {**r, "mistake_type": "wrong_order"}
    return r


CONFIRM_PROMPT = ("You check a camera transcript of a lab bench against ONE visual condition. Answer ONLY JSON "
                  '{"seen": true|false, "confidence": 0.0-1.0}.\n'
                  "seen=true only if the transcript shows the condition's ACTION or RESULT actually happening "
                  "(something poured/added/put on, or its visible result like color appearing or foam). Objects "
                  "merely present, picked up, held or tilted are NOT enough. Wording can differ: 'drops of blue "
                  "liquid into the cup' = 'blue food coloring dripped into the cup'; 'foam bubbling up' is the "
                  "result of vinegar being poured. Ignore amounts and 'until ...' parts.")


def confirm_done(step, transcript_lines):
    """Focused yes/no: is this step's visual done-condition in the transcript?
    Models judge a single condition far better than inside the full status prompt
    (Llama 4 Scout kept compound steps like 'add ... and stir' at in_progress)."""
    from aws_clients import bedrock

    cond = step.get("expected_action") or step["instruction"]
    if step.get("visual_cues"):
        cond += f" (objects/results involved: {', '.join(step['visual_cues'])})"
    resp = bedrock.converse(
        modelId=config.BEDROCK_MODEL_ID,
        system=[{"text": CONFIRM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": f"Condition: {cond}\nTranscript:\n" +
                                                         "\n".join(l["action"] for l in transcript_lines)}]}],
        inferenceConfig={"maxTokens": 60},
    )
    objs = vision.parse_all("".join(b.get("text", "") for b in resp["output"]["message"]["content"]))
    if not objs:
        return False, 0.0
    seen = objs[0].get("seen") is True or str(objs[0].get("seen")).lower() == "true"
    try:
        conf = max(0.0, min(1.0, float(objs[0].get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    return seen, conf


def check(steps, current_idx, transcript_lines):
    """One matcher call (+ a focused done-check when it says in_progress). Returns (result, model)."""
    from aws_clients import bedrock

    resp = bedrock.converse(
        modelId=config.BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": build_prompt(steps, current_idx, transcript_lines)}]}],
        inferenceConfig={"maxTokens": 300},
    )
    text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
    objs = vision.parse_all(text)
    if not objs:
        raise ValueError(f"no JSON in matcher reply: {text[:120]!r}")
    r = refine(normalize(objs[0]), steps, current_idx)
    if not config.LENIENT_ADVANCE and r["status"] == "in_progress" and not r["mistake"] and transcript_lines:
        seen, conf = confirm_done(steps[current_idx], transcript_lines)
        if seen and conf >= config.MATCH_MIN_CONFIDENCE:
            r = {**r, "status": "done", "confidence": round(conf, 2),
                 "evidence": r["evidence"] or transcript_lines[-1]["action"], "confirmed": True}
    return r, config.BEDROCK_MODEL_ID


def mock_check(steps, current_idx, tag, line):
    """Deterministic matcher for MOCK_MODE using the scripted line's tag."""
    cur = steps[current_idx]["number"]
    base = {"confidence": 0.9, "evidence": line["action"], "mistake": False, "mistake_type": "",
            "what_went_wrong": "", "how_to_fix": ""}
    if not tag:
        return {**base, "status": "not_started", "evidence": ""}
    step, kind = tag.get("step"), tag.get("kind")
    if kind == "mistake" and tag.get("during") == cur:
        return {**base, "status": "wrong_action", "mistake": True, "mistake_type": tag["mistake_type"],
                "what_went_wrong": tag["what_went_wrong"], "how_to_fix": tag["how_to_fix"]}
    if step == cur:
        return {**base, "status": "done" if kind == "done" else "in_progress"}
    if step == cur + 1:
        return {**base, "status": "next_step_detected", "confidence": 0.85}
    return {**base, "status": "not_started", "confidence": 0.6, "evidence": ""}


def voice_for_mistake(r):
    """Short, friendly spoken correction: 'Hold on — you added vinegar ... Step 3 says ...'"""
    wwg = r["what_went_wrong"].rstrip(".")
    wwg = wwg[0].lower() + wwg[1:] if wwg[:4].lower() == "you " else wwg
    return f"Hold on — {wwg}. {r['how_to_fix']}".strip()


def as_json(r):
    return json.dumps(r)
