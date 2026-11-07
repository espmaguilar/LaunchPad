"""Offline checks for the PPE parsing rules. No AWS, no server.
Run: python test_parser.py"""
from ppe import analyze, parse_person
import config

config.MOCK_MODE = True


def part(name, *covers):
    """Body part with gear. covers = (type, covers_body_part, confidence)."""
    return {
        "Name": name,
        "Confidence": 99,
        "EquipmentDetections": [
            {"Type": t, "Confidence": c, "CoversBodyPart": {"Value": v, "Confidence": 99}}
            for t, v, c in covers
        ],
    }


def person(*parts):
    return {"Id": 0, "BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.4}, "BodyParts": list(parts)}


REQ = ["HAND_COVER", "FACE_COVER"]

# 1. Fully compliant
p = parse_person(person(
    part("FACE", ("FACE_COVER", True, 95)),
    part("LEFT_HAND", ("HAND_COVER", True, 95)),
    part("RIGHT_HAND", ("HAND_COVER", True, 95)),
), REQ)
assert p["status"] == "ok" and p["missing"] == [], p

# 2. One glove off -> HAND_COVER missing
p = parse_person(person(
    part("FACE", ("FACE_COVER", True, 95)),
    part("LEFT_HAND", ("HAND_COVER", True, 95)),
    part("RIGHT_HAND"),
), REQ)
assert p["status"] == "violation" and p["missing"] == ["HAND_COVER"], p

# 3. Mask detected but pulled down (CoversBodyPart false) -> missing
p = parse_person(person(
    part("FACE", ("FACE_COVER", False, 95)),
    part("LEFT_HAND", ("HAND_COVER", True, 95)),
), REQ)
assert p["missing"] == ["FACE_COVER"], p

# 4. Low-confidence gear doesn't count
p = parse_person(person(
    part("FACE", ("FACE_COVER", True, 60)),
    part("LEFT_HAND", ("HAND_COVER", True, 95)),
), REQ)
assert p["missing"] == ["FACE_COVER"], p

# 5. Hands not visible -> unknown, not a violation
p = parse_person(person(part("FACE", ("FACE_COVER", True, 95))), REQ)
assert p["status"] == "unknown" and p["unknown"] == ["HAND_COVER"], p

# 6. Gear the zone doesn't require is ignored
p = parse_person(person(part("HEAD"), part("FACE", ("FACE_COVER", True, 95))), ["FACE_COVER"])
assert p["status"] == "ok", p

# 6b. Body-part confidence: a bare hand seen at 76% still counts (real webcam case);
#     a very uncertain "hand" (40%) is treated as not visible
def part_c(name, conf):
    return {"Name": name, "Confidence": conf, "EquipmentDetections": []}
p = parse_person(person(part("FACE", ("FACE_COVER", True, 95)), part_c("LEFT_HAND", 76.2)), REQ)
assert p["missing"] == ["HAND_COVER"], p
p = parse_person(person(part("FACE", ("FACE_COVER", True, 95)), part_c("LEFT_HAND", 40)), REQ)
assert p["unknown"] == ["HAND_COVER"], p

# 7. Mock response flows through the same parser
r = analyze(b"x", REQ)
assert len(r["persons"]) == 2 and r["persons"][0]["status"] == "ok", r

# 8. Background filter: small (far-away) people are ignored, not checked
config.MIN_PERSON_AREA = 0.05
r = analyze(b"x", REQ)              # mock persons are ~0.27 of the frame -> kept
assert len(r["persons"]) == 2 and r["ignored"] == [], r
import ppe
far = person(part("RIGHT_HAND"))    # box 0.3 x 0.4 = 0.12 -> kept
far["BoundingBox"] = {"Left": 0.1, "Top": 0.5, "Width": 0.14, "Height": 0.26}  # 0.036 -> ignored
orig = ppe.detect
ppe.detect = lambda img, req: {"Persons": [far]}
r = analyze(b"x", REQ)
assert r["persons"] == [] and len(r["ignored"]) == 1 and r["missing_gear"] == [], r
config.MIN_PERSON_AREA = 0
assert len(analyze(b"x", REQ)["persons"]) == 1   # 0 = check everyone
ppe.detect = orig

print("all parser checks passed")
