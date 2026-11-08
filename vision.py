"""AI-vision rules: a Bedrock vision model checks the rules Rekognition can't
(goggles, vests, "no phones in the Clean Room"...).

Every AI_CHECK_SECONDS per active zone, the latest camera frame + that zone's
AI-vision rules go to Bedrock Converse (image content block). The model answers
strict JSON: [{"rule": <number>, "violated": true|false|null, "confidence": 0-1, "reason": "..."}].
Results feed the SAME tracker pipeline as Rekognition (tracker.update_ai).

Model: BEDROCK_MODEL_ID; if it rejects images, VISION_FALLBACK_MODEL_ID (Nova Lite)
is used from then on. Runs in a small thread pool so /analyze never waits on it.
Rekognition is untouched.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config

SYSTEM_PROMPT = (
    "You are a workplace safety inspector looking at ONE still frame from a fixed safety camera. "
    "You get numbered site rules. For each rule, decide if the frame shows the rule being BROKEN right now.\n"
    "How to read rules:\n"
    '- A requirement ("Safety goggles must be worn", "Hi-vis vest") is BROKEN when a visible person is '
    "NOT wearing that item. No goggles visible on a visible person = violated: true.\n"
    '- A prohibition ("No phones", "No food") is BROKEN only when you can clearly see the prohibited '
    "thing happening. Don't guess: a hand near the face is not a phone.\n"
    "- Hand-gesture rules (showing a number, thumbs up, peace sign) are BROKEN only if you can clearly see "
    "the extended fingers and count them. A fist, a hand at the mouth or chin, or a partly hidden hand is "
    "NOT a gesture: violated=false.\n"
    "- Motion rules (walking, running) can't be seen in one still frame unless the pose is unmistakable "
    "(mid-stride, legs apart, body leaning forward): otherwise violated=null.\n"
    "- Judge each rule on its own evidence; one broken rule is not evidence for another.\n"
    "- violated: null if the rule can't be judged from this frame (nobody visible, the relevant body "
    "part out of frame, too blurry).\n"
    "First describe what you actually see for that rule in 'evidence', then decide. Use a calibrated "
    "confidence (0.5 = unsure, 0.9+ = clearly visible), not always 1.0.\n"
    "Return ONLY a JSON array, no prose, no markdown fences, exactly one object per rule:\n"
    '[{"rule": 1, "evidence": "what is visible", "violated": true, "confidence": 0.85, '
    '"reason": "short, factual, <= 15 words"}]'
)

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="safesight-vision")
_lock = threading.Lock()
_last_check = {}   # zone_id -> time the last check was started
_inflight = set()  # zone_ids with a check running
LAST = {}          # zone_id -> last result for the UI
_active_model = None  # resolved after the first call (configured model or fallback)


class VisionError(Exception):
    pass


def _image_rejected(e):
    """True if Bedrock says this model can't take image input."""
    from botocore.exceptions import ClientError
    if not isinstance(e, ClientError):
        return False
    code = e.response["Error"]["Code"]
    msg = e.response["Error"].get("Message", "").lower()
    return code == "ValidationException" and any(w in msg for w in ("image", "multimodal", "doesn't support",
                                                                    "does not support", "unsupported"))


def _converse(model_id, image_bytes, user_text):
    from aws_clients import bedrock
    resp = bedrock.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [
            {"image": {"format": "jpeg", "source": {"bytes": image_bytes}}},
            {"text": user_text},
        ]}],
        inferenceConfig={"maxTokens": 600},
    )
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"])


def call_model(image_bytes, user_text):
    """Returns (text, model_id). Falls back to the vision fallback model once if needed."""
    global _active_model
    model = _active_model or config.BEDROCK_MODEL_ID
    try:
        return _converse(model, image_bytes, user_text), model
    except Exception as e:
        if model != config.VISION_FALLBACK_MODEL_ID and _image_rejected(e):
            print(f"[vision] {model} rejected image input; falling back to {config.VISION_FALLBACK_MODEL_ID}")
            _active_model = config.VISION_FALLBACK_MODEL_ID
            return _converse(_active_model, image_bytes, user_text), _active_model
        raise


