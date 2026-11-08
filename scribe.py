"""The Scribe: camera frames -> one short, factual sentence about the physical action.

Every SCRIBE_INTERVAL_SECONDS the browser sends the latest 2 frames (~1 s apart, so the
model can see motion). Bedrock vision answers strict JSON {action, objects[]}. The prompt
forbids speculation: what the hands do and which objects are involved, nothing else.

MOCK_MODE: a scripted transcript walks through the demo procedure (Color-Change Reaction),
so the session UI - and later the step matcher - can be tested without AWS.
"""
import re
import time

import config
import vision

SYSTEM_PROMPT = """You are a lab scribe. You get 2 consecutive frames (about 1 second apart) from a camera \
pointed at a lab bench. Describe ONLY the physical action happening and the objects involved.

Rules:
- One short sentence, present tense, at most 15 words, e.g. "Pouring water from a bottle into a clear cup."
- Describe what the hands/person physically do. No intent, no guesses about why, no step numbers.
- Name objects by what they look like ("clear cup", "small white bottle", "blue liquid", "spoon").
- Compare the two frames: if something moves or changes (pouring, stirring, a color appearing), say so.
- If nothing is happening, action = "No visible action" and still list visible lab objects.
- Ignore people in the background; describe only the person working at the bench closest to the camera.

Return ONLY ONE JSON object for BOTH frames together (describe the change between them), no prose,
no markdown, not one object per frame:
{"action": "<sentence>", "objects": ["<object>", ...]}"""

# Mock transcript for the demo manual (Color-Change Reaction), one line per scribe tick; loops.
# (action, objects, tag). tag tells the mock matcher which demo step the line belongs to.
# Each step is shown on 2 consecutive ticks (the matcher needs 2 confident checks to advance),
# and there's one deliberate mistake - vinegar while step 3 (food coloring) is current - so the
# feedback flow can be demonstrated offline.
_VINEGAR_EARLY = {"kind": "mistake", "during": 3, "mistake_type": "wrong_order",
                  "what_went_wrong": "You poured vinegar before adding the food coloring.",
                  "how_to_fix": "Step 3 says to add 3 drops of blue food coloring first."}
MOCK_SCRIPT = [
    ("No visible action", ["clear cup", "safety goggles", "spoon"], None),
    ("Picking up safety goggles from the bench", ["safety goggles"], {"step": 1, "kind": "progress"}),
    ("Putting safety goggles on over the eyes", ["safety goggles"], {"step": 1, "kind": "done"}),
    ("Wearing safety goggles with hands on the bench", ["safety goggles"], {"step": 1, "kind": "done"}),
    ("Pouring water from a bottle into a clear cup", ["water bottle", "clear cup"], {"step": 2, "kind": "done"}),
    ("Water now fills the clear cup about halfway", ["clear cup", "water"], {"step": 2, "kind": "done"}),
    ("Tilting a bottle of clear liquid over the cup", ["vinegar bottle", "clear cup"], _VINEGAR_EARLY),
    ("Pouring clear liquid from a bottle into the cup", ["vinegar bottle", "clear cup"], _VINEGAR_EARLY),
    ("Squeezing drops of blue liquid into the cup", ["blue food coloring bottle", "clear cup"], {"step": 3, "kind": "done"}),
    ("Stirring the blue water with a spoon", ["spoon", "clear cup", "blue liquid"], {"step": 3, "kind": "done"}),
    ("Scooping white powder with a measuring spoon", ["measuring spoon", "white powder"], {"step": 4, "kind": "progress"}),
    ("Adding white powder to the blue water and stirring", ["measuring spoon", "clear cup"], {"step": 4, "kind": "done"}),
    ("Stirring until the white powder dissolves", ["spoon", "clear cup"], {"step": 4, "kind": "done"}),
    ("Slowly pouring vinegar from a measuring spoon into the cup", ["measuring spoon", "clear cup"], {"step": 5, "kind": "done"}),
    ("Blue foam bubbling up inside the cup", ["clear cup", "blue foam"], {"step": 5, "kind": "done"}),
    ("Pouring the cup's contents into the sink", ["clear cup", "sink"], {"step": 6, "kind": "done"}),
    ("Rinsing the empty cup under the tap", ["clear cup", "tap"], {"step": 6, "kind": "done"}),
]


class ScribeError(Exception):
    pass


def normalize(data):
    """Model JSON -> {action, objects}. Raises ScribeError on unusable output."""
    if not isinstance(data, dict):
        raise ScribeError("reply is not a JSON object")
    action = re.sub(r"\s+", " ", str(data.get("action") or "")).strip().strip('"')
    if not action:
        raise ScribeError("reply has no action")
    words = action.split()
    if len(words) > 25:  # model ignored the length rule: keep it a transcript line
        action = " ".join(words[:25]) + "…"
    if action[-1] not in ".!?…":
        action += "."
    objects = []
    for o in data.get("objects") or []:
        o = str(o).strip().lower()[:40]
        if o and o not in objects:
            objects.append(o)
    return {"action": action[0].upper() + action[1:], "objects": objects[:8]}


def pick(objs):
    """Choose the line to log when the model returns several objects (e.g. one per frame):
    the first that shows an actual action, else the first usable one."""
    lines = []
    for o in objs:
        try:
            lines.append(normalize(o))
        except ScribeError:
            pass
    if not lines:
        return None
    return next((l for l in lines if not l["action"].lower().startswith("no visible action")), lines[0])


def mock_line(tick):
    """(line, tag) for a scripted tick."""
    action, objects, tag = MOCK_SCRIPT[tick % len(MOCK_SCRIPT)]
    return normalize({"action": action, "objects": objects}), tag


def describe(frames):
    """frames: [jpeg_bytes, ...] oldest first (1-2). Returns {action, objects, model, ms}."""
    if not frames:
        raise ScribeError("no frames")
    t0 = time.time()
    text, model = vision.call_model(
        [(f, "jpeg") for f in frames[-2:]], SYSTEM_PROMPT,
        "Frame 1 is earlier, frame 2 is about 1 second later." if len(frames) > 1 else "One frame.",
        max_tokens=200,
    )
    line = pick(vision.parse_all(text))
    if line is None:
        raise ScribeError(f"unusable scribe reply: {text[:120]!r}")
    return {**line, "model": model, "ms": round((time.time() - t0) * 1000)}
