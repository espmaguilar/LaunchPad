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
LENIENT_ADVANCE = True
DONE_MIN_CONFIDENCE = 0.5 if LENIENT_ADVANCE else MATCH_MIN_CONFIDENCE
DONE_CHECKS = 1 if LENIENT_ADVANCE else 2  # consecutive qualifying checks before advancing
MISTAKE_CHECKS = 2              # consecutive confident detections of the same mistake before flagging
SKIP_CHECKS = 2                 # consecutive "next_step_detected" before "Possible skipped step"
STUCK_SECONDS = 20              # on the same step this long -> the coach speaks its latest tip
TIP_REPEAT_SECONDS = 45         # ...and at most this often after that
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

# --- Voice (Amazon Polly) ---
POLLY_VOICE_ID = "Joanna"
POLLY_ENGINE = "neural"
MAX_VOICE_CHARS = 400


def missing_settings():
    """Names of required .env settings that are empty."""
    required = {"AWS_REGION": AWS_REGION, "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID, "S3_BUCKET": S3_BUCKET}
    return [k for k, v in required.items() if not v]
