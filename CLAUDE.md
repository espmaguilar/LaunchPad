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
- [ ] **B. Scribe**: every 3 s send the latest 2 frames to Bedrock vision -> `{action, objects[]}`;
  live transcript panel; skip a frame if the previous call is still running. Mock: lines cycle
  through the demo steps.
- [ ] **C. Step matcher + feedback**: per transcript line, Bedrock (text) gets current + next step +
  last ~15 s -> `{status, confidence, evidence, mistake, mistake_type, what_went_wrong, how_to_fix}`.
  Advance on "done" >= 0.7 twice in a row; "next_step_detected" while current not done -> possible
  skipped step; mistakes need 2 consecutive detections >= 0.7. Polly: "Step N complete. Next: ..."
  and friendly corrections. Checklist, big current-step card, evidence, Mark done / Undo,
  Feedback panel (time, snapshot, what went wrong, how to fix, "Got it").
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
- `vision.py`: Bedrock image calls with automatic Nova Lite fallback + JSON parsing (Phases B-D).
- `voice.py`: Polly MP3 for any phrase, cached in memory + `audio_cache/`. Browser falls back to
  speechSynthesis; mute toggle in the topbar.
- `ppe.py`: Rekognition person parsing (background filter via MIN_PERSON_AREA). `settings.py`:
  runtime settings (Polly voice, min person size, workspace name) in gitignored settings.json.
- `setup_aws.py`: creates the S3 bucket + `labscribe-procedures`; `--check` is read-only (also probes
  Textract, Polly, Bedrock text + vision).
- `demo/make_lab_manual.py` -> `demo/color_change_lab.pdf`; `demo/pdfgen.py` tiny PDF writer.
- `frontend/src`: `App.tsx` shell, `pages/Procedures.tsx` (list, upload, processing, review editor),
  `pages/Settings.tsx`, `voice.ts` (speak + mute + Safari unlock), `design.css` (Figma design) +
  `app.css` (additions). Rebuild: `cd frontend && npm run build`.
- Tests: `test_procedures.py`, `test_parser.py`.

## Notes
- Real test 2026-11-08: demo manual -> Textract + Llama 4 Scout in 6.2 s, 6 steps with visual expected
  actions; goggles flagged on steps 1 and 5.
- Git: project-local repo, remote origin = github.com/espmaguilar/LaunchPad, branch `workspace`.
  Never commit .env.
