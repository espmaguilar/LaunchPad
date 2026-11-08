"""LabScribe API: an AI lab partner.

Phase A: procedure ingestion
  lab manual PDF -> S3 -> Textract (pypdf fallback) -> Bedrock steps JSON -> review -> DynamoDB
Phase B: the Scribe
  camera (latest 2 frames every 3 s) -> Bedrock vision -> timestamped action transcript
Phase C: step matcher + mistake feedback + voice guidance (2 consecutive confident checks)
Later phases add PPE checks, persisted deviations and the lab record.

Run: uvicorn main:app --reload --reload-include .env
"""
import base64
import binascii
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import coach
import config
import ingest
import procedures
import sessions
import settings
import store
import vision
import voice

STATIC_DIR = Path(__file__).parent / "static"
APP_DIR = STATIC_DIR / "app"  # React UI built from frontend/ (npm run build)
DEMO_MANUAL = Path(__file__).parent / "demo" / "color_change_lab.pdf"
MAX_TEXT_CHARS = 60_000


@asynccontextmanager
async def lifespan(app):
    settings.init()  # apply saved Settings-page values onto config
    yield


app = FastAPI(title="LabScribe", lifespan=lifespan)


def aws_error(e):
    """Friendly message for any AWS failure."""
    if isinstance(e, store.TableMissing):
        return HTTPException(503, str(e))
    if isinstance(e, ClientError):
        code = e.response["Error"]["Code"]
        msg = e.response["Error"].get("Message", "")
        return HTTPException(502, f"AWS error ({code}): {msg}")
    return HTTPException(502, f"Could not reach AWS: {e}")


AWS_ERRORS = (ClientError, BotoCoreError, store.TableMissing)


# ---------- health ----------
@app.get("/api/health")
def health():
    missing = config.missing_settings()
    return {
        "ok": config.MOCK_MODE or not missing,
        "app": "LabScribe",
        "mock_mode": config.MOCK_MODE,
        "region": config.AWS_REGION,
        "model": config.BEDROCK_MODEL_ID,
        "vision_model": vision.active_model(),
        "voice": voice.provider(),
        "advance_mode": "lenient" if config.LENIENT_ADVANCE else config.ADVANCE_MODE,
        "scribe": {"interval": config.SCRIBE_INTERVAL_SECONDS, "frame_gap": config.SCRIBE_FRAME_GAP_SECONDS,
                   "max_width": config.SCRIBE_FRAME_MAX_WIDTH},
        "missing_settings": missing,
    }


# ---------- procedure ingestion (Phase A) ----------
class ManualUpload(BaseModel):
    filename: str
    data: str  # base64 PDF, with or without a data: prefix


@app.post("/api/procedures/upload")
def upload_manual(req: ManualUpload):
    """PDF -> S3 -> text -> Bedrock steps. Returns an editable draft; nothing is saved yet."""
    b64 = req.data.split(",", 1)[1] if req.data.startswith("data:") else req.data
    try:
        pdf = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "file is not valid base64")
    if not pdf.startswith(b"%PDF"):
        raise HTTPException(400, "that file is not a PDF")
    if len(pdf) > config.MAX_PDF_BYTES:
        raise HTTPException(413, "PDF larger than 10 MB")
    try:
        key = ingest.store_pdf(pdf, config.MANUALS_PREFIX)
        text, method, warning = ingest.extract_text(pdf, key)
    except AWS_ERRORS as e:
        raise aws_error(e)
    except ingest.IngestError as e:
        raise HTTPException(422, str(e))
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(413, f"manual text is {len(text):,} characters; the limit is {MAX_TEXT_CHARS:,}. "
                                 "Upload just the procedure section.")

    warnings = [warning] if warning else []
    source = None
    if len(text.strip()) < config.MIN_MANUAL_CHARS:
        warnings.append(f"This PDF has almost no text ({len(text.strip())} characters) - it looks like a diagram "
                        "or figure, not a written procedure. Upload the lab manual itself, or add the steps below.")
    try:
        raw, source = procedures.ai_extract(text)
    except procedures.NoStepsFound:
        raw = {"title": Path(req.filename).stem, "steps": []}
        if len(text.strip()) >= config.MIN_MANUAL_CHARS:  # the low-text warning above already explains it
            warnings.append("No procedure steps were found in this document. Add them below, or upload the lab "
                            "manual that contains the instructions.")
    except procedures.ProcedureError as e:
        try:
            raw = procedures.heuristic_extract(text)
            warnings.append(f"{e}. Steps were read from the numbered list instead: add each step's "
                            "expected action and visual cues before approving.")
        except procedures.ProcedureError:
            raw = {"title": Path(req.filename).stem, "steps": []}
            warnings.append(f"{e}, and no numbered steps were found. Add the steps below.")
    draft = procedures.normalize(raw)
    return {"source_key": key, "filename": req.filename, "extraction": method, "text_chars": len(text),
            "text_preview": text[:1500], "generated_by": source, "warnings": warnings, **draft}


