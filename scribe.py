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

FIRST look at frame 2 on its own: what is the student doing or holding right now? That is your answer. Then use
frame 1 only to add motion (pouring, lifting, turning). If the two frames look the same, the student is holding
still - say what they hold ("Holding a silver water bottle with the cap off."). Never answer "No visible action"
while an object is in the student's hands.

Focus on the STUDENT'S WORK AREA: the person closest to the camera, their hands, the bench in front of them and
anything they hold. Scan that whole area, including its edges (actions often happen off-center), but treat
everything behind it as background.

Rules:
- One short sentence, present tense, at most 15 words, e.g. "Pouring water from a bottle into a clear cup."
- Describe what the hands/person physically do. No intent, no guesses about why, no step numbers.
- Name objects by what they look like ("clear cup", "small white bottle", "blue liquid", "spoon").
- Compare the two frames ONLY to add motion: if the student moves something or something on their bench changes (pouring, stirring,
  a color appearing), say so. Camera shake and lighting changes are NOT actions.
- HOLDING COUNTS: if the student holds, shows or wears an object, describe it even when nothing moves, e.g.
  "Holding a stuffed dog up to the camera.", "Wearing headphones.", "Holding a silver bottle with the cap off."
- "No visible action" ONLY when the student's hands are empty or idle and nothing on the bench changes. Still list
  visible lab objects.
- IGNORE THE BACKGROUND: people walking or moving behind the student, other benches, screens, doors, windows.
  Background movement is never the action. If only the background moves, action = "No visible action".
- You may also get a "Scene tracking" summary from on-device models watching the last 3 seconds: hand motion
  (e.g. "right hand: pouring 2.1s, tilt 68°") and detected objects with who holds them, how high they are and where they are relative to each
  other (e.g. "bottle (held by right hand, lifted high, moving down, above the cup, center), cup (on the bench)"). Use it as extra evidence for motion the still frames can't show and for
  objects you might miss, but the IMAGE is the source of truth: objects on the student's bench matter as much as the
  hands, and only describe what is consistent with the image.
- You may get "Watch for:" key words from the current instruction (an action and its objects, e.g. action 'twist'
  with bottle cap). Look for THOSE objects first and describe what the student does with them - including when the
  action is NOT happening ("Holding a bottle, cap still on."). Leave out unrelated things (clothes, toys, furniture,
  decorations) unless the student is using them. Never claim the action happens unless you can see it.
- boxes: for up to 4 objects the student is using or that sit on their bench (NOT clothes, hair, furniture or
  background), where each one is in the frame as [x, y, width, height] fractions from the top-left corner, named
  with the same words you used in objects (e.g. {"name": "silver water bottle", "box": [0.40, 0.20, 0.12, 0.45]}).
  [] if there are none.
- focus: where in the frame the action (or the most relevant lab object) is, as [x, y, width, height] fractions of
  the frame from the top-left corner, e.g. [0.62, 0.40, 0.25, 0.30]. null if there is nothing to point at.

