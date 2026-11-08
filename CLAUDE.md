# LabScribe: build plan & progress

AI lab partner. A user uploads a lab manual PDF; the AI turns it into steps. During the
experiment the camera feed is transcribed into a running log of actions; each action window
is matched against the current step; steps auto-advance with voice guidance; mistakes are
explained (what went wrong + how to fix, citing the manual); a lab record is generated.

Hackathon (AWS track). **A working demo comes before completeness.** Build in phases and stop
after each one with test steps for the user.

## History
- Pivoted from **SafeSight** (PPE compliance camera with fines) on 2026-11-08. All SafeSight code
  is in git commit `e7dfecd` ("SafeSight final state before LabScribe pivot"). Removed: zones,
  fines/OSHA, SNS emails/escalation, incidents/dashboard, zone policy ingestion, old classic UI.
  The AWS tables `safesight-zones` / `safesight-violations` still exist but are no longer used.

## Stack
- Python 3.11+ (venv runs 3.14), FastAPI, boto3, uvicorn. React 19 + Vite 8 + Tailwind 4 UI in
  `frontend/` (from the team's Figma design), built into `static/app/` and committed.
- AWS: S3, Textract, Bedrock Runtime `converse` (text + image blocks), Polly, DynamoDB,
  Rekognition (person boxes for the background filter only).
- Config in `.env` (gitignored): AWS_REGION, BEDROCK_MODEL_ID, S3_BUCKET, MOCK_MODE, MIN_PERSON_AREA.
  Never hardcode or commit secrets. Show the user commands before creating AWS resources.

## Hard facts
- Rekognition PPE can't see goggles or lab coats. PPE checks for safety-critical steps use the
  **Bedrock vision model** (Phase D).
- Bedrock: never send `temperature` (current Claude models reject sampling params). Workshop
  account: Claude models are AccessDenied; BEDROCK_MODEL_ID = us.meta.llama4-scout-17b-instruct-v1:0
  (accepts images). Vision fallback: us.amazon.nova-lite-v1:0 (`vision.call_model`).
- Facts (times, step numbers, evidence) come from our data; the LLM writes wording only.
- Tone of feedback: encouraging coach, never harsh.

## Phases
- [x] **A. Procedure ingestion**: PDF -> S3 `labscribe/manuals/{ts}.pdf` -> Textract (pypdf fallback)
  -> Bedrock strict JSON `{title, steps[{number, instruction, expected_action, visual_cues,
  safety_critical, required_ppe}]}` (one retry) -> review (edit/reorder/add/delete/safety/PPE) ->
  Approve -> DynamoDB `labscribe-procedures` (PK procedure_id). MOCK_MODE / Bedrock failure:
  heuristic "Step N:" parser with a warning. Demo manual `demo/color_change_lab.pdf`.
- [x] **B. Scribe**: browser keeps a 2-frame ring (1 s apart, 640 px JPEG) and every 3 s POSTs it to
  `/api/sessions/{id}/scribe` -> `scribe.describe` (Bedrock vision, strict "physical actions only"
  prompt) -> `{action, objects[]}`. Per-session lock acquired without waiting: if the previous call is
  still running the frames are SKIPPED (no backlog; also skipped client-side). Identical consecutive
  actions merge (repeat count). Sessions live in memory (`sessions.py`; lost on restart) until Phase D/E.
  Mock: `scribe.MOCK_SCRIPT` walks through the demo steps, works without a camera. Llama 4 Scout often
  returns one JSON object per frame: `vision.parse_all` + `scribe.pick` keep the first with an action.
  Real latency 0.7-1.8 s per call.
- [x] **C. Step matcher + feedback** (`matcher.py`, state machine in `sessions.py`): after every scribe tick,
  Bedrock (text) gets the step list, current + next step and the transcript since the current step started
  (max 15 s) -> `{status, confidence, evidence, mistake, mistake_type, what_went_wrong, how_to_fix}`.
  Advance on "done" >= 0.7 twice in a row; same mistake type twice -> feedback item + snapshot + Polly
  "Hold on — ..." (once per step+type); "next_step_detected" twice -> "Possible skipped step". Manual
  Mark done / Undo; "Got it" dismisses. Mistakes on a step that then completes are marked corrected.
  Llama 4 Scout quirks handled: compound steps ("add ... and stir") stayed in_progress -> a focused
  yes/no `confirm_done` call (only when in_progress) checks the step's DONE-WHEN condition; it must see the
  action/result, not just objects. "wrong_material" that is a later step's material -> `refine` makes it
  wrong_order. Real-model scenario suite: 28/28 after these fixes (avg 0.9 s per check).
  Advance modes (`/api/health.advance_mode`, env ADVANCE_MODE). DEFAULT = "instant" (2026-11-08, user: "as soon as
  a line like that comes, the next step comes ... when a human does something it's instant"): ONE matcher check with
  done / in_progress / next_step_detected >= DONE_MIN_CONFIDENCE (0.55), no mistake, AND a transcript line since the
  step started names a KEY OBJECT (`sessions._key_words`: key_terms objects + visual cues + (aliases), minus generic
  scenery and BODY PARTS like head/ears, plus _ALIASES e.g. iphone->phone). "Lifting hands high" can't tick
  "put on headphones". No vision check, no min time. `checks[].confirm` says which line matched.
  Previous default "careful" (2026-11-08, user: "things are done in haste" -
  "put on headphones" ticked when hands were lifted high): matcher "done" >= DONE_MIN_CONFIDENCE (0.55) OR "in_progress" >=
  IN_PROGRESS_MIN_CONFIDENCE (0.55, only when the frame is really checked - compound steps like "unscrew the cap.
  Set the cap down" stayed in_progress forever while the transcript said "Unscrewing cap"), no
  mistake, >= MIN_STEP_SECONDS (5) on the step (real mode only), and each such check confirmed by
  `matcher.finished_check` (FINISH_PROMPT: MAIN action done or clearly being done on the KEY object, using the
  scribe lines + image together; follow-ups like "set it down" not required; NOT done when the image contradicts the
  lines or only hands are raised) >= FINISH_MIN_CONFIDENCE (0.55; user asked for 55%), on DONE_CHECKS (2) checks in a row. Result logged as `confirm` in
  `checks`. Real photos: 12/12 (opening a bottle + "Unscrewing cap" = yes; bottle alone / hands up = no; headphones
  on = yes; hands up = no even when a scribe line claims headphones).
  LENIENT_ADVANCE=true in .env restores the old lenient mode; CONFIRM_WITH_VISION=false = plain strict.
  Coach suggestions: matcher also returns `tip` (always); Coach panel shows the latest; spoken when the
  student has been on a step >= STUCK_SECONDS (20 s), at most every TIP_REPEAT_SECONDS (45 s). Each session
  keeps its last 40 checks (`checks`) for debugging; `/api/health` reports `advance_mode`.
  Mock: `scribe.MOCK_SCRIPT` lines carry demo-step tags (incl. a vinegar-before-coloring mistake) and
  `matcher.mock_check` is deterministic.
- [x] Ingestion for real manuals (2026-11-08): prompt extracts only student actions (skips theory/tables/
  questions), adds `section` and `camera_checkable` (calculate/plot/sketch/write = paper step, no camera cue).
  Paper steps: matcher skipped, coach + voice say "tap Mark step done". Diagram/scan PDFs (< MIN_MANUAL_CHARS
  of text, e.g. Task_1.1_String_Modes: 75 chars) or Bedrock returning `steps: []` -> `NoStepsFound` + one clear
  warning (was a confusing "steps must be a non-empty list" JSON error). Tested on the user's "Lab 7 - Standing
  Waves" (PHYS 32): 15 steps, Part 1/Part 2 sections, 6 paper steps.
- [x] Ask LabScribe (`coach.py`, `POST /api/sessions/{id}/ask`): Bedrock answers questions with the procedure,
  current step, recent transcript and open feedback; 1-3 sentences, never invents steps ("the procedure doesn't
  specify ... ask your instructor"). Answers are spoken.
- [x] UI v3 (third Figma export): immersive Live Session (instruction header with procedure picker - switching
  mid-session ends + restarts after confirm -, full-bleed camera, Scribe overlay, floating Undo/Mark done/camera
  controls, steps drawer, coach chat = tips + step messages + mistake cards + Q&A), collapsible sidebar with
  "Feed in a lab manual". Chat log lives in `useLab` so it survives page changes.
- [x] "Feeling confused?" (`breakdown.py`, `POST /api/sessions/{id}/confused {yes}`): on a camera step for
  CONFUSED_AFTER_SECONDS (25, env-overridable) -> `confused_offer` event -> pop-up. No = never again for that
  step. Yes = Bedrock splits the step into 2-5 sub-steps each with its own camera check (last one = the step's
  done-condition); the session checks the current sub-step with `matcher.confirm_done` instead of the matcher;
  all done -> step unlocks. Manual sub-step tick: `POST /substeps/done`.
  Sub-steps are checked by VISION (`matcher.substep_check`: latest frame + recent lines + parent step for aliases,
  image-first prompt; text-only confirm_done kept failing on aliases/compound conditions). Real test 10/10.
  Exit: `POST /substeps/exit` (Exit button on the panel) and "Got it" on any chat card also exits the breakdown.
- [x] Coach = chatbot only: no tips (removed from the matcher prompt and the chat), no spoken tips. Chat keeps
  Q&A + mistake cards; step completions are toasts.
- [x] Accuracy: a step's matcher window only contains lines AFTER the step started (old bug: step 2 ticked on
  step 1's "placing headphones" line); nothing is judged until the new step has its own line. Each transcript
  line keeps its camera frame; step/feedback snapshots = frame of the evidence line. "[00:16]" stripped.
- [x] Scribe scans the whole frame and returns `focus` [x,y,w,h]; the live callout follows it.
- [x] Lab Records + chat snapshots open an in-page carousel (`Lightbox` in ui.tsx), not a new tab.
- [x] Hand tracking (`frontend/src/hands.ts`): MediaPipe Hand Landmarker 0.10.14 from jsDelivr (GPU, CPU
  fallback, 2 hands), adaptive throttle (<= 15 fps, <= ~1/3 of main-thread time - unthrottled CPU detection
  starved the scribe timers). Rolling 3 s features: pouring (tilt > 45 deg for > 1.5 s), stirring (fingertip
  sweep >= 1.5 turns), pinch count, side of frame, idle/moving. Summary sent as `motion` with each scribe
  request and added to the scribe prompt. 21-point skeleton overlay + "Hand tracking" chip with Show/Hide.
  Handedness is swapped (MediaPipe assumes mirrored selfie frames). Fails silently -> app works without it.
  Scene tracking: also MediaPipe Object Detector (EfficientDet-Lite0, COCO classes, ~2.5/s, scenery like person/
  laptop ignored) -> amber boxes with labels, "held by right hand" when >= 3 hand landmarks are inside a box. Objects
  are added to the `motion` summary ("objects: bottle (held by right hand, center), cup (left side)"). The scribe
  prompt says the IMAGE is the source of truth and the whole scene matters, not just hands. Matcher/sub-step
  checks never see the overlay.
  Objects are tracked (same label + overlap/near, smoothed, kept 1.5 s after last seen -> faded, no flicker); model
  threshold 0.3, boxes < 0.45 shown as "maybe" only when nothing surer is visible AND they are large or near a hand.
  Background filters (user: "too sensitive, focusing on background movements"): boxes < 1.2% of the frame dropped
  unless held, new boxes need 2 hits, motion only for held objects (> 6%) or big moves (> 15%), hands with a landmark
  span < 0.09 of the frame ignored (background people), hand confidences 0.6. Scribe prompt: student's work area
  only; background people/screens/doors, camera shake and lighting are never the action. Each object gets height vs the
  "bench line" (lowest unheld object): on the bench / raised / lifted high; motion over 1.5 s (moving up/down/...);
  up to 2 relations (above / touching / left of / right of another object). Shown on the box label + sent to the scribe.
- [x] Instruction key words (user tip: "twist the bottle cap open" -> watch twist + bottle cap, not the shirt/toy):
  `scribe.key_terms(step)` (deterministic, no model call): first verb (+ particle: "put on") and later known verbs
  ("and stir") -> action; visual_cues, else noun runs of the instruction minus stop/abstract words -> objects.
  `sessions._focus_terms` uses the current sub-step in step-by-step mode. Sent to the scribe as "Watch for: action
  'twist, open' with bottle cap." (prompt: look for those first, say when the action is NOT happening, leave out
  clothes/toys/furniture, never claim the action unless seen). `focus_terms` in public state -> `hands.setFocusTerms`:
  matching objects (COCO label -> synonyms, e.g. bottle ~ cap/lid/flask, cup ~ beaker/glass/tube) get a solid teal
  "★" box; others a faint unlabeled outline and are listed as "ignored" in the summary; no match -> all shown as before.
  Chip shows "Watching: twist, open · bottle cap". Not yet verified on real frames.
- [x] Camera first: `startSession` awaits camera permission (startCamera -> bool) BEFORE POST /api/sessions and the
  spoken intro, so the clock and voice start only after "Allow". Denied -> session still starts (mock / Mark done).
  "Start session" on Procedures switches to Live first so the prompt appears over the camera placeholder.
- [x] Restart step (`POST /api/sessions/{id}/steps/restart`, circular-arrow button next to Undo): the current step's
  start time resets so earlier lines stop counting; streaks, breakdown, the confused offer and mistake warnings for
  that step reset.
- [x] Voice: ElevenLabs when ELEVENLABS_API_KEY is in .env (voice ELEVENLABS_VOICE_ID, default
  dmWfLQZJq7wNPv9WmtiM, model eleven_turbo_v2_5), cached; any ElevenLabs error -> Polly. `/api/health.voice`.
- [ ] **D. Safety + deviations**: before safety_critical steps, Bedrock vision checks required PPE;
  block advancing + "Put on <gear> before continuing." Log deviations (skipped, out of order,
  wrong action, PPE missing, mistakes) with snapshot to S3 + DynamoDB.
- [ ] **E. Lab record**: procedure, start/end, each step's time + evidence + snapshot, deviations,
  "Mistakes & tips" (every mistake, whether corrected, 2-3 personalized tips), duration, Download PDF.
- [ ] Stretch: instructor view (most common mistakes per step across sessions).

## Files
- `main.py`: FastAPI app: `/api/health`, `/api/procedures/upload|approve`, `GET/DELETE /api/procedures[/{id}]`,
  `/api/procedures/sample`, `/api/voice?text=`, `/api/settings`, UI at `/` (static/app).
- `ingest.py`: PDF -> S3 -> text (Textract async, pypdf fallback). `procedures.py`: Bedrock steps
  prompt + retry, heuristic parser, normalize/validate. `store.py`: procedures in DynamoDB / mock JSON
  (`TableMissing` -> friendly 503 "run setup_aws.py").
- `vision.py`: Bedrock image calls with automatic Nova Lite fallback + JSON parsing (`parse_all` for
  multi-object replies).
- `scribe.py`: scribe prompt, normalize/pick, mock script. `sessions.py`: in-memory sessions + transcript,
  skip-when-busy, repeat merging. Endpoints: `POST /api/sessions {procedure_id}`, `GET /api/sessions/{id}?since=n`,
  `POST /api/sessions/{id}/scribe {frames: [dataURL x2]}`, `POST /api/sessions/{id}/end`, `GET /api/sessions` (list),
  `POST /api/sessions/{id}/steps/done|undo`, `POST /api/sessions/{id}/feedback/{fid}/dismiss`, `GET /api/sessions/{id}/snapshots/{snap}`.
- `voice.py`: Polly MP3 for any phrase, cached in memory + `audio_cache/`. Browser falls back to
  speechSynthesis; mute toggle in the topbar.
- `ppe.py`: Rekognition person parsing (background filter via MIN_PERSON_AREA). `settings.py`:
  runtime settings (Polly voice, min person size, workspace name) in gitignored settings.json.
- `setup_aws.py`: creates the S3 bucket + `labscribe-procedures`; `--check` is read-only (also probes
  Textract, Polly, Bedrock text + vision).
- `demo/make_lab_manual.py` -> `demo/color_change_lab.pdf`; `demo/pdfgen.py` tiny PDF writer.
- UI: second Figma design ("LabScribe AI Lab Partner", light teal theme) in `frontend/src/design.css`; `app.css` = additions
  (states, layout fixes for ~1170 px windows). Pages: Live Session (steps rail, current step + AI confidence, camera with
  live scribe caption, Mark done/Undo/End, success toast, Scribe panel with Export, Coach feedback with "Got it"),
  Procedures (stepper, upload/sample, processing, review cards with inline edit + up/down reorder, publish card:
  Approve & start session / save only, saved library), Lab Records (from real session data: timeline with evidence +
  snapshots, deviations, mistakes; print-to-PDF; Bedrock tips come in Phase E), Instructor View (KPIs, sessions,
  most common mistakes by step - real data from `GET /api/sessions`), Settings (camera, coach voice, Polly voice +
  test, background filter, workspace; "Block on safety steps" shown disabled until Phase D).
- `frontend/src`: `App.tsx` shell (owns `useLab` from `lab.ts`: camera + session loop keeps running on
  every page), `pages/LabSession.tsx`, `pages/Procedures.tsx`, `pages/Records.tsx`, `pages/Instructor.tsx` (list, upload, processing, review editor),
  `pages/Settings.tsx`, `voice.ts` (speak + mute + Safari unlock), `design.css` (Figma design) +
  `app.css` (additions). Rebuild: `cd frontend && npm run build`.
- Tests: `test_procedures.py`, `test_scribe.py`, `test_matcher.py`, `test_voice.py`, `test_parser.py`.

## Notes
- Real test 2026-11-08: demo manual -> Textract + Llama 4 Scout in 6.2 s, 6 steps with visual expected
  actions; goggles flagged on steps 1 and 5.
- Git: project-local repo, remote origin = github.com/espmaguilar/LaunchPad, branch `workspace`.
  Never commit .env.
