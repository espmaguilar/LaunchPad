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
import notify
import policy
import ppe
import records
import reports
import settings
import stats
import store
import vision
import voice
from tracker import tracker

STATIC_DIR = Path(__file__).parent / "static"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Rekognition limit for raw bytes

@asynccontextmanager
async def lifespan(app):
    # One notification thread per server process. With --reload the old process is
    # killed before the new one starts, so threads never double up.
    # (Don't run with --workers > 1: each worker would have its own tracker.)
    settings.init()  # apply saved Settings-page values onto config
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
            "voice_after": config.VOICE_AFTER_SECONDS,
            "notify_after": config.NOTIFY_AFTER_SECONDS,
            "resolve": config.RESOLVE_SECONDS,
        },
        "notify_channel": notify.channel_label(),
        "region": config.AWS_REGION,
        "missing_settings": missing,
    }


@app.get("/api/zones")
def zones():
    try:
        return [{**z, "ai_rules": config.ai_rules(z)} for z in list_zones()]
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)


def tracked_keys(zone):
    """Rekognition gear types + AI-vision rule keys, with labels for the UI."""
    keys = list(zone["required"]) + [r["key"] for r in config.ai_rules(zone)]
    return keys, {k: config.gear_label(k, zone) for k in keys}


def tracking_for(zone):
    keys, labels = tracked_keys(zone)
    st = tracker.status(zone["zone_id"], keys)
    for k, v in st.items():
        v["label"] = labels[k]
    return st


class RuleRequest(BaseModel):
    item: str
    fine: int | None = None
    severity: str | None = None
    rule: str | None = None


@app.post("/api/zones/{zone_id}/rules")
def add_rule(zone_id: str, req: RuleRequest):
    """Add a free-text AI-vision rule to a live zone (no PDF needed)."""
    item = req.item.strip()
    if not 3 <= len(item) <= 120:
        raise HTTPException(422, "Describe the rule in 3-120 characters, e.g. \"No phones\".")
    if req.fine is not None and not 0 <= req.fine <= 1_000_000:
        raise HTTPException(422, "Fine must be between $0 and $1,000,000.")
    try:
        zone = get_zone(zone_id)
        if zone is None:
            raise HTTPException(404, f"unknown zone '{zone_id}'")
        key = config.ai_key(item)
        if any(r["key"] == key for r in config.ai_rules(zone)):
            raise HTTPException(409, f'"{item}" is already a rule in {zone["name"]}.')
        sev = req.severity if req.severity in policy.SEVERITIES else None
        zone = {**zone, "unmonitored": list(zone.get("unmonitored") or []) + [{
            "item": item, "rule": (req.rule or "").strip() or "Custom rule", "severity": sev,
            "fine": req.fine, "source": "custom" if req.fine is not None else None}]}
        save_zones([zone], replace=False)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    return {**zone, "ai_rules": config.ai_rules(zone)}


