// Browser-side scene tracking: hands (MediaPipe Hand Landmarker) AND objects (MediaPipe Object Detector,
// EfficientDet-Lite0 / COCO classes: bottle, cup, phone, teddy bear, spoon, bowl, scissors...), so the
// summary sent to the Scribe covers the whole bench, not just the hands.
// Hand tracking with MediaPipe Hand Landmarker (@mediapipe/tasks-vision from the CDN,
// no API key, GPU delegate, up to 2 hands). Runs on every animation frame of the live video and keeps
// a rolling 3 s window per hand to compute simple motion features:
//   pouring  - hand axis tilted > 45 deg from vertical for > 1.5 s
//   stirring - index fingertip sweeping >= 1.5 turns around its own centre within the window
//   squeezes - thumb-index pinches (distance dips) -> e.g. drops added
//   side     - where the hand is relative to the frame centre
//   idle / moving
// The summary ("right hand: pouring 2.1s, tilt 68°, right side; left hand: holding, idle") is sent with
// each Scribe request. If MediaPipe fails to load, everything else keeps working without it.

const VERSION = "0.10.14";
const CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VERSION}`;
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const OBJECT_MODEL = "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite";
const OBJECT_EVERY_MS = 400;   // objects change slower than hands: ~2.5 detections/s keeps it light
const OBJECT_MIN_SCORE = 0.3;     // model threshold
const OBJECT_SURE_SCORE = 0.45;   // below this an object is shown as "maybe" (only near a hand or large)
const OBJECT_MIN_AREA = 0.012;    // smaller boxes are background clutter (unless a hand holds them)
const OBJECT_CONFIRM_HITS = 2;    // a new box must be seen twice before it shows (no one-off ghosts)
const MIN_HAND_SPAN = 0.09;       // hands smaller than this (fraction of the frame) belong to people in the background
const OBJECT_KEEP_MS = 1500;      // keep a box this long after the detector last saw it (no flicker)
const IGNORE_OBJECTS = new Set(["person", "chair", "dining table", "tv", "laptop", "couch", "bed"]); // scenery, not lab items
const WINDOW_MS = 3000;
const POUR_TILT_DEG = 45;
const POUR_MIN_MS = 1500;

export type Pt = { x: number; y: number };
export interface HandFeatures {
  side: "left" | "right";
  pouring: boolean;
  pourMs: number;
  tilt: number;
  stirring: boolean;
  squeezes: number;
  position: string;
  moving: boolean;
}
export interface SceneObject {
  label: string;           // the browser detector's class (COCO) - internal only: NOT shown, NOT sent to the scribe
  name: string | null;     // what the SCRIBE called it (Llama vision) - the one name shown everywhere; null = unnamed
  fromScribe: boolean;     // box comes only from the scribe (detector didn't find it), position up to ~3 s old
  score: number;
  box: { x: number; y: number; w: number; h: number }; // 0-1 of the video frame
  heldBy: "left" | "right" | null;
  maybe: boolean;          // low confidence
  height: "lifted high" | "raised" | "on the bench";
  motion: "" | "moving up" | "moving down" | "moving left" | "moving right";
  stale: boolean;          // not seen in the latest detection (kept briefly)
  relations: string[];     // e.g. "above the cup", "left of the bowl"
  focus: boolean;          // matches the current instruction's key objects
}
interface Track { id: number; label: string; name: string | null; score: number; box: SceneObject["box"]; lastSeen: number; hits: number; hist: { t: number; cx: number; cy: number }[] }
let tracks: Track[] = [];
let lastHands: HandsState["hands"] = [];

// ---- focus on the instruction's key words (e.g. "Twist the bottle cap open" -> bottle cap) ----
let focusWords: string[] = [];
// ---- names come from the scribe (open vocabulary), positions from the browser detector ----
// The detector only knows 80 COCO classes on a ~320 px image ("toothbrush" for a slim metal bottle), so its class is
// never shown; each detector box takes the name of the scribe box it overlaps.
const SCRIBE_BOX_MS = 6000;     // scribe names are matched to detector boxes for this long after a scribe line
// Llama's own boxes are rough (tested on photos: sometimes offset, sometimes on empty background), so they are only a
// hint. The strong signal: the object named in the action sentence ("Drinking from a water bottle") is the one a
// hand is holding.
let scribeBoxes: { name: string; box: SceneObject["box"]; at: number }[] = [];
let actionObject: { name: string; at: number } | null = null;
const wordsOf = (s: string) => s.toLowerCase().split(/[^a-z]+/).filter((w) => w.length > 2);
/** Latest scribe line: its named boxes ([x, y, w, h] fractions), action sentence and object names. */
export function setScribeObjects(boxes: { name: string; box: [number, number, number, number] }[], action = "", objects: string[] = []) {
  const at = performance.now(), act = new Set(wordsOf(action));
  scribeBoxes = (boxes ?? []).map((b) => ({ name: b.name, box: { x: b.box[0], y: b.box[1], w: b.box[2], h: b.box[3] }, at }));
  // the object the action is about: the scribe's object name that shares the most words with the sentence
  let best: string | null = null, bestN = 0;
  for (const n of [...objects, ...(boxes ?? []).map((b) => b.name)]) {
    const k = wordsOf(n).filter((w) => act.has(w)).length;
    if (k > bestN) { best = n; bestN = k; }
  }
  actionObject = best ? { name: best, at } : null;
}
const singular = (w: string) => w.replace(/(es|s)$/, "");
/** Current step's key words (the session sends them as focus_terms). */
export function setFocusTerms(action: string, objects: string[]) {
  focusWords = [...new Set(objects.join(" ").toLowerCase().split(/[^a-z]+/).filter((w) => w.length > 2).map(singular))];
  const watching = [action, objects.join(", ")].filter(Boolean).join(" · ");
  if (watching !== state.watching) emit({ watching });
}
function isFocus(name: string | null) {
  if (!focusWords.length || !name) return false;
  return name.toLowerCase().split(/[^a-z]+/).map(singular).some((w) => focusWords.includes(w));
}
let nextTrackId = 1;
export interface HandsState {
  status: "off" | "loading" | "ready" | "unavailable";
  hands: { side: "left" | "right"; points: Pt[] }[]; // latest landmarks (0-1 of the video frame)
  features: HandFeatures[];
  objects: SceneObject[];
  summary: string;
  watching: string;        // "twist, open · bottle cap" (from the current step)
}

type Sample = { t: number; pts: Pt[] };
// MediaPipe's "handedness" assumes a mirrored (selfie) image. Our frames are NOT mirrored, so swap.
const sideOf = (label: string): "left" | "right" => (label === "Left" ? "right" : "left");

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let landmarker: any = null;
let loading: Promise<boolean> | null = null;
let raf = 0;
let video: HTMLVideoElement | null = null;
let lastVideoTime = -1;
let nextRunAt = 0;      // adaptive throttle: <= 15 fps, and back off when detection is slow (CPU fallback)
let costMs = 0;
const history: Record<"left" | "right", Sample[]> = { left: [], right: [] };
let state: HandsState = { status: "off", hands: [], features: [], objects: [], summary: "", watching: "" };
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let objectDetector: any = null;
let nextObjectsAt = 0;
const listeners = new Set<(s: HandsState) => void>();

export const handsState = () => state;
export function subscribeHands(cb: (s: HandsState) => void) {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}
function emit(next: Partial<HandsState>) {
  state = { ...state, ...next };
  listeners.forEach((cb) => cb(state));
}

async function load(): Promise<boolean> {
  if (landmarker) return true;
  if (!loading) {
    loading = (async () => {
      try {
        const vision = await import(/* @vite-ignore */ `${CDN}/vision_bundle.mjs`);
        const fileset = await vision.FilesetResolver.forVisionTasks(`${CDN}/wasm`);
        const make = (delegate: "GPU" | "CPU") => vision.HandLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: MODEL, delegate }, runningMode: "VIDEO", numHands: 2,
          minHandDetectionConfidence: 0.6, minHandPresenceConfidence: 0.6, minTrackingConfidence: 0.6,
        });
        try { landmarker = await make("GPU"); } catch { landmarker = await make("CPU"); }
        // objects are optional: hands still work if this model fails
        const makeObj = (delegate: "GPU" | "CPU") => vision.ObjectDetector.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: OBJECT_MODEL, delegate }, runningMode: "VIDEO", scoreThreshold: OBJECT_MIN_SCORE, maxResults: 8,
        });
        try { objectDetector = await makeObj("GPU"); } catch { try { objectDetector = await makeObj("CPU"); } catch (e) { console.warn("[hands] object detector unavailable:", e); } }
        return true;
      } catch (e) {
        console.warn("[hands] MediaPipe unavailable, continuing without hand tracking:", e);
        return false;
      }
    })();
  }
  return loading;
}

const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);

/** Angle of the hand axis (wrist -> middle-finger knuckle) away from pointing straight up, in degrees. */
function tiltOf(p: Pt[]) {
  const dx = p[9].x - p[0].x, dy = p[9].y - p[0].y; // image y grows downward
  return Math.abs((Math.atan2(dx, -dy) * 180) / Math.PI);
}

function features(side: "left" | "right", samples: Sample[], now: number): HandFeatures {
  const latest = samples[samples.length - 1].pts;
  const size = Math.max(0.02, dist(latest[0], latest[9])); // hand scale: wrist -> middle knuckle
  // pouring: continuous run of tilt > threshold ending now
  let pourMs = 0;
  for (let i = samples.length - 1; i >= 0 && tiltOf(samples[i].pts) > POUR_TILT_DEG; i--) pourMs = now - samples[i].t;
  // stirring: total angle swept by the index fingertip around the path's centroid
  const tips = samples.map((s) => s.pts[8]);
  const c = { x: tips.reduce((a, p) => a + p.x, 0) / tips.length, y: tips.reduce((a, p) => a + p.y, 0) / tips.length };
  const radius = tips.reduce((a, p) => a + dist(p, c), 0) / tips.length;
  let sweep = 0;
  for (let i = 1; i < tips.length; i++) {
    let d = Math.atan2(tips[i].y - c.y, tips[i].x - c.x) - Math.atan2(tips[i - 1].y - c.y, tips[i - 1].x - c.x);
    if (d > Math.PI) d -= 2 * Math.PI;
    if (d < -Math.PI) d += 2 * Math.PI;
    sweep += d;
  }
  const stirring = Math.abs(sweep) >= 3 * Math.PI && radius > 0.15 * size && radius < 2.5 * size;
  // squeezes: thumb-index distance (relative to hand size) dipping below 0.25 after being above 0.4
  let squeezes = 0, open = true;
  for (const s of samples) {
    const r = dist(s.pts[4], s.pts[8]) / Math.max(0.02, dist(s.pts[0], s.pts[9]));
    if (open && r < 0.25) { squeezes++; open = false; } else if (!open && r > 0.4) open = true;
  }
  // moving vs idle: wrist travel over the last second, relative to hand size
  const recent = samples.filter((s) => now - s.t <= 1000);
  const travel = recent.slice(1).reduce((a, s, i) => a + dist(s.pts[0], recent[i].pts[0]), 0);
  const moving = travel / size > 0.6;
  const x = latest[0].x, y = latest[0].y;
  const horiz = x < 0.4 ? "left side" : x > 0.6 ? "right side" : "center";
  const vert = y < 0.35 ? "high" : y > 0.7 ? "low" : "";
  return { side, pouring: pourMs >= POUR_MIN_MS, pourMs, tilt: Math.round(tiltOf(latest)), stirring, squeezes,
    position: vert ? `${horiz}, ${vert}` : horiz, moving };
}

const sideOfBox = (b: SceneObject["box"]) => {
  const cx = b.x + b.w / 2;
  return cx < 0.4 ? "left side" : cx > 0.6 ? "right side" : "center";
};
export function describeObject(o: SceneObject) {
  const bits = [o.heldBy ? `held by ${o.heldBy} hand` : "", o.height, o.motion, ...o.relations, sideOfBox(o.box)].filter(Boolean);
  return `${o.name ?? "an object"} (${bits.join(", ")})`;
}

const iou = (a: SceneObject["box"], b: SceneObject["box"]) => {
  const x1 = Math.max(a.x, b.x), y1 = Math.max(a.y, b.y), x2 = Math.min(a.x + a.w, b.x + b.w), y2 = Math.min(a.y + a.h, b.y + b.h);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  return inter / (a.w * a.h + b.w * b.h - inter || 1);
};
const centre = (b: SceneObject["box"]) => ({ cx: b.x + b.w / 2, cy: b.y + b.h / 2 });

/** Match new detections to existing tracks (same label, overlapping or close), smooth boxes, drop old tracks. */
function updateTracks(dets: { label: string; score: number; box: SceneObject["box"] }[], now: number) {
  const used = new Set<number>();
  for (const d of dets) {
    let best: Track | null = null, bestScore = 0;
    for (const t of tracks) {
      if (used.has(t.id) || t.label !== d.label) continue;
      const a = centre(t.box), b = centre(d.box);
      const sc = Math.max(iou(t.box, d.box), 0.3 - Math.hypot(a.cx - b.cx, a.cy - b.cy)); // overlap, or within ~0.3 of the frame
      if (sc > bestScore) { best = t; bestScore = sc; }
    }
    if (best) {
      const k = 0.6; // smoothing toward the new box
      best.box = { x: best.box.x + (d.box.x - best.box.x) * k, y: best.box.y + (d.box.y - best.box.y) * k,
        w: best.box.w + (d.box.w - best.box.w) * k, h: best.box.h + (d.box.h - best.box.h) * k };
      best.score = d.score; best.lastSeen = now; best.hits++; used.add(best.id);
    } else {
      const t: Track = { id: nextTrackId++, label: d.label, name: null, score: d.score, box: d.box, lastSeen: now, hits: 1, hist: [] };
      tracks.push(t); used.add(t.id);
    }
  }
  tracks = tracks.filter((t) => now - t.lastSeen <= OBJECT_KEEP_MS);
  for (const t of tracks) {
    if (!used.has(t.id)) continue;
    t.hist.push({ t: now, ...centre(t.box) });
    t.hist = t.hist.filter((p) => now - p.t <= 1500);
  }
}

/** Give detector boxes the scribe's names. 1) the object named in the action sentence goes to the box a hand holds;
 *  2) other boxes: best one-to-one overlap with the scribe's (rough) boxes. Names stick to a track afterwards. */
function nameTracks(live: Track[], hands: HandsState["hands"], now: number) {
  const usedT = new Set<number>(), usedN = new Set<string>();
  if (actionObject && now - actionObject.at <= SCRIBE_BOX_MS) {
    const held = live.filter((t) => holder(t.box, hands));
    if (held.length === 1) {   // only when it's unambiguous which box is in hand
      held[0].name = actionObject.name; usedT.add(held[0].id); usedN.add(actionObject.name);
    }
  }
  const fresh = scribeBoxes.filter((b) => now - b.at <= SCRIBE_BOX_MS && !usedN.has(b.name));
  const pairs: { t: Track; b: (typeof fresh)[number]; sc: number }[] = [];
  for (const t of live) for (const b of fresh) {
    if (usedT.has(t.id)) continue;
    const a = centre(t.box), c = centre(b.box);
    const sc = Math.max(iou(t.box, b.box), 0.15 - Math.hypot(a.cx - c.cx, a.cy - c.cy));
    if (sc > 0.1) pairs.push({ t, b, sc });
  }
  for (const p of pairs.sort((x, y) => y.sc - x.sc)) {
    if (usedT.has(p.t.id) || usedN.has(p.b.name)) continue;
    p.t.name = p.b.name; usedT.add(p.t.id); usedN.add(p.b.name);
  }
}

/** Where things are relative to the bench, the hands and each other. */
function sceneObjects(hands: HandsState["hands"], now: number): SceneObject[] {
  const live = tracks.filter((t) => t.hits >= OBJECT_CONFIRM_HITS);
  nameTracks(live, hands, now);
  // "bench line": the lowest bottom edge of objects nobody holds (fallback: bottom 15% of the frame)
  const base = live.map((t) => ({ t, held: holder(t.box, hands) }))
    .filter(({ t, held }) => held || t.box.w * t.box.h >= OBJECT_MIN_AREA);
  const resting = base.filter((x) => !x.held).map((x) => x.t.box.y + x.t.box.h);
  const bench = resting.length ? Math.max(...resting) : 0.85;
  const objs = base.map(({ t, held }) => {
    const bottom = t.box.y + t.box.h, lift = bench - bottom;
    // unheld objects further back sit higher in the image (perspective), so they need a bigger gap to count as raised
    const height: SceneObject["height"] = held ? (lift > 0.25 ? "lifted high" : lift > 0.08 ? "raised" : "on the bench")
      : lift > 0.3 ? "raised" : "on the bench";
    let motion: SceneObject["motion"] = "";
    if (t.hist.length >= 2) {
      const a = t.hist[0], b = t.hist[t.hist.length - 1], dx = b.cx - a.cx, dy = b.cy - a.cy;
      // only real moves: held objects moving > 6% of the frame, unheld ones > 15% (box jitter is not motion)
      if (Math.hypot(dx, dy) > (held ? 0.06 : 0.15)) motion = Math.abs(dy) >= Math.abs(dx) ? (dy < 0 ? "moving up" : "moving down") : (dx < 0 ? "moving left" : "moving right");
    }
    return { label: t.label, name: t.name, fromScribe: t.id < 0, score: t.score, box: t.box, heldBy: held,
      maybe: t.id >= 0 && t.score < OBJECT_SURE_SCORE, height, motion,
      stale: t.id < 0 || now - t.lastSeen > OBJECT_EVERY_MS * 1.5, relations: [] as string[], focus: isFocus(t.name) };
  });
  // if anything matches the instruction, only those objects count (relations, summary); else keep everything
  const anyFocus = objs.some((o) => o.focus);
  // pairwise relations (closest/most telling one per object, at most 2)
  for (const o of objs) {
    if (anyFocus && !o.focus) continue;
    const a = centre(o.box), rels: { d: number; text: string }[] = [];
    for (const p of objs) {
      if (p === o || (anyFocus && !p.focus)) continue;
      const b = centre(p.box), dx = a.cx - b.cx, gapY = p.box.y - (o.box.y + o.box.h);
      const overlapX = Math.min(o.box.x + o.box.w, p.box.x + p.box.w) - Math.max(o.box.x, p.box.x);
      const name = !p.name ? "another object" : p.name === o.name ? `the other ${p.name}` : `the ${p.name}`;
      if (overlapX > 0 && gapY > -0.05 && gapY < 0.35) rels.push({ d: Math.max(0, gapY), text: `above ${name}` });
      else if (overlapX > 0 && iou(o.box, p.box) > 0.15) rels.push({ d: 0.01, text: `touching ${name}` });
      else if (Math.abs(dx) < 0.3) rels.push({ d: Math.abs(dx), text: `${dx < 0 ? "left" : "right"} of ${name}` });
    }
    o.relations = rels.sort((x, y) => x.d - y.d).slice(0, 2).map((r) => r.text);
  }
  return objs;
}

/** Which hand (if any) is holding this object: >= 3 of that hand's landmarks inside the (slightly padded) box. */
function holder(box: SceneObject["box"], hands: HandsState["hands"]): "left" | "right" | null {
  let best: "left" | "right" | null = null, bestN = 2;
  for (const h of hands) {
    const n = h.points.filter((p) => p.x > box.x - 0.03 && p.x < box.x + box.w + 0.03 && p.y > box.y - 0.03 && p.y < box.y + box.h + 0.03).length;
    if (n > bestN) { best = h.side; bestN = n; }
  }
  return best;
}

export function describe(f: HandFeatures) {
  const what = f.pouring ? `pouring ${(f.pourMs / 1000).toFixed(1)}s, tilt ${f.tilt}°`
    : f.stirring ? "stirring (circular motion)"
      : f.squeezes ? `${f.squeezes} squeeze${f.squeezes === 1 ? "" : "s"} (pinch)`
        : f.moving ? "moving" : "holding, idle";
  return `${f.side} hand: ${what}, ${f.position}`;
}

function loop() {
  raf = requestAnimationFrame(loop);
  const v = video;
  if (!landmarker || !v || v.readyState < 2 || v.currentTime === lastVideoTime) return;
  const now = performance.now();
  if (now < nextRunAt) return; // keep the page responsive (scribe timers, UI)
  lastVideoTime = v.currentTime;
  let res;
  const t0 = performance.now();
  try { res = landmarker.detectForVideo(v, now); } catch { return; }
  costMs = costMs * 0.8 + (performance.now() - t0) * 0.2;
  nextRunAt = now + Math.max(66, costMs * 3); // spend at most ~1/3 of the time detecting
  const hands: HandsState["hands"] = [];
  const seen = new Set<string>();
  (res.landmarks ?? []).forEach((lm: Pt[], i: number) => {
    const side = sideOf(res.handedness?.[i]?.[0]?.categoryName ?? "Right");
    if (seen.has(side)) return;
    const pts = lm.map((p) => ({ x: p.x, y: p.y }));
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    if (Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) < MIN_HAND_SPAN) return; // background hand
    seen.add(side);
    hands.push({ side, points: pts });
    history[side].push({ t: now, pts });
  });
  for (const side of ["left", "right"] as const) {
    history[side] = history[side].filter((s) => now - s.t <= WINDOW_MS);
    if (!seen.has(side) && history[side].length && now - history[side][history[side].length - 1].t > 600) history[side] = [];
  }
  const feats = (["right", "left"] as const).filter((sd) => history[sd].length >= 3).map((sd) => features(sd, history[sd], now));
  // objects: every OBJECT_EVERY_MS (adaptive like hands)
  if (objectDetector && now >= nextObjectsAt) {
    const t1 = performance.now();
    try {
      const out = objectDetector.detectForVideo(v, now);
      const W = v.videoWidth || 1, H = v.videoHeight || 1;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const dets = (out.detections ?? []).map((d: any) => {
        const bb = d.boundingBox, cat = d.categories?.[0];
        return { label: String(cat?.categoryName ?? "object"), score: cat?.score ?? 0,
          box: { x: bb.originX / W, y: bb.originY / H, w: bb.width / W, h: bb.height / H } };
      }).filter((o: { label: string; score: number }) => !IGNORE_OBJECTS.has(o.label));
      // low-confidence boxes only if nothing better: there is always something to show when anything is visible
      const sure = dets.filter((o: { score: number }) => o.score >= OBJECT_SURE_SCORE);
      const near = (o: { score: number; box: SceneObject["box"] }) => o.box.w * o.box.h >= 0.04 || holder(o.box, lastHands) !== null;
      updateTracks(sure.length ? sure : dets.filter(near).slice(0, 2), now);
    } catch { /* keep previous tracks */ }
    nextObjectsAt = now + Math.max(OBJECT_EVERY_MS, (performance.now() - t1) * 4);
  }
  lastHands = hands;
  const objects = sceneObjects(hands, now);
  const parts = [feats.length ? feats.map(describe).join("; ") : "no hands visible"];
  const focusObjs = objects.filter((o) => o.focus);
  if (focusObjs.length) {
    parts.push(`key objects for this step: ${focusObjs.map(describeObject).join(", ")}`);
    const other = [...new Set(objects.filter((o) => !o.focus && o.name).map((o) => o.name as string))];
    if (other.length) parts.push(`ignored (not in the instruction): ${other.join(", ")}`);
  } else {
    parts.push(objects.length ? `objects: ${objects.map(describeObject).join(", ")}` : "no objects tracked");
  }
  emit({ hands, features: feats, objects, summary: parts.join("; ") });
}

/** Start tracking on a playing <video>. Safe to call repeatedly; no-op if MediaPipe can't load. */
export async function startHands(v: HTMLVideoElement) {
  video = v;
  if (state.status === "ready" && raf) return;
  emit({ status: "loading" });
  const ok = await load();
  if (!ok) { emit({ status: "unavailable", hands: [], features: [], objects: [], summary: "" }); return; }
  if (video !== v) return; // camera stopped meanwhile
  cancelAnimationFrame(raf);
  emit({ status: "ready" });
  raf = requestAnimationFrame(loop);
}

export function stopHands() {
  cancelAnimationFrame(raf);
  raf = 0;
  video = null;
  lastVideoTime = -1;
  history.left = [];
  history.right = [];
  tracks = [];
  emit({ status: state.status === "unavailable" ? "unavailable" : "off", hands: [], features: [], objects: [], summary: "" });
}

/** Summary to send with a Scribe request ("" if tracking isn't running). */
export const motionSummary = () => (state.status === "ready" ? state.summary : "");

// 21-point hand skeleton
export const HAND_EDGES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];
