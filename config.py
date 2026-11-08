"""LabScribe configuration. Everything tunable lives here or in .env."""
import os

from dotenv import load_dotenv

load_dotenv()


def _env(name, default=""):
    """Read a setting, tolerating stray quotes/brackets/spaces pasted into .env."""
    return os.getenv(name, default).strip().strip("<>\"' ")


# --- AWS settings (from .env, never hardcode) ---
AWS_REGION = _env("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = _env("BEDROCK_MODEL_ID")
S3_BUCKET = _env("S3_BUCKET")

# MOCK_MODE=true -> no AWS calls: local files in mock_data/, heuristic step extraction,
# mock scribe lines (later phases).
MOCK_MODE = os.getenv("MOCK_MODE", "false").strip().lower() in ("1", "true", "yes")

# --- Storage ---
PROCEDURES_TABLE = "labscribe-procedures"   # PK procedure_id
MANUALS_PREFIX = "labscribe/manuals/"       # S3: uploaded lab manual PDFs
MAX_PDF_BYTES = 10 * 1024 * 1024

# --- Procedure ingestion ---
MAX_STEPS = 40
MAX_INSTRUCTION_CHARS = 300
MAX_EXPECTED_ACTION_CHARS = 120
MIN_MANUAL_CHARS = 300          # less text than this -> probably a diagram/scan, not a procedure
# PPE names the vision check understands (free text is allowed too)
COMMON_PPE = ["safety goggles", "gloves", "lab coat", "face mask"]

# --- Scribe (Phase B) ---
SCRIBE_INTERVAL_SECONDS = 3     # browser sends the latest frames this often
SCRIBE_FRAME_GAP_SECONDS = 1    # the 2 frames sent are this far apart (motion context)
SCRIBE_FRAME_MAX_WIDTH = 640    # px; downscaled in the browser before upload
MAX_FRAME_BYTES = 2 * 1024 * 1024

# --- Step matcher (Phase C) ---
MATCH_WINDOW_SECONDS = 15       # transcript the matcher sees (and never before the current step started)
MATCH_MIN_CONFIDENCE = 0.7      # below this a mistake / skip check doesn't count
# LENIENT_ADVANCE: move on as soon as the student does anything resembling the current step
# ("in_progress", "done", or already doing the next step) on ONE check at DONE_MIN_CONFIDENCE.
# False = strict: only "done" >= MATCH_MIN_CONFIDENCE on DONE_CHECKS consecutive checks.
# ADVANCE_MODE (default "instant", user: "when a human does something it's instant"): ONE matcher check that
# resembles the step (done / in_progress / next_step_detected >= DONE_MIN_CONFIDENCE) advances immediately, as long
# as a transcript line since the step started names one of the step's KEY OBJECTS (so "lifting hands" can never tick
# "put on headphones"). "careful" = 2 checks + vision finish check + MIN_STEP_SECONDS; "lenient"/"strict" = older modes.
ADVANCE_MODE = _env("ADVANCE_MODE", "instant").lower()
SAVE_SESSIONS = _env("SAVE_SESSIONS", "true").lower() == "true"
# off-track buffer: the newest line must show ANOTHER step's action on 2 checks in a row, >= this many seconds apart
OFF_TRACK_SECONDS = float(_env("OFF_TRACK_SECONDS", "6"))   # session_logs/ for replay tests
# instant mode's floor for the matcher's number: the key-object line is the real gate. Llama reported "in_progress 0.4"
# for "Holding up a stuffed animal" on "Place the stuffed animal..." (its number meant "not finished", not "unsure"),
# which blocked correct matches for 30 s at 0.55.
INSTANT_MIN_CONFIDENCE = float(_env("INSTANT_MIN_CONFIDENCE", "0.3"))
# careful mode (was the default) (user: "things are being done in haste" - hands lifted high ticked "put on headphones").
LENIENT_ADVANCE = _env("LENIENT_ADVANCE", "false").lower() == "true"
DONE_MIN_CONFIDENCE = 0.5 if LENIENT_ADVANCE else 0.55   # matcher "done" this sure counts toward passing
DONE_CHECKS = 1 if LENIENT_ADVANCE else 2  # consecutive qualifying checks before advancing
# careful mode: a step can't complete sooner than this after it started, and each "done" must be confirmed by a
# strict vision check of the latest frame (matcher.finished_check: key object visibly in its finished state)
MIN_STEP_SECONDS = float(_env("MIN_STEP_SECONDS", "5"))
CONFIRM_WITH_VISION = _env("CONFIRM_WITH_VISION", "true").lower() == "true"
FINISH_MIN_CONFIDENCE = 0.55       # vision finish check must say done at least this sure
IN_PROGRESS_MIN_CONFIDENCE = 0.55  # matcher "in_progress" this sure is also sent to the vision finish check
MISTAKE_CHECKS = 2              # consecutive confident detections of the same mistake before flagging
SKIP_CHECKS = 2                 # consecutive "next_step_detected" before "Possible skipped step"
CONFUSED_AFTER_SECONDS = int(_env("CONFUSED_AFTER_SECONDS", "25"))  # on a camera step this long -> "Feeling confused?"
MAX_SUBSTEPS = 5                # a confused step is split into 2..MAX_SUBSTEPS sub-steps
SUBSTEP_MIN_CONFIDENCE = 0.5    # focused check confidence needed to tick a sub-step
CHECK_HISTORY = 40              # matcher results kept per session (debugging + lab record)

# --- Vision model (scribe + PPE checks) ---
# BEDROCK_MODEL_ID must accept images; if it doesn't, this fallback is used.
VISION_FALLBACK_MODEL_ID = _env("VISION_FALLBACK_MODEL_ID", "us.amazon.nova-lite-v1:0")

# --- Background filter (Rekognition person boxes) ---
# Ignore people whose box covers less than this fraction of the frame (0 = everyone).
# Crowded demo room: ~0.05 ignores people far in the background.
try:
    MIN_PERSON_AREA = max(0.0, float(_env("MIN_PERSON_AREA", "0") or 0))
except ValueError:
    MIN_PERSON_AREA = 0.0
# Rekognition detect_protective_equipment internals used by ppe.py (person detection)
GEAR_TYPES = ["FACE_COVER", "HAND_COVER", "HEAD_COVER"]
MIN_CONFIDENCE = 80
BODY_PART_MIN_CONFIDENCE = 60
BODY_PART_TO_GEAR = {"FACE": "FACE_COVER", "HEAD": "HEAD_COVER", "LEFT_HAND": "HAND_COVER", "RIGHT_HAND": "HAND_COVER"}
GEAR_LABELS = {"FACE_COVER": "face cover", "HAND_COVER": "gloves", "HEAD_COVER": "head protection"}

# --- Voice ---
# ElevenLabs is used when ELEVENLABS_API_KEY is set (voice ELEVENLABS_VOICE_ID); otherwise - or if an
# ElevenLabs call fails - Amazon Polly. The key lives in .env only, never in code or git.
ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _env("ELEVENLABS_VOICE_ID", "dmWfLQZJq7wNPv9WmtiM")
ELEVENLABS_MODEL_ID = _env("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")   # low latency, good for live coaching
POLLY_VOICE_ID = "Joanna"
POLLY_ENGINE = "neural"
MAX_VOICE_CHARS = 400


def missing_settings():
    """Names of required .env settings that are empty."""
    required = {"AWS_REGION": AWS_REGION, "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID, "S3_BUCKET": S3_BUCKET}
    return [k for k, v in required.items() if not v]