@app.delete("/api/zones/{zone_id}/rules/{key}")
def delete_rule(zone_id: str, key: str):
    try:
        zone = get_zone(zone_id)
        if zone is None:
            raise HTTPException(404, f"unknown zone '{zone_id}'")
        kept = [u for u in zone.get("unmonitored") or [] if config.ai_key(u.get("item", "")) != key]
        zone = {**zone, "unmonitored": kept}
        save_zones([zone], replace=False)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    tracker.forget(zone_id, key)
    return {**zone, "ai_rules": config.ai_rules(zone)}


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
    meta = incidents.zone_meta(zone, people=max(1, len([p for p in result["persons"] if p["missing"]])))
    raw_events = tracker.update(zone["zone_id"], required, result["persons"], meta=meta)
    events = incidents.process(raw_events, zone, image_bytes, result["persons"])
    # AI-vision rules: background Bedrock check every AI_CHECK_SECONDS (results arrive via drain_ui)
    vision.maybe_schedule(zone, image_bytes, incidents.on_vision_result)
    events += incidents.drain_ui()  # supervisor notifications fired by the background thread

    return {
        "zone_id": zone["zone_id"],
        "zone_name": zone["name"],
        "required": required,
        "mock": config.MOCK_MODE,
        **result,
        "events": events,
        "tracking": tracking_for(zone),
        "ai_vision": vision.last(zone["zone_id"]),
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
    return {"tracking": tracking_for(zone), "events": incidents.drain_ui(), "ai_vision": vision.last(zone_id)}


@app.get("/api/voice/{zone_id}/{gears}")
def voice_warning(zone_id: str, gears: str):
    """Voice warning MP3 for one zone; gears is comma-separated (one combined phrase).
    Polly, cached. 503 -> browser uses speechSynthesis."""
    gear_list = [g for g in gears.split(",") if g]
    try:
        zone = get_zone(zone_id)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    if zone is None:
        raise HTTPException(404, f"unknown zone '{zone_id}'")
    valid = set(config.GEAR_TYPES) | {r["key"] for r in config.ai_rules(zone)}
    if not gear_list or any(g not in valid for g in gear_list):
        raise HTTPException(404, "unknown gear or rule")
    try:
        audio, _ = voice.mp3_for(zone["name"], gear_list, zone)
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


@app.get("/api/dashboard")
def dashboard(days: int = 7, tz_offset: int = 0):
    if days not in (7, 30, 90):
        raise HTTPException(422, "days must be 7, 30 or 90")
    try:
        items = store.list_violations(None)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    return {**stats.dashboard(items, tz_offset, days), "today": stats.compute(items, tz_offset)["today"]}


@app.post("/api/violations/{zone_id}/{timestamp}/resolve")
def resolve_violation(zone_id: str, timestamp: str):
    """Manager marks an incident resolved from the Incidents page."""
    try:
        item = store.get_violation(zone_id, timestamp)
        if item is None:
            raise HTTPException(404, "violation not found")
        if item.get("status") == "RESOLVED":
            return item
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    err = incidents.resolve_all(zone_id, timestamp)
    if err:
        raise HTTPException(502, f"Could not resolve: {err}")
    for it in records.items_of(item):  # still missing -> fresh 20 s / 30 s cycle per item
        tracker.forget(zone_id, it["gear"], restart_clock=True)
    return store.get_violation(zone_id, timestamp)


class ZoneUpdate(BaseModel):
    alert_email: str


@app.put("/api/zones/{zone_id}")
def update_zone(zone_id: str, req: ZoneUpdate):
    email = req.alert_email.strip()
    if email and ("@" not in email or " " in email or len(email) > 254):
        raise HTTPException(422, "Enter a valid email address.")
    try:
        zone = get_zone(zone_id)
        if zone is None:
            raise HTTPException(404, f"unknown zone '{zone_id}'")
        zone = {**zone, "alert_email": email}
        save_zones([zone], replace=False)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    return {**zone, "ai_rules": config.ai_rules(zone)}


@app.get("/api/settings")
def get_settings():
    return {"settings": settings.current(), "presets": settings.PRESETS, "polly_voices": settings.POLLY_VOICES,
            "notify_channel": notify.channel_label(), "sns_topic": bool(config.SNS_TOPIC_ARN),
            "alert_email": config.ALERT_EMAIL}


@app.put("/api/settings")
def put_settings(body: dict):
    try:
        return {"settings": settings.save(body)}
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))


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
    """Publish reviewed zones. A draft zone with target_zone_id is MERGED into that existing
    zone (its rules are added; the zone keeps its name, other rules and supervisor)."""
    try:
        zones = policy.validate_approved(req.zones)  # same order as req.zones
    except policy.PolicyError as e:
        raise HTTPException(422, str(e))
    approved_at = utc_now()
    out = []
    try:
        for raw, z in zip(req.zones, zones):
            target = str((raw or {}).get("target_zone_id") or "").strip()
            if target:
                existing = get_zone(target)
                if existing is None:
                    raise HTTPException(422, f'Zone "{target}" no longer exists - pick another zone to add rules to.')
                z = merge_zone(existing, z)
            z.update(policy_key=req.policy_key, approved_at=approved_at)
            out.append(z)
        ids = [z["zone_id"] for z in out]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Two zones in this policy publish to the same zone - merge them or rename one.")
        save_zones(out, req.replace_existing)
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    return {"ok": True, "zones": [{**z, "ai_rules": config.ai_rules(z)} for z in out]}


def merge_zone(existing, new):
    """Add a policy zone's rules to an existing zone without dropping what it already checks."""
    have_ai = {config.ai_key(u.get("item", "")) for u in existing.get("unmonitored") or []}
    return {
        **existing,
        "required": list(existing.get("required") or []) + [g for g in new["required"] if g not in (existing.get("required") or [])],
        "rules": {**(existing.get("rules") or {}), **(new.get("rules") or {})},
        "unmonitored": list(existing.get("unmonitored") or [])
        + [u for u in new.get("unmonitored") or [] if config.ai_key(u.get("item", "")) not in have_ai],
        "alert_email": existing.get("alert_email") or new.get("alert_email", ""),
    }


def utc_now():
    from tracker import utc_timestamp
    import time

    return utc_timestamp(time.time())


SAMPLE_POLICIES = {  # name -> (file in demo/, script that generates it)
    "brightwater": ("sample_safety_policy.pdf", "demo/make_sample_policy.py"),
    "new_rules": ("new_rules.pdf", "demo/make_new_rules.py"),
}


@app.get("/api/policy/sample")
def policy_sample(name: str = "brightwater"):
    if name not in SAMPLE_POLICIES:
        raise HTTPException(404, "unknown sample policy")
    file, script = SAMPLE_POLICIES[name]
    path = Path(__file__).parent / "demo" / file
    if not path.exists():
        raise HTTPException(404, f"run: python {script}")
    return FileResponse(path, media_type="application/pdf")


@app.get("/setup")
def setup_page():
    return FileResponse(STATIC_DIR / "setup.html")


APP_DIR = STATIC_DIR / "app"  # React UI built from frontend/ (npm run build)


@app.get("/")
def index():
    """The SafeSight app (React build). Falls back to the classic page if it isn't built."""
    if (APP_DIR / "index.html").exists():
        return FileResponse(APP_DIR / "index.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/classic")
def classic():
    """Original single-page UI, kept as a backup for demos."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/app", StaticFiles(directory=APP_DIR, check_dir=False), name="app")
