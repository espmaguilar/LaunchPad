"""SafeSight API. Run: uvicorn main:app --reload"""
import base64
import binascii
import json
from contextlib import asynccontextmanager
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import incidents
import policy
import ppe
import reports
import stats
import store
import voice
from tracker import tracker

STATIC_DIR = Path(__file__).parent / "static"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Rekognition limit for raw bytes

@asynccontextmanager
async def lifespan(app):
    # One escalation thread per server process. With --reload the old process is
    # killed before the new one starts, so threads never double up.
    # (Don't run with --workers > 1: each worker would have its own tracker.)
    incidents.start_ticker()
    yield
    incidents.stop_ticker()


app = FastAPI(title="SafeSight", lifespan=lifespan)


# ---------- zones ----------
MOCK_ZONES_FILE = store.MOCK_DIR / "zones.json"


def list_zones():
    if config.MOCK_MODE:
        if MOCK_ZONES_FILE.exists():
            return json.loads(MOCK_ZONES_FILE.read_text())
        return [{**z, "alert_email": config.ALERT_EMAIL} for z in config.DEFAULT_ZONES]
    from aws_clients import zones_table

    return sorted(zones_table.scan().get("Items", []), key=lambda z: z["name"])


def get_zone(zone_id):
    if config.MOCK_MODE:
        return next((z for z in list_zones() if z["zone_id"] == zone_id), None)
    from aws_clients import zones_table

    return zones_table.get_item(Key={"zone_id": zone_id}).get("Item")


def save_zones(zones, replace):
    """Write approved zones. replace=True removes zones not in the policy."""
    keep = {z["zone_id"] for z in zones}
    if config.MOCK_MODE:
        current = [] if replace else [z for z in list_zones() if z["zone_id"] not in keep]
        store.MOCK_DIR.mkdir(exist_ok=True)
        MOCK_ZONES_FILE.write_text(json.dumps(sorted(current + zones, key=lambda z: z["name"]), indent=2))
        return
    from aws_clients import zones_table

    with zones_table.batch_writer() as batch:
        for z in zones:
            batch.put_item(Item=z)
        if replace:
            for old in zones_table.scan(ProjectionExpression="zone_id").get("Items", []):
                if old["zone_id"] not in keep:
                    batch.delete_item(Key={"zone_id": old["zone_id"]})


def aws_error(e):
    """Friendly message for any AWS failure."""
    if isinstance(e, ClientError):
        code = e.response["Error"]["Code"]
        msg = e.response["Error"].get("Message", "")
        return HTTPException(502, f"AWS error ({code}): {msg}")
    return HTTPException(502, f"Could not reach AWS: {e}")


# ---------- routes ----------
@app.get("/api/health")
def health():
    missing = config.missing_settings()
    return {
        "ok": config.MOCK_MODE or not missing,
        "mock_mode": config.MOCK_MODE,
        "demo_mode": config.DEMO_MODE,
        "min_person_area": config.MIN_PERSON_AREA,
        "timers": {
            "grace": config.ALERT_GRACE_SECONDS,
            "escalation": config.ESCALATION_SECONDS,
            "resolve": config.RESOLVE_SECONDS,
        },
        "region": config.AWS_REGION,
        "missing_settings": missing,
    }


@app.get("/api/zones")
def zones():
    try:
        return list_zones()
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)


class AnalyzeRequest(BaseModel):
    image: str  # base64 JPEG, with or without a "data:image/jpeg;base64," prefix
    zone_id: str


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    b64 = req.image.split(",", 1)[1] if req.image.startswith("data:") else req.image
    try:
        image_bytes = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "image is not valid base64")
    if not image_bytes:
        raise HTTPException(400, "image is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image larger than 5 MB")

    try:
        zone = get_zone(req.zone_id)
        if zone is None:
            raise HTTPException(404, f"unknown zone '{req.zone_id}'")
        required = list(zone["required"])
        result = ppe.analyze(image_bytes, required)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)

    # Debounce / cooldown / resolve, then save + alert. Failures come back as events.
    meta = {"zone_name": zone["name"], "alert_email": zone.get("alert_email", ""), "rules": zone.get("rules"),
            "people": max(1, len([p for p in result["persons"] if p["missing"]]))}
    raw_events = tracker.update(zone["zone_id"], required, result["persons"], meta=meta)
    events = incidents.process(raw_events, zone, image_bytes, result["persons"])
    events += incidents.drain_ui()  # emails/escalations fired by the background thread

    return {
        "zone_id": zone["zone_id"],
        "zone_name": zone["name"],
        "required": required,
        "mock": config.MOCK_MODE,
        **result,
        "events": events,
        "tracking": tracker.status(zone["zone_id"], required),
    }


@app.get("/api/tracking/{zone_id}")
def tracking(zone_id: str):
    """Tier status for the Detection logic panel (polled even when the camera is off)."""
    try:
        zone = get_zone(zone_id)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    if zone is None:
        raise HTTPException(404, f"unknown zone '{zone_id}'")
    return {"tracking": tracker.status(zone_id, list(zone["required"])), "events": incidents.drain_ui()}