class ProcedureApprove(BaseModel):
    procedure_id: str | None = None  # set when editing an existing procedure
    source_key: str = ""
    title: str
    steps: list


@app.post("/api/procedures/approve")
def approve_procedure(req: ProcedureApprove):
    try:
        body = procedures.validate({"title": req.title, "steps": req.steps})
    except procedures.ProcedureError as e:
        raise HTTPException(422, str(e))
    now = ingest.utc_timestamp()
    try:
        existing = store.get_procedure(req.procedure_id) if req.procedure_id else None
        proc = {
            "procedure_id": existing["procedure_id"] if existing else f"{procedures.slug(body['title'])}-{secrets.token_hex(2)}",
            "title": body["title"],
            "steps": body["steps"],
            "step_count": len(body["steps"]),
            "safety_step_count": sum(1 for s in body["steps"] if s["safety_critical"]),
            "source_key": req.source_key or (existing or {}).get("source_key", ""),
            "created_at": (existing or {}).get("created_at", now),
            "approved_at": now,
        }
        store.save_procedure(proc)
    except AWS_ERRORS as e:
        raise aws_error(e)
    return proc


@app.get("/api/procedures")
def list_procedures():
    try:
        items = store.list_procedures()
    except AWS_ERRORS as e:
        raise aws_error(e)
    keys = ("procedure_id", "title", "step_count", "safety_step_count", "approved_at", "created_at")
    return [{k: p.get(k) for k in keys} for p in items]


@app.get("/api/procedures/sample")
def sample_manual():
    if not DEMO_MANUAL.exists():
        raise HTTPException(404, "run: python demo/make_lab_manual.py")
    return FileResponse(DEMO_MANUAL, media_type="application/pdf", filename=DEMO_MANUAL.name)


@app.get("/api/procedures/{procedure_id}")
def get_procedure(procedure_id: str):
    try:
        proc = store.get_procedure(procedure_id)
    except AWS_ERRORS as e:
        raise aws_error(e)
    if proc is None:
        raise HTTPException(404, "procedure not found")
    return proc


@app.delete("/api/procedures/{procedure_id}")
def delete_procedure(procedure_id: str):
    try:
        store.delete_procedure(procedure_id)
    except AWS_ERRORS as e:
        raise aws_error(e)
    return {"ok": True}


# ---------- lab sessions + scribe (Phase B) ----------
class SessionStart(BaseModel):
    procedure_id: str


@app.post("/api/sessions")
def start_session(req: SessionStart):
    try:
        proc = store.get_procedure(req.procedure_id)
    except AWS_ERRORS as e:
        raise aws_error(e)
    if proc is None:
        raise HTTPException(404, "procedure not found")
    return sessions.public(sessions.start(proc))


@app.get("/api/sessions")
def list_sessions():
    """Summaries of sessions in memory (no transcripts). Lab Records + Instructor View."""
    out = []
    for s in sessions.list_all():
        v = sessions.public(s, since=10**9)
        v.pop("transcript", None)
        out.append(v)
    return out


def _session_or_404(sid):
    try:
        return sessions.get(sid)
    except sessions.SessionError as e:
        raise HTTPException(404, str(e))


@app.get("/api/sessions/{sid}")
def session_state(sid: str, since: int = 0):
    return sessions.public(_session_or_404(sid), since)


class Frames(BaseModel):
    frames: list[str] = []  # 1-2 JPEG data URLs, oldest first
    motion: str = ""        # browser hand-tracking summary (MediaPipe), optional


@app.post("/api/sessions/{sid}/scribe")
def scribe_frames(sid: str, req: Frames):
    """Latest frames -> one transcript line. {skipped: true} if the previous call is still running."""
    _session_or_404(sid)
    if len(req.frames) > 2:
        raise HTTPException(422, "send at most 2 frames")
    frames = []
    for f in req.frames:
        try:
            data = base64.b64decode(f.split(",", 1)[1] if f.startswith("data:") else f, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "frame is not valid base64")
        if len(data) > config.MAX_FRAME_BYTES:
            raise HTTPException(413, "frame larger than 2 MB")
        frames.append(data)
    if not frames and not config.MOCK_MODE:
        raise HTTPException(422, "no frames")
    try:
        return sessions.scribe_frames(sid, frames, motion=req.motion[:400])
    except sessions.SessionError as e:
        raise HTTPException(409, str(e))


