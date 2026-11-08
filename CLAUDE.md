# SafeSight: build plan & progress

AI safety-gear compliance camera that puts a dollar amount on every violation.
Hackathon (AWS track), about 9h of build time. **A working demo comes before completeness.** Build in phases and stop after each one for the user to test.

## Stack
- UI: React 19 + Vite 8 + Tailwind 4 app in `frontend/` (from the team's Figma Make design), built into `static/app/` and committed, so the server runs without Node. FastAPI serves it at `/`; the original vanilla page is kept at `/classic` (+ `/setup`) as a demo backup.
- Python 3.11+ (local venv runs 3.14), FastAPI, boto3, uvicorn (local)
- One static HTML page + vanilla JS + Chart.js, served by FastAPI
- AWS: Rekognition `detect_protective_equipment`, S3, DynamoDB, SNS, Bedrock Runtime `converse`
- Config lives in `.env` (gitignored). AWS credentials come from the standard chain, never from code.

## Hard facts (do not deviate)
- Rekognition PPE detects only FACE_COVER, HAND_COVER, HEAD_COVER. Never claim Rekognition sees goggles or vests: those are AI-vision rules (Bedrock), labelled as such.
- FACE->FACE_COVER, HEAD->HEAD_COVER, LEFT_HAND/RIGHT_HAND->HAND_COVER. Gear counts as worn only if `CoversBodyPart.Value` is true.
- MinConfidence 80. Pass the zone's required types.
- BoundingBox values are 0-1 ratios. Scale them to the canvas size.
- The text must say "potential fine exposure", never "you will be fined".
- OSHA table is in `config.py` (TODO: verify penalties on osha.gov before the demo).

## Smart logic (timings in config.py)
- Clock starts at the first frame where someone is missing a required item. A frame where it's visibly worn restarts it. No-evidence frames (person out of frame, hands hidden) are neutral unless no miss is seen for MISS_GAP_SECONDS (6s).
- 20s continuous (VOICE_AFTER_SECONDS): log the incident (DynamoDB + S3 photo + Bedrock report) and play the Polly voice warning. No message. One voice play, no repeat. Voice is ONE generalized announcement per zone ("face cover and gloves required in the Chem Lab"): the browser merges violations arriving within 2.5s and speaks a zone at most once per 15s (later items queue into the next announcement). Never per person. Mute toggle; browser speechSynthesis fallback.
- 30s continuous (NOTIFY_AFTER_SECONDS): ONE supervisor notification. One message per zone covering every open item in it (gloves + face cover = 1 email). No escalation tier. At most 1 notification per incident.
- Channel: `notify.py`, NOTIFY_CHANNEL=email (SNS) now. SMS via Twilio is planned but not wired (`_send_sms` stub, falls back to email). The SMS text is already built (<=160 chars).
- Hysteresis: resolves only after RESOLVE_SECONDS (demo 10s / prod 30s) of compliance with no miss. The notification is held while a fix is being confirmed (at most RESOLVE_SECONDS+5).
- Fine avoided: fixed within 5 min of logging means RESOLVED. A later fix means `fixed_at` is set and the record stays OPEN.
- Expire: unresolved after EXPIRE_SECONDS (600s) stops tracking (stays OPEN). If still missing, a fresh 20s/30s cycle starts, so at most one notification every ~10 min per item.
- Stale zone: no frames for 10s pauses that zone's timers ("Camera offline"). Stopping the camera within 10s of the voice warning still sends the notification.
- The notification tick runs in one daemon thread started in FastAPI lifespan. update()/tick()/status() share one lock. Don't use --workers > 1.
- Mock mode: person 2 misses gear for 36s of every 60s (voice, notify, resolve all show).

## AI-vision rules (break the 3-gear limit)
- Policy items Rekognition can't check (zone["unmonitored"]: goggles, vests, boots) and free-text rules added on /setup ("No phones", $500) are AI-vision rules. Tracker key = "AI:" + slug(item) (`config.ai_key`). Labels via `config.gear_label`, rules/fines via `config.rule_for` (no fine stated -> $0 "no fine set").
- `vision.py`: every AI_CHECK_SECONDS (10s) per active zone, /analyze hands the frame to a 2-thread pool. Bedrock Converse gets an image block plus numbered rules and returns strict JSON [{rule, evidence, violated true/false/null, confidence, reason}]. One retry on bad JSON. violated with confidence < 0.6 counts as unsure (neutral). If BEDROCK_MODEL_ID rejects images it falls back to us.amazon.nova-lite-v1:0 (remembered).
- `tracker.update_ai`: 2 consecutive positive checks open an incident and play voice. Then the same pipeline: notify at 30s from the first positive (batched per zone with Rekognition items), hysteresis resolve (2 negative checks), stale pause, expiry.
- Model test 2026-11-07 on a real frame (goggles/vest/headphones/phone): Llama 4 Scout and Nova Lite both 4/4 after the prompt was changed to explain requirement vs prohibition rules and ask for evidence first (Llama got the "must be worn" rules inverted before). Claude models are AccessDenied on the workshop account.
- Test policy `demo/new_rules.pdf` (`demo/make_new_rules.py`): zone "Test Booth" with 6 conduct rules (two-finger gesture, raised hands, phones, headphones, covering face, walking). Published to DynamoDB as `test-booth` (2026-11-07).
- Vision prompt has explicit gesture/motion rules: before them, Llama 4 Scout flagged "showing the number two" at 0.95 on frames with just a hand at the mouth. After: 0 false alarms in 20 judgments (misses "cover face" on a fist-at-mouth frame). Nova Lite: 1 false alarm (phone). Walking can't be seen in one still frame -> usually null/ok.
- Policy extraction also takes conduct rules ("No phones in use"), not just PPE. map_gear uses whole-word matching (the old substring list made "wear gloves" hit "ear").
- UI: "Rekognition" (blue) vs "AI vision" (yellow #c98500, validated categorical slot 4) badges on chips, Detection logic, incident log and modal; "AI vision checks" panel with the last verdicts and reasons. Chart has an "AI-vision rules" series.
- Endpoints: POST /api/zones/{id}/rules {item, fine, severity}, DELETE /api/zones/{id}/rules/{key}. `/api/zones` adds `ai_rules`. `--check` runs a Bedrock vision probe.

## Frontend (frontend/)
- Rebuild after UI changes: `cd frontend && npm install && npm run build` (runs `tsc` then `vite build` into ../static/app). Dev with hot reload: `npm run dev` (port 5173, proxies /api to :8000).
- Pages (hash routes): `#/monitor` (webcam, Rekognition boxes from 0-1 ratios as % positions, detection logic from /api/tracking, AI vision status, KPIs), `#/incidents` (filters, pagination, CSV export, drawer with photo/boxes/timeline/Bedrock report, Mark resolved, print-to-PDF report), `#/dashboard` (7/30/90 days from /api/dashboard, weekly Bedrock report), `#/policy` (upload/sample -> Textract/Bedrock -> editable review -> approve; save draft in localStorage; live-zone quick rules), `#/settings` (timings, supervisors, Polly voice, min person size, workspace name).
- The camera/analyze loop lives in App (`monitor.ts`), so detection + voice keep running on every page. Voice: `voice.ts` (one phrase per zone, 15 s cooldown, Polly with speechSynthesis fallback).
- Runtime settings: `settings.py` -> `settings.json` (gitignored), applied onto config at startup and on save (GET/PUT /api/settings). Manual resolve: POST /api/violations/{zone}/{ts}/resolve (restarts that item's 20 s clock). Supervisor email: PUT /api/zones/{id}. Range stats: GET /api/dashboard?days=7|30|90 (`stats.dashboard`).
- Design font sizes are small (7-10px) on purpose (design fidelity). The page needs >= 1180px width.

## Incidents (one per event)
- One incident = one event in a zone (`records.py`). Items that go missing while the zone's incident is open join it (gloves + face cover = 1 row, fines summed); each item keeps its own timers, resolve time and fine-avoided status (`items[]`: OPEN / RESOLVED / FIXED_LATE). The incident is RESOLVED when every item is. Stats count exposure/avoided per item.
- Restart-safe: before logging, `incidents._current_incident` looks for the tracker's open incident in the zone, else an OPEN record from the last EXPIRE_SECONDS in DynamoDB (`store.find_open`), and re-attaches (voice again, no new row, no second email). Fixes the old duplicate-row problem caused by `--reload` restarts wiping the in-memory tracker.
- All record read-modify-write goes through `incidents._record_lock`. Manual resolve = `incidents.resolve_all` + restart each item's clock.
- 2026-11-07: the 20 test incidents, their S3 photos and the duplicate zone `chemistry-laboratory-chem-lab` were deleted at the user's request. The remaining `chem-lab` zone uses OSHA defaults (no $18,500 policy fine, no goggles rule).
- Mock camera: person 2 misses the zone's first TWO required items for 36 s of every 60 s.

## Data
- `safesight-zones`: zone_id (PK), name, required (list), alert_email
- `safesight-violations`: zone_id (PK), timestamp (SK), gear_type, osha_rule, fine, status, image_key, report
- S3: `violations/{zone_id}/{timestamp}.jpg`

## Files
- `config.py`: settings, OSHA rules, tuning constants, default zones
- `aws_clients.py`: shared boto3 clients
- `setup_aws.py`: idempotent resource creation (`--check` is read-only)
- `main.py`: FastAPI app (`/api/health`, `/api/zones`, `POST /api/analyze`)
- `ppe.py`: Rekognition call + parser (worn / missing / unknown per person) + mock generator
- `static/index.html`, `app.js`, `style.css`: frontend
- `tracker.py`: 20s voice / 30s notify / per-zone batching / hysteresis / stale-zone pause (pure, in-memory, locked)
- `vision.py`: AI-vision rules via Bedrock image input (see above)
- `test_vision.py`: offline AI-vision checks (parse, debounce, pipeline, fallback, voice)
- `notify.py`: supervisor notification channel (email now, Twilio SMS later)
- `policy.py`, `static/setup.*`: policy PDF ingestion (/setup): Textract (pypdf fallback), Bedrock JSON zones, review, approve to DynamoDB
- `incidents.py`: tracker events -> save violation + photo, one notification per zone, mark RESOLVED. Runs the notification ticker thread and a UI event queue
- `voice.py`: Polly neural voice MP3 with per-phrase cache
- `store.py`: S3 / DynamoDB / SNS, or local `mock_data/` in MOCK_MODE
- `reports.py`: Bedrock converse incident narrative (background thread). Falls back to a template in mock mode or on failure
- `stats.py`: dashboard numbers (today in the viewer's time zone, 7-day zone/gear breakdown, riskiest zone)
- `static/vendor/chart.umd.min.js`: Chart.js 4.4.1, kept locally so the demo works offline
- `test_stats.py`: offline stats + weekly template checks. `test_reports.py`: offline report checks (Bedrock stubbed). `test_tracker.py`: offline smart-logic checks. `test_parser.py`: offline parser checks. `test_analyze.py`: CLI that posts a local image to the server

## Progress
- [x] 1. Skeleton, requirements, .env.example, config.py, setup_aws.py
- [x] 2. POST /analyze (Rekognition) + CLI test script
- [x] 3. Frontend: webcam, 2s snapshots, red/green box overlay, zone dropdown
- [x] 4. Debounce, violation logging (DynamoDB + S3), SNS alerts, cooldown
- [x] 5. Bedrock incident reports, incident log panel, report modal with photo
- [x] 6. Dashboard counters, trends charts, Weekly Report button
- [x] Extra: tiered alerts (now 20s voice / 30s notify), background-person filter (MIN_PERSON_AREA), policy ingestion (/setup)
- [x] Extra: AI-vision rules (Bedrock image checks) + free-text quick rules on /setup
- [x] Extra: new React UI from the Figma design, every control wired to the backend
- [ ] 7. seed_demo.py (sample historical data, clearly flagged)
- [ ] 8. Polish: dark UI, friendly AWS error handling, README + architecture diagram

## Notes
- `MOCK_MODE=true` in .env: no AWS calls, fake Rekognition-shaped responses, default zones from config.
- Status `unknown` = the required body part isn't visible (e.g. hands out of frame). It is not counted as a violation.
- Older note (pre-tiers): one violation per zone+gear while it's OPEN, so there are no duplicates while someone stays non-compliant. Frames with nobody visible or the body part hidden reset the miss streak but don't count as fixed. An open violation not fixed within 5 min expires: it stays OPEN in the DB and a new streak can log a new incident. Tracker state is in memory, so it resets when the server restarts.
- SNS is one topic, so every subscriber gets every alert. The zone's alert_email is printed in the message body. Per-zone routing would need SNS filter policies (not done).
- Reports: facts (rule, fine, time, zone) come from our data. Bedrock writes only summary/risk/fix/prevention as JSON. Never send temperature (current Claude models reject sampling params). Default model is `anthropic.claude-opus-5-5`; `anthropic.claude-haiku-5-5` is the faster, cheaper option. The region may need an inference-profile ID instead.
- Endpoints: GET /api/violations, /api/violations/{zone}/{ts}, /api/violations/{zone}/{ts}/image (mock: local file; real: 5-min presigned S3 redirect).
- Dashboard definitions: exposure today = fines of today's OPEN violations. Avoided today = fines of today's RESOLVED violations. Riskiest zone = most violations in 7 days (ties broken by open exposure). Weekly report: numbers from stats.py, Bedrock writes summary/highlights/3 recommendations (template fallback).
- Chart colors: gear types use the validated categorical palette blue #3987e5 / orange #d95926 / aqua #199e70. Red and green are reserved for violation/compliant status.
- Phases 2-6 were written and verified in mock mode. Real Rekognition is confirmed working (workshop account, us-east-1). Real S3/DynamoDB/SNS/Bedrock are set up by the user and still need confirming.
- Git: project-local repo, remote origin = github.com/espmaguilar/LaunchPad, branch `workspace` (on top of the teammate's aws-access-26-main starter). Never commit .env.