def parse(text, rules):
    """Model text -> {rule_key: {violated, confidence, reason}}. Raises VisionError."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        raise VisionError("no JSON array in the model reply")
    try:
        arr = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise VisionError(f"invalid JSON: {e}") from e
    out = {}
    for obj in arr if isinstance(arr, list) else []:
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(str(obj.get("rule")).strip().rstrip(".")) - 1
        except ValueError:  # model echoed the rule text instead of the number
            idx = next((i for i, r in enumerate(rules)
                        if str(obj.get("rule", "")).lower().strip() == r["item"].lower()), -1)
        if not 0 <= idx < len(rules):
            continue
        v = obj.get("violated")
        v = v if isinstance(v, bool) else None
        try:
            conf = max(0.0, min(1.0, float(obj.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0.0
        out[rules[idx]["key"]] = {"violated": v, "confidence": round(conf, 2),
                                  "reason": str(obj.get("reason") or "")[:160]}
    return out


def to_tracker(parsed, rules):
    """Low-confidence 'violated' answers count as unsure (None); missing rules too."""
    res = {}
    for r in rules:
        p = parsed.get(r["key"])
        if not p or p["violated"] is None:
            res[r["key"]] = None
        elif p["violated"] and p["confidence"] < config.AI_MIN_CONFIDENCE:
            res[r["key"]] = None
        else:
            res[r["key"]] = p["violated"]
    return res


def _mock_parsed(rules):
    """Mock mode: the zone's first AI rule is violated for 36 s of every 60 s."""
    on = (time.time() % 60) < 36
    return {r["key"]: {"violated": (i == 0 and on), "confidence": 0.9,
                       "reason": ("Mock: rule not followed" if i == 0 and on else "Mock: looks compliant")}
            for i, r in enumerate(rules)}


def check(image_bytes, rules):
    """One vision check. Returns (parsed, model_id)."""
    if config.MOCK_MODE:
        return _mock_parsed(rules), "mock"
    numbered = "\n".join(f"{i + 1}. {r['item']}" + (f" ({r['rule']})" if r.get("rule") else "")
                         for i, r in enumerate(rules))
    text, model = call_model(image_bytes, f"Rules for this zone:\n{numbered}")
    try:
        return parse(text, rules), model
    except VisionError:
        text, model = call_model(image_bytes, f"Rules for this zone:\n{numbered}\n\n"
                                              "Reply with ONLY the JSON array described.")
        return parse(text, rules), model


def maybe_schedule(zone, image_bytes, on_result, now=None):
    """Start a background check if this zone has AI rules and is due. Never blocks."""
    rules = config.ai_rules(zone)
    if not rules:
        return False
    zid = zone["zone_id"]
    now = time.time() if now is None else now
    with _lock:
        if zid in _inflight or now - _last_check.get(zid, 0) < config.AI_CHECK_SECONDS:
            return False
        _inflight.add(zid)
        _last_check[zid] = now
    _pool.submit(_run, zone, rules, image_bytes, on_result)
    return True


def _run(zone, rules, image_bytes, on_result):
    zid = zone["zone_id"]
    started = time.time()
    try:
        parsed, model = check(image_bytes, rules)
        LAST[zid] = {"at": started, "model": model, "error": None,
                     "results": [{"key": r["key"], "item": r["item"], **parsed.get(r["key"], {
                         "violated": None, "confidence": 0, "reason": "no answer"})} for r in rules]}
        on_result(zone, to_tracker(parsed, rules), image_bytes, parsed)
    except Exception as e:  # never let a vision failure touch the Rekognition path
        from botocore.exceptions import ClientError
        msg = (f"{e.response['Error']['Code']}: {e.response['Error'].get('Message', '')}"
               if isinstance(e, ClientError) else str(e))
        print(f"[vision] check failed for {zid}: {msg}")
        LAST[zid] = {"at": started, "model": _active_model or config.BEDROCK_MODEL_ID, "error": msg[:200],
                     "results": (LAST.get(zid) or {}).get("results", [])}
    finally:
        with _lock:
            _inflight.discard(zid)


def last(zone_id):
    r = LAST.get(zone_id)
    return {**r, "age": round(time.time() - r["at"])} if r else None
