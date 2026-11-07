"""Policy ingestion: safety-policy PDF -> draft zones -> (manager approves) -> live zones.

1. store the PDF       S3 policies/{timestamp}.pdf   (mock: mock_data/policies/)
2. extract text        Textract async (multi-page PDFs need the S3 path); fallback pypdf
3. structure it        Bedrock -> strict JSON (validated, one retry on bad JSON)
4. map gear            gloves->HAND_COVER, mask/respirator->FACE_COVER, hard hat/helmet->HEAD_COVER;
                       anything else is kept but labelled "Not camera-monitored"
5. approve             nothing is live until the manager approves the reviewed table
"""
import io
import json
import re
import time

from botocore.exceptions import BotoCoreError, ClientError

import config
from tracker import utc_timestamp

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 150_000
TEXTRACT_TIMEOUT_SECONDS = 60
SEVERITIES = ["Serious", "Willful", "Repeat", "Other-than-serious"]
UNMONITORED = "UNMONITORED"

# Order matters: first match wins. Words are matched on the lower-cased item text.
GEAR_KEYWORDS = [
    ("HAND_COVER", ["glove"]),
    ("FACE_COVER", ["respirator", "face mask", "mask", "n95", "face cover"]),
    ("HEAD_COVER", ["hard hat", "hardhat", "helmet", "head protection", "head cover", "bump cap"]),
]
# Checked first, so e.g. "face shield" or "safety goggles" never map to a camera type.
NOT_CAMERA = ["goggle", "glasses", "eyewear", "face shield", "vest", "boot", "shoe", "footwear",
              "ear", "hearing", "apron", "coat", "harness", "sleeve"]


class PolicyError(Exception):
    pass


def map_gear(item):
    text = (item or "").lower()
    if any(w in text for w in NOT_CAMERA):
        return UNMONITORED
    for gear, words in GEAR_KEYWORDS:
        if any(w in text for w in words):
            return gear
    return UNMONITORED


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "zone"


# ---------------- 1-2. store + extract ----------------
def store_pdf(pdf_bytes):
    ts = utc_timestamp(time.time())
    key = f"policies/{ts}.pdf"
    if config.MOCK_MODE:
        import store

        path = store._mock_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf_bytes)
    else:
        from aws_clients import s3

        s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=pdf_bytes, ContentType="application/pdf")
    return key


def _textract(key):
    """Async Textract on the S3 object (handles multi-page PDFs)."""
    from aws_clients import session

    tx = session.client("textract")
    job = tx.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": config.S3_BUCKET, "Name": key}})["JobId"]
    deadline = time.time() + TEXTRACT_TIMEOUT_SECONDS
    while True:
        resp = tx.get_document_text_detection(JobId=job)
        status = resp["JobStatus"]
        if status == "SUCCEEDED":
            break
        if status == "FAILED" or time.time() > deadline:
            raise PolicyError(f"Textract job {status.lower() if status == 'FAILED' else 'timed out'}")
        time.sleep(1)
    lines = []
    while True:
        lines += [b["Text"] for b in resp.get("Blocks", []) if b["BlockType"] == "LINE"]
        token = resp.get("NextToken")
        if not token:
            break
        resp = tx.get_document_text_detection(JobId=job, NextToken=token)
    return "\n".join(lines)


def _pypdf(pdf_bytes):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(pdf_bytes, key):
    """Returns (text, method, warning)."""
    warning = None
    if not config.MOCK_MODE:
        try:
            text = _textract(key)
            if text.strip():
                return text, "Amazon Textract", None
            warning = "Textract returned no text; used pypdf instead."
        except (ClientError, BotoCoreError, PolicyError) as e:
            code = e.response["Error"]["Code"] if isinstance(e, ClientError) else str(e)
            warning = f"Textract unavailable ({code}); used pypdf instead."
    try:
        text = _pypdf(pdf_bytes)
    except Exception as e:
        raise PolicyError(f"Could not read the PDF: {e}")
    if not text.strip():
        raise PolicyError("No text found in the PDF (scanned image PDFs need Textract).")
    return text, "pypdf" + (" (mock mode)" if config.MOCK_MODE else ""), warning


# ---------------- 3. Bedrock -> JSON ----------------
SYSTEM_PROMPT = """You extract workplace PPE requirements from a company safety policy.

Return ONLY a JSON object, no prose, no markdown fences, exactly this shape:
{"zones": [
  {"name": "<zone or area name as written>",
   "supervisor_email": "<email if stated, else null>",
   "gear": [
     {"item": "<required PPE item as written, e.g. 'nitrile gloves'>",
      "rule_reference": "<policy section and/or regulation cited for it, e.g. 'Sec. 4.2 / 29 CFR 1910.138', else null>",
      "severity": "<Serious | Willful | Repeat | Other-than-serious, as stated, else null>",
      "fine_usd": <integer fine amount stated in the policy for this item or zone, else null>}
   ]}
]}

Rules:
- One entry per physical work area/zone that has PPE requirements. Skip zones with none.
- One gear entry per distinct PPE item. Include every required item (eye, foot, hi-vis, hearing too).
- Only use facts written in the document. Never invent fines, sections, or emails: use null.
- fine_usd is a plain integer (16550, not "$16,550")."""


