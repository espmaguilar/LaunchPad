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



# --- AI-vision rules (Bedrock looks at the frame) ---
# Policy items Rekognition can't check (goggles, vests, "no phones"...) are stored in
# zone["unmonitored"]. They are checked by a Bedrock vision model every AI_CHECK_SECONDS
# and tracked under keys like "AI:no-phones" in the same tracker pipeline.
AI_PREFIX = "AI:"
AI_CHECK_SECONDS = 10          # one vision check per active zone this often
AI_DEBOUNCE_CHECKS = 2         # consecutive positive checks before an incident
AI_MIN_CONFIDENCE = 0.6        # below this a "violated" answer is treated as unsure
AI_DEFAULT_FINE = 0            # AI rule with no fine stated
VISION_FALLBACK_MODEL_ID = _env("VISION_FALLBACK_MODEL_ID", "us.amazon.nova-lite-v1:0")


def is_ai(gear):
    return str(gear).startswith(AI_PREFIX)


def ai_key(item):
    import re
    return AI_PREFIX + (re.sub(r"[^a-z0-9]+", "-", str(item).lower()).strip("-")[:40] or "rule")


def ai_rules(zone):
    """[{key, item, rule, severity, fine, source}] for a zone (or tracker meta)."""
    out, seen = [], set()
    for u in (zone or {}).get("unmonitored") or []:
        if not u.get("item"):
            continue
        key = ai_key(u["item"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**u, "key": key})
    return out


def gear_label(gear, zone=None):
    """Human label for a Rekognition gear type or an AI rule key."""
    if gear in GEAR_LABELS:
        return GEAR_LABELS[gear]
    for r in ai_rules(zone):
        if r["key"] == gear:
            return r["item"]
    return str(gear)[len(AI_PREFIX):].replace("-", " ") if is_ai(gear) else str(gear)


def detector(gear):
    return "AI vision" if is_ai(gear) else "Rekognition"


def rule_for(zone, gear):
    """Rule/severity/fine for a gear type (or AI rule key) in a zone.

    Zones approved from a policy PDF carry their own rules (zone["rules"][gear]);
    otherwise - or for any field the policy didn't state - the OSHA table applies.
    AI rules come from zone["unmonitored"]; with no fine stated they use AI_DEFAULT_FINE.
    `zone` can be a zone record or the tracker's meta dict.
    """
    if is_ai(gear):
        r = next((r for r in ai_rules(zone) if r["key"] == gear), {})
        fine = r.get("fine")
        return {"label": r.get("rule") or r.get("item") or gear_label(gear, zone),
                "severity": r.get("severity") or "Other-than-serious",
                "fine": int(fine) if fine not in (None, "") else AI_DEFAULT_FINE,
                "source": r.get("source") or ("policy" if fine not in (None, "") else "no fine set")}
    osha = OSHA_RULES[gear]
    custom = ((zone or {}).get("rules") or {}).get(gear) or {}
    return {
        "label": custom.get("rule") or f"{osha['rule']} {osha['title']}",
        "severity": custom.get("severity") or osha["severity"],
        "fine": int(custom["fine"]) if custom.get("fine") not in (None, "") else osha["fine"],
        "source": custom.get("source") or "OSHA default",
    }


# --- Smart logic ---
# Timeline for one missing gear item (continuous non-compliance):
#   0s  first frame where someone is missing it
#   20s still missing        -> log the incident + VOICE warning on site (no message)
#   30s still missing        -> ONE supervisor notification (email now; SMS/Twilio later)
# A frame where the gear is visibly worn restarts the clock. Frames with no
# evidence (person stepped out, hands hidden) don't restart it, unless no miss
# has been seen for MISS_GAP_SECONDS.
# DEMO_MODE only shortens the resolve confirmation; the 20s / 30s thresholds are the same.
DEMO_MODE = _env("DEMO_MODE", "false").lower() in ("1", "true", "yes")

VOICE_AFTER_SECONDS = int(_env("VOICE_AFTER_SECONDS", "20"))    # continuous miss -> incident + voice
NOTIFY_AFTER_SECONDS = int(_env("NOTIFY_AFTER_SECONDS", "30"))  # continuous miss -> supervisor notified
MISS_GAP_SECONDS = 6           # no miss seen this long (no evidence either way) -> streak restarts
RESOLVE_SECONDS = 10 if DEMO_MODE else 30        # hysteresis: continuous compliance needed to resolve
RESOLVE_WINDOW_SECONDS = 300   # fixed within this long after logging -> counts as "fine avoided"
EXPIRE_SECONDS = 600           # unresolved this long -> stop tracking (stays OPEN); a new streak = new incident
ZONE_STALE_SECONDS = 10        # no frames from a zone this long -> pause its timers ("camera offline")
TICK_SECONDS = 1               # background notification check interval

# Supervisor notification channel. "email" = SNS topic. "sms" (Twilio) is not wired yet.
NOTIFY_CHANNEL = _env("NOTIFY_CHANNEL", "email").lower()

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