@app.get("/api/voice/{zone_id}/{gear}")
def voice_warning(zone_id: str, gear: str):
    """Tier-1 voice warning MP3 (Polly, cached). 503 -> browser uses speechSynthesis."""
    if gear not in config.GEAR_TYPES:
        raise HTTPException(404, "unknown gear")
    try:
        zone = get_zone(zone_id)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    if zone is None:
        raise HTTPException(404, f"unknown zone '{zone_id}'")
    try:
        audio, _ = voice.mp3_for(zone["name"], gear)
    except voice.VoiceUnavailable as e:
        raise HTTPException(503, f"voice unavailable: {e}")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/debug/last-detection")
def last_detection():
    """What Rekognition actually saw in the last frame (body parts, confidences, gear)."""
    raw = ppe.LAST_RAW.get("raw") or {}
    out = []
    for p in raw.get("Persons", []):
        out.append({
            "id": p.get("Id"),
            "box": {k: round(v, 2) for k, v in p.get("BoundingBox", {}).items()},
            "body_parts": [{
                "name": bp["Name"],
                "confidence": round(bp.get("Confidence", 0), 1),
                "gear": [{"type": e["Type"], "conf": round(e.get("Confidence", 0), 1),
                          "covers": e.get("CoversBodyPart", {}).get("Value")}
                         for e in bp.get("EquipmentDetections", [])],
            } for bp in p.get("BodyParts", [])],
        })
    return {"required": ppe.LAST_RAW.get("required"), "persons": out, "summary": raw.get("Summary")}


@app.get("/api/violations")
def violations(limit: int = 50):
    try:
        return store.list_violations(limit)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)


@app.get("/api/stats")
def dashboard_stats(tz_offset: int = 0):
    """tz_offset = browser's Date.getTimezoneOffset() so 'today' is the viewer's today."""
    try:
        return stats.compute(store.list_violations(None), tz_offset)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)


@app.post("/api/weekly-report")
def weekly_report(tz_offset: int = 0):
    try:
        st = stats.compute(store.list_violations(None), tz_offset)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    return reports.weekly_report(st)


@app.get("/api/violations/{zone_id}/{timestamp}")
def violation_detail(zone_id: str, timestamp: str):
    try:
        item = store.get_violation(zone_id, timestamp)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    if item is None:
        raise HTTPException(404, "violation not found")
    return item


@app.get("/api/violations/{zone_id}/{timestamp}/image")
def violation_image(zone_id: str, timestamp: str):
    key = store.image_key(zone_id, timestamp)
    try:
        loc = store.image_location(key)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    if config.MOCK_MODE:
        if not loc.exists():
            raise HTTPException(404, "image not found")
        return FileResponse(loc, media_type="image/jpeg")
    return RedirectResponse(loc)


# ---------- policy ingestion ----------
class PolicyUpload(BaseModel):
    filename: str
    data: str  # base64 PDF, with or without a data: prefix


@app.post("/api/policy/upload")
def policy_upload(req: PolicyUpload):
    b64 = req.data.split(",", 1)[1] if req.data.startswith("data:") else req.data
    try:
        pdf = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "file is not valid base64")
    if not pdf.startswith(b"%PDF"):
        raise HTTPException(400, "that file is not a PDF")
    if len(pdf) > policy.MAX_PDF_BYTES:
        raise HTTPException(413, "PDF larger than 10 MB")

    try:
        key = policy.store_pdf(pdf)
        text, method, warning = policy.extract_text(pdf, key)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    except policy.PolicyError as e:
        raise HTTPException(422, str(e))
    if len(text) > policy.MAX_TEXT_CHARS:
        raise HTTPException(413, f"policy text is {len(text):,} characters; the limit is "
                                 f"{policy.MAX_TEXT_CHARS:,}. Upload the PPE section only.")

    warnings = [warning] if warning else []
    try:
        raw, source = policy.ai_extract(text)
        draft = policy.to_draft(raw)
    except policy.PolicyError as e:
        draft, source = [], None
        warnings.append(f"{e}. Add zones manually below.")
    return {"policy_key": key, "filename": req.filename, "extraction": method, "text_chars": len(text),
            "text_preview": text[:1500], "generated_by": source, "warnings": warnings, "zones": draft}


class PolicyApprove(BaseModel):
    policy_key: str = ""
    zones: list
    replace_existing: bool = True


@app.post("/api/policy/approve")
def policy_approve(req: PolicyApprove):
    try:
        zones = policy.validate_approved(req.zones)
    except policy.PolicyError as e:
        raise HTTPException(422, str(e))
    approved_at = utc_now()
    for z in zones:
        z.update(policy_key=req.policy_key, approved_at=approved_at)
    try:
        save_zones(zones, req.replace_existing)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    return {"ok": True, "zones": zones}


def utc_now():
    from tracker import utc_timestamp
    import time

    return utc_timestamp(time.time())


@app.get("/api/policy/sample")
def policy_sample():
    path = Path(__file__).parent / "demo" / "sample_safety_policy.pdf"
    if not path.exists():
        raise HTTPException(404, "run: python demo/make_sample_policy.py")
    return FileResponse(path, media_type="application/pdf")


@app.get("/setup")
def setup_page():
    return FileResponse(STATIC_DIR / "setup.html")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
