"""Ask LabScribe: the student's questions, answered with the session's context.

Text-only Bedrock call with the procedure, the current step, recent Scribe lines and any open
feedback, so answers are about THIS run ("Hold the bottle over the cup so the camera sees the
drops"). Short, encouraging, never makes up procedure steps. MOCK_MODE: a canned answer that
still uses the current step.
"""
import re

import config
import matcher

SYSTEM_PROMPT = """You are LabScribe, a friendly lab partner coaching a student through a lab procedure in real \
time. A camera scribe describes what the student is doing. Answer the student's question in 1-3 short sentences.

Rules:
- Use the procedure and what the camera saw. Refer to step numbers. Never invent steps, amounts or safety rules
  that are not in the procedure; if the procedure doesn't say, say so and suggest asking the instructor.
- Be encouraging and practical, never harsh. Plain text only, no markdown, no lists."""

MAX_QUESTION = 400


def answer(session, question):
    question = re.sub(r"\s+", " ", str(question or "")).strip()[:MAX_QUESTION]
    if not question:
        raise ValueError("Ask a question first.")
    steps = session["procedure"]["steps"]
    idx = min(session["current"], len(steps) - 1)
    if config.MOCK_MODE:
        step = steps[idx]
        return (f"(Mock mode) You're on step {step['number']}: {step['instruction']} "
                "Keep your hands and materials in view so I can follow along."), "mock"
    recent = session["transcript"][-8:]
    open_fb = [f for f in session["feedback"] if not f["dismissed"]]
    context = matcher.build_prompt(steps, idx, recent)
    if session["current"] >= len(steps):
        context += "\n\nAll steps are complete."
    if open_fb:
        context += "\n\nOpen feedback: " + " | ".join(f"{f['what_went_wrong']} {f['how_to_fix']}" for f in open_fb[-3:])
    from aws_clients import bedrock

    resp = bedrock.converse(
        modelId=config.BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": f"{context}\n\nStudent's question: {question}"}]}],
        inferenceConfig={"maxTokens": 250},
    )
    text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()
    text = re.sub(r"[*#`_]+", "", text)  # plain text for the chat bubble + Polly
    return text[:600] or "Sorry, I couldn't come up with an answer — try asking another way.", config.BEDROCK_MODEL_ID