def _converse(messages):
    from aws_clients import bedrock

    resp = bedrock.converse(
        modelId=config.BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={"maxTokens": 8000},  # no temperature: current Claude models reject it
    )
    return "".join(b["text"] for b in resp["output"]["message"]["content"] if "text" in b)


def _parse(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found")
    data = json.loads(text[start:end + 1])
    zones = data.get("zones")
    if not isinstance(zones, list):
        raise ValueError('missing "zones" list')
    for z in zones:
        if not isinstance(z, dict) or not str(z.get("name") or "").strip():
            raise ValueError("every zone needs a name")
        if not isinstance(z.get("gear"), list):
            raise ValueError(f'zone "{z.get("name")}" needs a "gear" list')
    return zones


def ai_extract(text):
    """Returns (raw_zones, source). Retries once if the JSON doesn't validate."""
    if config.MOCK_MODE or not config.BEDROCK_MODEL_ID:
        raise PolicyError("AI extraction needs Bedrock (mock mode / no model configured)")
    messages = [{"role": "user", "content": [{"text": "Safety policy text:\n\n" + text}]}]
    try:
        out = _converse(messages)
        try:
            return _parse(out), f"Amazon Bedrock ({config.BEDROCK_MODEL_ID})"
        except (ValueError, json.JSONDecodeError) as e:
            messages += [
                {"role": "assistant", "content": [{"text": out}]},
                {"role": "user", "content": [{"text": f"That was not valid JSON for the required shape ({e}). "
                                                      "Return ONLY the JSON object."}]},
            ]
            out = _converse(messages)
            return _parse(out), f"Amazon Bedrock ({config.BEDROCK_MODEL_ID}, after 1 retry)"
    except (ValueError, json.JSONDecodeError) as e:
        raise PolicyError(f"Bedrock did not return valid JSON after a retry: {e}")
    except (ClientError, BotoCoreError) as e:
        code = e.response["Error"]["Code"] if isinstance(e, ClientError) else str(e)
        raise PolicyError(f"Bedrock unavailable ({code})")


# ---------------- 4. normalize into an editable draft ----------------
def _int_or_none(v):
    if v in (None, ""):
        return None
    try:
        return int(round(float(str(v).replace("$", "").replace(",", ""))))
    except ValueError:
        return None


def to_draft(raw_zones):
    draft = []
    for z in raw_zones:
        rows = []
        for g in z.get("gear") or []:
            item = str(g.get("item") or "").strip()
            if not item:
                continue
            gear_type = map_gear(item)
            fine = _int_or_none(g.get("fine_usd"))
            sev = g.get("severity") if g.get("severity") in SEVERITIES else None
            row = {"item": item, "gear_type": gear_type, "rule": (g.get("rule_reference") or "").strip(),
                   "severity": sev, "fine": fine, "fine_source": "policy" if fine is not None else None}
            if gear_type != UNMONITORED:
                d = config.rule_for(None, gear_type)
                row["rule"] = row["rule"] or d["label"]
                row["severity"] = row["severity"] or d["severity"]
                if fine is None:
                    row["fine"], row["fine_source"] = d["fine"], "OSHA default"
            rows.append(row)
        email = z.get("supervisor_email") or config.ALERT_EMAIL or ""
        draft.append({"name": str(z["name"]).strip(), "zone_id": slug(str(z["name"])),
                      "alert_email": email, "gear": rows})
    return draft


# ---------------- 5. validate the manager-approved table ----------------
def validate_approved(zones):
    """Turn the reviewed table into zone records. Raises PolicyError with a readable message."""
    if not zones:
        raise PolicyError("Add at least one zone before approving.")
    out, seen = [], set()
    for z in zones:
        name = str(z.get("name") or "").strip()
        if not name:
            raise PolicyError("Every zone needs a name.")
        zid = slug(name)
        if zid in seen:
            raise PolicyError(f'Two zones are both named "{name}".')
        seen.add(zid)
        required, rules, unmonitored = [], {}, []
        for g in z.get("gear") or []:
            item = str(g.get("item") or "").strip()
            gt = g.get("gear_type")
            if not item:
                continue
            fine = _int_or_none(g.get("fine"))
            if fine is not None and not 0 <= fine <= 1_000_000:
                raise PolicyError(f'{name}: fine for "{item}" must be between $0 and $1,000,000.')
            sev = g.get("severity") if g.get("severity") in SEVERITIES else None
            entry = {"item": item, "rule": str(g.get("rule") or "").strip(), "severity": sev,
                     "fine": fine, "source": g.get("fine_source") or ("policy" if fine is not None else None)}
            if gt in config.GEAR_TYPES:
                if gt not in required:  # first item wins if two map to the same camera type
                    required.append(gt)
                    rules[gt] = entry
            elif gt == UNMONITORED:
                unmonitored.append(entry)
            else:
                raise PolicyError(f'{name}: unknown gear type "{gt}".')
        out.append({"zone_id": zid, "name": name, "required": required, "rules": rules,
                    "unmonitored": unmonitored, "alert_email": str(z.get("alert_email") or "").strip()})
    return out
