"""PPE detection: call Rekognition (or a mock) and turn the raw response into
per-person boxes + missing gear for a zone."""
import time

import config


def detect(image_bytes, required):
    """Return the raw Rekognition DetectProtectiveEquipment response."""
    if config.MOCK_MODE:
        return mock_response(required)
    from aws_clients import rekognition

    kwargs = {"Image": {"Bytes": image_bytes}}
    if required:  # a policy zone may have no camera-monitored gear
        kwargs["SummarizationAttributes"] = {
            "MinConfidence": config.MIN_CONFIDENCE,
            "RequiredEquipmentTypes": required,
        }
    return rekognition.detect_protective_equipment(**kwargs)


def _worn_gear(body_part):
    """Gear types that are actually covering this body part (CoversBodyPart.Value true)."""
    worn = set()
    for eq in body_part.get("EquipmentDetections", []):
        covers = eq.get("CoversBodyPart", {})
        if covers.get("Value") and eq.get("Confidence", 0) >= config.MIN_CONFIDENCE:
            worn.add(eq["Type"])
    return worn


def parse_person(person, required):
    """Classify each required gear type as worn / missing / unknown for one person.

    unknown = the body part wasn't visible (e.g. hands out of frame), so we
    don't call it a violation.
    """
    # For each gear type: list of booleans, one per visible body part it protects
    coverage = {g: [] for g in required}
    for part in person.get("BodyParts", []):
        if part.get("Confidence", 0) < config.BODY_PART_MIN_CONFIDENCE:
            continue
        gear = config.BODY_PART_TO_GEAR.get(part["Name"])
        if gear in coverage:
            coverage[gear].append(gear in _worn_gear(part))

    worn, missing, unknown = [], [], []
    for gear, parts in coverage.items():
        if not parts:
            unknown.append(gear)
        elif all(parts):
            worn.append(gear)
        else:  # e.g. one glove on, one hand bare -> still missing
            missing.append(gear)

    status = "violation" if missing else ("unknown" if unknown else "ok")
    box = person.get("BoundingBox", {})
    return {
        "id": person.get("Id"),
        "box": {
            "left": box.get("Left", 0),
            "top": box.get("Top", 0),
            "width": box.get("Width", 0),
            "height": box.get("Height", 0),
        },
        "status": status,
        "worn": worn,
        "missing": missing,
        "unknown": unknown,
    }


LAST_RAW = {}  # most recent raw Rekognition response, for /api/debug/last-detection


def analyze(image_bytes, required):
    raw = detect(image_bytes, required)
    LAST_RAW.clear()
    LAST_RAW.update(raw=raw, required=required)
    persons, ignored = [], []
    for p in raw.get("Persons", []):
        parsed = parse_person(p, required)
        b = parsed["box"]
        if b["width"] * b["height"] < config.MIN_PERSON_AREA:
            ignored.append({"id": parsed["id"], "box": b})  # background: not checked, not logged
        else:
            persons.append(parsed)
    missing_gear = sorted({g for p in persons for g in p["missing"]})
    return {"persons": persons, "ignored": ignored, "missing_gear": missing_gear}


# ---------------- Mock ----------------
MOCK_CYCLE_SECONDS = 24  # first half: person 2 violates; second half: everyone compliant


def _mock_part(name, gear_type, worn):
    eq = []
    if worn:
        eq.append({
            "Type": gear_type,
            "Confidence": 97.0,
            "CoversBodyPart": {"Confidence": 95.0, "Value": True},
            "BoundingBox": {"Left": 0, "Top": 0, "Width": 0.05, "Height": 0.05},
        })
    return {"Name": name, "Confidence": 99.0, "EquipmentDetections": eq}


def _mock_person(pid, box, missing_gear):
    parts = []
    for part_name, gear in config.BODY_PART_TO_GEAR.items():
        parts.append(_mock_part(part_name, gear, worn=gear not in missing_gear))
    return {"Id": pid, "Confidence": 99.0, "BoundingBox": box, "BodyParts": parts}


def mock_response(required):
    """Fake response in the exact shape Rekognition returns.

    Person 2 is missing the zone's first required gear for 12s, then puts it on,
    so you can watch the boxes flip red -> green (and later test debounce/resolve).
    """
    violating = (time.time() % MOCK_CYCLE_SECONDS) < MOCK_CYCLE_SECONDS / 2
    p2_missing = {required[0]} if (violating and required) else set()
    return {
        "Persons": [
            _mock_person(0, {"Left": 0.06, "Top": 0.12, "Width": 0.34, "Height": 0.82}, set()),
            _mock_person(1, {"Left": 0.56, "Top": 0.16, "Width": 0.34, "Height": 0.78}, p2_missing),
        ]
    }
