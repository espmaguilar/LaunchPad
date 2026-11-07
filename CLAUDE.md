# SafeSight: build plan & progress

AI safety-gear compliance camera that puts a dollar amount on every violation.
Hackathon (AWS track), about 9h of build time. **A working demo comes before completeness.** Build in phases and stop after each one for the user to test.

## Stack
- Python 3.11+ (local venv runs 3.14), FastAPI, boto3, uvicorn (local)
- One static HTML page + vanilla JS + Chart.js, served by FastAPI
- AWS: Rekognition `detect_protective_equipment`, S3, DynamoDB, SNS, Bedrock Runtime `converse`
- Config lives in `.env` (gitignored). AWS credentials come from the standard chain, never from code.

## Hard facts (do not deviate)
- Rekognition PPE detects only FACE_COVER, HAND_COVER, HEAD_COVER. Never claim goggles or vests.
- FACE->FACE_COVER, HEAD->HEAD_COVER, LEFT_HAND/RIGHT_HAND->HAND_COVER. Gear counts as worn only if `CoversBodyPart.Value` is true.
- MinConfidence 80. Pass the zone's required types.
- BoundingBox values are 0-1 ratios. Scale them to the canvas size.
- The text must say "potential fine exposure", never "you will be fined".
- OSHA table is in `config.py` (TODO: verify penalties on osha.gov before the demo).

## Smart logic (tiered escalation; timings live in config.py, DEMO_MODE in .env)
- Debounce: the same missing gear in a zone for 3 consecutive frames logs a violation.
- Tier 1 (instant): Polly voice warning "Attention: {gear} required in the {zone}." No email. Repeats once if still missing after 10s. Polly MP3s are cached per phrase (memory + audio_cache/). The browser falls back to speechSynthesis. There is a mute toggle.
- Tier 2: still OPEN after ALERT_GRACE_SECONDS (demo 20s / prod 120s) sends ONE supervisor email.
- Tier 3: still OPEN after ESCALATION_SECONDS (demo 60s / prod 600s) sends ONE escalation email. Never more than 2 emails per violation.
- Hysteresis: resolves only after RESOLVE_SECONDS (demo 10s / prod 30s) of compliance with no miss. Emails are held while a fix is being confirmed (at most RESOLVE_SECONDS+5).
- Fine avoided: fixed within 5 min of logging means RESOLVED. A later fix means `fixed_at` is set but the record stays OPEN (exposure stands).
- Stale zone: no frames for 10s pauses that zone's email timers ("Camera offline"). Paused time doesn't count.
- The email tiers run in one daemon thread started in FastAPI lifespan. update()/tick()/status() share one lock. Don't use --workers > 1.
- The old 60s alert cooldown was replaced by the grace period plus hysteresis.

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
- `tracker.py`: debounce / tiers / hysteresis / stale-zone pause (pure, in-memory, locked)
- `incidents.py`: tracker events -> save violation + photo, emails, mark RESOLVED. Runs the escalation ticker thread and a UI event queue
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
- [ ] 7. seed_demo.py (sample historical data, clearly flagged)
- [ ] 8. Polish: dark UI, friendly AWS error handling, README + architecture diagram

## Notes
- `MOCK_MODE=true` in .env: no AWS calls, fake Rekognition-shaped responses, default zones from config. Person 2 misses the zone's first required gear for 12s out of every 24s.
- Status `unknown` = the required body part isn't visible (e.g. hands out of frame). It is not counted as a violation.
- Older note (pre-tiers): one violation per zone+gear while it's OPEN, so there are no duplicates while someone stays non-compliant. Frames with nobody visible or the body part hidden reset the miss streak but don't count as fixed. An open violation not fixed within 5 min expires: it stays OPEN in the DB and a new streak can log a new incident. Tracker state is in memory, so it resets when the server restarts.
- SNS is one topic, so every subscriber gets every alert. The zone's alert_email is printed in the message body. Per-zone routing would need SNS filter policies (not done).
- Reports: facts (rule, fine, time, zone) come from our data. Bedrock writes only summary/risk/fix/prevention as JSON. Never send temperature (current Claude models reject sampling params). Default model is `anthropic.claude-opus-5-5`; `anthropic.claude-haiku-5-5` is the faster, cheaper option. The region may need an inference-profile ID instead.
- Endpoints: GET /api/violations, /api/violations/{zone}/{ts}, /api/violations/{zone}/{ts}/image (mock: local file; real: 5-min presigned S3 redirect).
- Dashboard definitions: exposure today = fines of today's OPEN violations. Avoided today = fines of today's RESOLVED violations. Riskiest zone = most violations in 7 days (ties broken by open exposure). Weekly report: numbers from stats.py, Bedrock writes summary/highlights/3 recommendations (template fallback).
- Chart colors: gear types use the validated categorical palette blue #3987e5 / orange #d95926 / aqua #199e70. Red and green are reserved for violation/compliant status.
- Phases 2-6 were written and verified in mock mode. Real Rekognition is confirmed working (workshop account, us-east-1). Real S3/DynamoDB/SNS/Bedrock are set up by the user and still need confirming.
- Git: project-local repo, remote origin = github.com/espmaguilar/LaunchPad, branch `workspace` (on top of the teammate's aws-access-26-main starter). Never commit .env.
