"""Bedrock vision calls (image content blocks), shared by the Scribe and the PPE check.

call_model(images, system, text) tries BEDROCK_MODEL_ID first; if Bedrock says that
model can't take images, it switches to VISION_FALLBACK_MODEL_ID (Nova Lite) and
remembers the switch. parse_json() pulls the JSON out of a reply.
"""
import json
import re

import config

_active_model = None  # resolved after the first call (configured model or fallback)


class VisionError(Exception):
    pass


def active_model():
    return _active_model or config.BEDROCK_MODEL_ID


def image_rejected(e):
    """True if Bedrock says this model can't take image input."""
    from botocore.exceptions import ClientError
    if not isinstance(e, ClientError):
        return False
    code = e.response["Error"]["Code"]
    msg = e.response["Error"].get("Message", "").lower()
    return code == "ValidationException" and any(w in msg for w in ("image", "multimodal", "doesn't support",
                                                                    "does not support", "unsupported"))


def _converse(model_id, images, system, text, max_tokens):
    from aws_clients import bedrock
    content = [{"image": {"format": fmt, "source": {"bytes": data}}} for data, fmt in images]
    resp = bedrock.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": content + [{"text": text}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"])


def call_model(images, system, text, max_tokens=400):
    """images: [(bytes, "jpeg"|"png"), ...]. Returns (reply_text, model_id)."""
    global _active_model
    model = active_model()
    try:
        return _converse(model, images, system, text, max_tokens), model
    except Exception as e:
        if model != config.VISION_FALLBACK_MODEL_ID and image_rejected(e):
            print(f"[vision] {model} rejected image input; falling back to {config.VISION_FALLBACK_MODEL_ID}")
            _active_model = config.VISION_FALLBACK_MODEL_ID
            return _converse(_active_model, images, system, text, max_tokens), _active_model
        raise


def parse_json(text, kind="object"):
    """First JSON object (or array) in a model reply. Raises VisionError."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    o, c = ("{", "}") if kind == "object" else ("[", "]")
    start, end = cleaned.find(o), cleaned.rfind(c)
    if start < 0 or end <= start:
        raise VisionError(f"no JSON {kind} in the model reply")
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise VisionError(f"invalid JSON: {e}") from e
