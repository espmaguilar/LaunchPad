# LabScribe — an AI lab partner

Upload a lab manual PDF and LabScribe turns it into steps. During the experiment the
camera feed is transcribed into a running log of actions, each action is matched against
the current step, steps auto-advance with voice guidance, and mistakes are explained the
way a friendly lab partner would ("Hold on — you added vinegar before the food coloring.
Step 3 comes first."). At the end it writes a lab record.

> Built for a hackathon (AWS track). Phases A-C are done; PPE safety checks and the AI-written
> lab record are next — see [Status](#status).

## How it works

```
 Lab manual PDF                                   Camera (every 3 s, phases B-E)
      │                                                  │
      ▼                                                  ▼
  Amazon S3 ──► Amazon Textract ──► Amazon Bedrock   Bedrock vision: "what is happening?"
  (original)    (text; pypdf       (steps JSON:          │  (scribe transcript)
                 fallback)          instruction,         ▼
                                    expected action,  Step matcher (Bedrock, text) ──► next step / mistake
                                    visual cues,         │
                                    safety, PPE)         ├─► Amazon Polly voice guidance
      │                                                  ├─► PPE check before safety steps (Bedrock vision)
      ▼                                                  ▼
  Review & approve ──► DynamoDB labscribe-procedures   Lab record (Bedrock) + snapshots in S3
```

- **FastAPI** backend (`main.py`) + **React/Vite** UI (`frontend/`, built into `static/app/`).
- `MOCK_MODE=true` runs everything offline: no AWS calls, local files in `mock_data/`.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in AWS_REGION, BEDROCK_MODEL_ID, S3_BUCKET (or MOCK_MODE=true)

python setup_aws.py --check # read-only: shows what exists + service permissions
python setup_aws.py         # creates the S3 bucket and the labscribe-procedures table

uvicorn main:app --reload --reload-include .env
# open http://localhost:8000
```

The built UI is committed, so Node is only needed to change it:
`cd frontend && npm install && npm run build` (or `npm run dev` for hot reload on :5173).

## Try it

1. **Procedures → Use demo manual: Color-Change Reaction** (`demo/color_change_lab.pdf`,
   household items only: water, food coloring, baking soda, vinegar).
2. Review the 6 extracted steps: edit, reorder, mark safety-critical, set PPE.
3. **Approve & save**.
4. **Lab session** → pick the procedure → **Start camera** → **Start session**. The Scribe panel
   logs what your hands do every 3 s (mock mode scripts the demo without a camera).

## Tests

```bash
python test_procedures.py   # ingestion: Bedrock retry, normalize, validate, demo manual, store
python test_scribe.py       # scribe: parsing, per-frame replies, repeats, skip-when-busy, errors
python test_matcher.py      # step matcher + state machine: advance, mistakes, skips, undo, done-check
python test_parser.py       # Rekognition person parsing (background filter)
```

## Status

- [x] **A. Procedure ingestion** — PDF → S3 → Textract → Bedrock steps → review → DynamoDB
- [x] **B. Scribe** — camera → live action transcript (Bedrock vision, 2 frames every 3 s, no backlog)
- [x] **C. Step matcher + mistake feedback** — auto-advance on 2 confident checks, friendly spoken corrections citing the manual, manual Mark done / Undo
- [ ] D. Safety (PPE via Bedrock vision) + deviations log
- [ ] E. Lab record with "Mistakes & tips"

LabScribe grew out of **SafeSight** (a PPE-compliance camera). That code is in git history
(commit `e7dfecd`); LabScribe reuses its ingestion pipeline, Polly voice, camera loop and UI shell.