Return ONLY ONE JSON object for BOTH frames together (describe the change between them), no prose,
no markdown, not one object per frame:
{"action": "<sentence>", "objects": ["<object>", ...], "focus": [x, y, w, h] or null,
 "boxes": [{"name": "<object, same words as in objects>", "box": [x, y, w, h]}, ...]}"""

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
    boxes = []
    for b in data.get("boxes") or []:
        if not isinstance(b, dict):
            continue
        name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip().lower()[:40]
        box = _focus(b.get("box"))
        if name and box and all(x["name"] != name for x in boxes):
            boxes.append({"name": name, "box": box})
    return {"action": action[0].upper() + action[1:], "objects": objects[:8], "focus": _focus(data.get("focus")),
            "boxes": boxes[:4]}


def _focus(v):
    """[x, y, w, h] in 0-1 (also accepts 0-100 or a dict) -> clamped list, or None."""
    if isinstance(v, dict):
        v = [v.get(k) for k in ("x", "y", "w", "h")]
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        x, y, w, h = (float(n) for n in v)
    except (TypeError, ValueError):
        return None
    if max(x, y, w, h) > 1.5:  # percentages
        x, y, w, h = x / 100, y / 100, w / 100, h / 100
    # Llama sometimes sends two corners [x1, y1, x2, y2] instead of [x, y, w, h]: as a size it would run off the frame
    if (x + w > 1.05 or y + h > 1.05) and w > x and h > y and w <= 1.0 and h <= 1.0:
        w, h = w - x, h - y
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(max(w, 0.02), 1.0 - x), min(max(h, 0.02), 1.0 - y)
    if w <= 0 or h <= 0:
        return None
    return [round(x, 3), round(y, 3), round(w, 3), round(h, 3)]


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


MOCK_FOCUS = [None, [0.08, 0.55, 0.22, 0.3], [0.35, 0.1, 0.3, 0.3], [0.35, 0.1, 0.3, 0.3], [0.55, 0.45, 0.2, 0.35],
              [0.55, 0.45, 0.2, 0.35], [0.7, 0.3, 0.2, 0.4], [0.62, 0.35, 0.2, 0.4], [0.5, 0.4, 0.2, 0.35],
              [0.5, 0.45, 0.2, 0.3], [0.2, 0.6, 0.2, 0.25], [0.45, 0.5, 0.2, 0.3], [0.45, 0.5, 0.2, 0.3],
              [0.5, 0.35, 0.2, 0.4], [0.48, 0.42, 0.2, 0.3], [0.75, 0.55, 0.2, 0.35], [0.8, 0.6, 0.18, 0.3]]


def mock_line(tick):
    """(line, tag) for a scripted tick."""
    i = tick % len(MOCK_SCRIPT)
    action, objects, tag = MOCK_SCRIPT[i]
    focus = MOCK_FOCUS[i % len(MOCK_FOCUS)]
    boxes = [{"name": objects[0], "box": focus}] if focus and objects else []
    return normalize({"action": action, "objects": objects, "focus": focus, "boxes": boxes}), tag


_STOP = set("""a an the this that these those it its your you of to in into onto on from with without for and or
then until while at by as up down off out over under about all each both any some few more most other is are be being
been so very gently slowly carefully quickly firmly completely fully again now first next finally make sure using use
open closed tight tightly loose away back together apart around clockwise counterclockwise""".split())
_VERBS = set("""add pour stir mix shake swirl twist turn unscrew screw open close remove place put set lift hold pick
take drop squeeze press push pull fill empty rinse wash dry wipe cut fold tape attach connect plug unplug clamp heat
cool light measure weigh record label insert dip cover uncover tilt rotate move slide hang tie wear grab""".split())
_PARTICLES = {"on", "off", "up", "down", "in", "out"}
_ABSTRACT = {"length", "amount", "volume", "temperature", "time", "mass", "weight", "distance", "value", "reading",
             "result", "results", "data", "number", "position", "level", "step", "steps", "procedure"}
_COLORS = {"red", "blue", "green", "yellow", "white", "black", "clear", "orange", "purple", "pink", "brown", "gray", "grey"}


def key_terms(step):
    """The step's key action + objects, e.g. 'Twist the bottle cap open' -> {'action': 'twist', 'objects': ['bottle cap']}.
    Deterministic (no model call). Objects: the step's visual cues, else the noun runs of the instruction."""
    if not step:
        return {"action": "", "objects": []}
    words = re.findall(r"[a-zA-Z][a-zA-Z-]*", str(step.get("instruction") or "").lower())
    actions = []
    if words:  # instructions start with their verb ("Twist ...", "Put on ...")
        actions.append(words[0] + (f" {words[1]}" if len(words) > 1 and words[1] in _PARTICLES else ""))
    nouns, run = [], []
    for w in words[1:] + ["."]:
        if w in _VERBS:  # a second action ("... and stir")
            if run:
                nouns.append(" ".join(run)); run = []
            if w not in " ".join(actions).split():
                actions.append(w)
        elif w != "." and w not in _STOP and w not in _ABSTRACT and w not in _PARTICLES \
                and w not in {"ml", "drops", "drop", "seconds", "minutes", "cm", "g"}:
            run.append(w)
        elif run:
            nouns.append(" ".join(run)); run = []
    action = ", ".join(actions[:2])
    objects = [c.strip().lower() for c in (step.get("visual_cues") or []) if c and c.strip()] or nouns
    # drop pure colors ("blue") but keep "blue liquid"
    objects = [o for o in dict.fromkeys(objects) if o not in _COLORS][:5]
    return {"action": action, "objects": objects}


def describe(frames, motion="", terms=None):
    """frames: [jpeg_bytes, ...] oldest first (1-2); motion: hand-tracking summary; terms: key_terms() of the current
    step (what to watch). Returns {action, objects, focus, model, ms}."""
    if not frames:
        raise ScribeError("no frames")
    t0 = time.time()
    text, model = vision.call_model(
        [(f, "jpeg") for f in frames[-2:]], SYSTEM_PROMPT,
        ("Frame 1 is earlier, frame 2 is about 1 second later." if len(frames) > 1 else "One frame.")
        + (f"\nWatch for: action '{terms['action']}' with {', '.join(terms['objects']) or 'the materials'}."
           if terms and (terms.get("action") or terms.get("objects")) else "")
        + (f"\nScene tracking (last 3 s): {motion}" if motion else ""),
        max_tokens=320,   # room for boxes
    )
    line = pick(vision.parse_all(text))
    if line is None:
        raise ScribeError(f"unusable scribe reply: {text[:120]!r}")
    return {**line, "model": model, "ms": round((time.time() - t0) * 1000)}
