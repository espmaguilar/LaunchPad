"""SafeSight configuration. Everything tunable lives here or in .env."""
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
SNS_TOPIC_ARN = _env("SNS_TOPIC_ARN")
ALERT_EMAIL = _env("ALERT_EMAIL")

# MOCK_MODE=true -> fake Rekognition responses + default zones, no AWS calls.
MOCK_MODE = os.getenv("MOCK_MODE", "false").strip().lower() in ("1", "true", "yes")

ZONES_TABLE = "safesight-zones"
VIOLATIONS_TABLE = "safesight-violations"
SNS_TOPIC_NAME = "safesight-alerts"

# --- Rekognition PPE ---
# Rekognition PPE detects ONLY these three types. No goggles, no vests.
GEAR_TYPES = ["FACE_COVER", "HAND_COVER", "HEAD_COVER"]
MIN_CONFIDENCE = 80             # gear detection (passed to Rekognition + checked per item)
# Body parts (is that a hand?) use a looser bar: hands close to the camera or partly
# out of frame often score 60-80. Too high -> bare hands read as "not visible".
BODY_PART_MIN_CONFIDENCE = 60

# Ignore people whose box covers less than this fraction of the frame (0 = check everyone).
# Real deployment: 0, the camera's view IS the zone. Crowded demo room: ~0.05 ignores
# people far in the background (a seated person ~2 m behind measured ~0.04).
try:
    MIN_PERSON_AREA = max(0.0, float(_env("MIN_PERSON_AREA", "0") or 0))
except ValueError:
    MIN_PERSON_AREA = 0.0

# Which gear type protects which body part.
BODY_PART_TO_GEAR = {
    "FACE": "FACE_COVER",
    "HEAD": "HEAD_COVER",
    "LEFT_HAND": "HAND_COVER",
    "RIGHT_HAND": "HAND_COVER",
}

GEAR_LABELS = {
    "FACE_COVER": "face cover",
    "HAND_COVER": "gloves",
    "HEAD_COVER": "head protection",
}

# --- OSHA rules table ---
# TODO: verify current penalty amounts on osha.gov before demo.
# Wording everywhere must be "potential fine exposure", never "you will be fined".
OSHA_RULES = {
    "HAND_COVER": {
        "rule": "29 CFR 1910.138",
        "title": "Hand Protection",
        "severity": "Serious",
        "fine": 16550,
    },
    "HEAD_COVER": {
        "rule": "29 CFR 1910.135",
        "title": "Head Protection",
        "severity": "Serious",
        "fine": 16550,
    },
    "FACE_COVER": {
        "rule": "29 CFR 1910.134",
        "title": "Respiratory Protection",
        "severity": "Serious",
        "fine": 16550,
    },
}



def rule_for(zone, gear):
    """Rule/severity/fine for a gear type in a zone.

    Zones approved from a policy PDF carry their own rules (zone["rules"][gear]);
    otherwise - or for any field the policy didn't state - the OSHA table applies.
    `zone` can be a zone record or the tracker's meta dict (both may have "rules").
    """
    osha = OSHA_RULES[gear]
    custom = ((zone or {}).get("rules") or {}).get(gear) or {}
    return {
        "label": custom.get("rule") or f"{osha['rule']} {osha['title']}",
        "severity": custom.get("severity") or osha["severity"],
        "fine": int(custom["fine"]) if custom.get("fine") not in (None, "") else osha["fine"],
        "source": custom.get("source") or "OSHA default",
    }


# --- Smart logic ---
# DEMO_MODE=true in .env shortens the escalation timers so a pitch can show every tier.
DEMO_MODE = _env("DEMO_MODE", "false").lower() in ("1", "true", "yes")

DEBOUNCE_FRAMES = 3            # same gear missing N frames in a row -> violation + voice warning
VOICE_REPEAT_SECONDS = 10      # still missing this long after the warning -> say it once more
VOICE_MAX_PLAYS = 2            # voice plays per violation (initial + 1 repeat)
ALERT_GRACE_SECONDS = 20 if DEMO_MODE else 120   # Tier 2: still OPEN -> one supervisor email
ESCALATION_SECONDS = 60 if DEMO_MODE else 600    # Tier 3: still OPEN -> one escalation email
RESOLVE_SECONDS = 10 if DEMO_MODE else 30        # hysteresis: continuous compliance needed to resolve
RESOLVE_WINDOW_SECONDS = 300   # fixed within this long after logging -> counts as "fine avoided"
ZONE_STALE_SECONDS = 10        # no frames from a zone this long -> pause its email timers ("camera offline")
TICK_SECONDS = 1               # background escalation check interval

# --- Voice (Amazon Polly) ---
POLLY_VOICE_ID = "Joanna"
POLLY_ENGINE = "neural"
VOICE_TEXT = "Attention: {gear} required in the {zone}."

# --- Default zones (written to DynamoDB by setup_aws.py) ---
DEFAULT_ZONES = [
    {"zone_id": "chem-lab", "name": "Chem Lab", "required": ["HAND_COVER", "FACE_COVER"]},
    {"zone_id": "construction", "name": "Construction Site", "required": ["HEAD_COVER", "HAND_COVER"]},
    {"zone_id": "clean-room", "name": "Clean Room", "required": ["FACE_COVER", "HEAD_COVER", "HAND_COVER"]},
]


def missing_settings():
    """Names of required .env settings that are empty."""
    required = {
        "AWS_REGION": AWS_REGION,
        "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
        "S3_BUCKET": S3_BUCKET,
        "SNS_TOPIC_ARN": SNS_TOPIC_ARN,
        "ALERT_EMAIL": ALERT_EMAIL,
    }
    return [k for k, v in required.items() if not v]