class StepClick(BaseModel):
    step: int | None = None   # the step number the student saw (0 = all done); a stale click changes nothing


@app.post("/api/sessions/{sid}/steps/done")
def mark_step_done(sid: str, req: StepClick | None = None):
    """Manual override: the current step is done."""
    _session_or_404(sid)
    try:
        return sessions.mark_done(sid, step=req.step if req else None)
    except sessions.SessionError as e:
        raise HTTPException(409, str(e))


@app.post("/api/sessions/{sid}/steps/restart")
def restart_step(sid: str, req: StepClick | None = None):
    """Start the current step over (earlier camera lines stop counting for it)."""
    _session_or_404(sid)
    try:
        return sessions.restart_step(sid, step=req.step if req else None)
    except sessions.SessionError as e:
        raise HTTPException(409, str(e))


@app.post("/api/sessions/{sid}/steps/undo")
def undo_step(sid: str, req: StepClick | None = None):
    """Manual override: re-open the last completed step."""
    _session_or_404(sid)
    try:
        return sessions.undo(sid, step=req.step if req else None)
    except sessions.SessionError as e:
        raise HTTPException(409, str(e))


@app.post("/api/sessions/{sid}/feedback/{fid}/dismiss")
def dismiss_feedback(sid: str, fid: str):
    _session_or_404(sid)
    try:
        return sessions.dismiss(sid, fid)
    except sessions.SessionError as e:
        raise HTTPException(404, str(e))


@app.get("/api/sessions/{sid}/snapshots/{snap_id}")
def session_snapshot(sid: str, snap_id: str):
    _session_or_404(sid)
    data = sessions.snapshot(sid, snap_id)
    if data is None:
        raise HTTPException(404, "snapshot not found")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


class Question(BaseModel):
    question: str


@app.post("/api/sessions/{sid}/ask")
def ask_coach(sid: str, req: Question):
    """Ask LabScribe: a short answer using the procedure, current step and what the camera saw."""
    s = _session_or_404(sid)
    try:
        text, model = coach.answer(s, req.question)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)
    return {"answer": text, "model": model}


class Confused(BaseModel):
    yes: bool


@app.post("/api/sessions/{sid}/confused")
def confused(sid: str, req: Confused):
    """'Feeling confused?' answer. yes -> the current step is split into checked sub-steps."""
    _session_or_404(sid)
    try:
        return sessions.answer_confused(sid, req.yes)
    except sessions.SessionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except (ClientError, BotoCoreError) as e:
        raise aws_error(e)


@app.post("/api/sessions/{sid}/substeps/exit")
def substeps_exit(sid: str):
    _session_or_404(sid)
    return sessions.exit_breakdown(sid)


@app.post("/api/sessions/{sid}/substeps/done")
def substep_done(sid: str, req: StepClick | None = None):
    _session_or_404(sid)
    try:
        return sessions.mark_substep_done(sid, step=req.step if req else None)
    except sessions.SessionError as e:
        raise HTTPException(409, str(e))


@app.post("/api/sessions/{sid}/end")
def end_session(sid: str):
    _session_or_404(sid)
    return sessions.public(sessions.end(sid))


# ---------- voice ----------
@app.get("/api/voice")
def speak(text: str):
    """Polly MP3 for a guidance phrase (cached). 503 -> the browser speaks it instead."""
    try:
        audio, _ = voice.mp3_for(text)
    except voice.VoiceUnavailable as e:
        raise HTTPException(503, f"voice unavailable: {e}")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "max-age=86400"})


# ---------- settings ----------
@app.get("/api/settings")
def get_settings():
    return {"settings": settings.current(), "polly_voices": settings.POLLY_VOICES}


@app.put("/api/settings")
def put_settings(body: dict):
    try:
        return {"settings": settings.save(body)}
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))


# ---------- UI ----------
@app.get("/")
def index():
    if not (APP_DIR / "index.html").exists():
        raise HTTPException(503, "UI not built: cd frontend && npm install && npm run build")
    return FileResponse(APP_DIR / "index.html")


app.mount("/app", StaticFiles(directory=APP_DIR, check_dir=False), name="app")
