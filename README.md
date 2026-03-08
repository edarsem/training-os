# Training OS

Logo asset: [logo.png](logo.png)

Training OS is a personalized sports coach and training tracker.

It combines text-based plan and notes along with structured session data from .fit files and Strava sync. This data is analysed to provide insights into training load and performance. The core concept is a **weekly training calendar** with two main layers:

- **Actuals**: sessions + day notes (what happened)
- **Plan**: weekly intent (what should happen)

It is designed for practical weekly use, with load analytics and an MCP-powered coaching backend.

## What it currently includes

- Weekly calendar workflow for sessions and day notes
- Weekly plan as a separate intent layer
- MCP tools for agentic coaching queries
- Analysis views (`analysis.html`, `training-load.html`, `chat-history.html`)
- Strava sync + FIT import
- Training load pipeline (TL / ATL / CTL / ACWR)

## Quick start

### One command

```bash
./scripts/dev_up.sh
```

### Manual steps

```bash
# backend
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# frontend (new terminal)
cd ../frontend
python3 -m http.server 3000
```

- App: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>

## MCP server (coaching tools)

The LLM service runs in MCP mode and can call tool functions over your training data before composing an answer.

Current toolset:

- `get_week_summary`
- `get_day_details`
- `get_session_details`
- `get_block_summary`
- `get_recent_weeks_summary`
- `get_salient_sessions`
- `get_all_races`
- `get_recent_context`
- `submit_final_answer`

Main implementation points:

- Tool schema + executor: `backend/app/llm/mcp_tools.py`
- LLM orchestration: `backend/app/llm/service.py`
- Provider adapters (Mistral, Google Gemini, Echo): `backend/app/llm/providers.py`
- Prompt loading/compilation: `backend/app/llm/prompt_loader.py`, `backend/app/llm/profile_prompt_compiler.py`

## Training load model (TL / ATL / CTL)

Training OS uses **moving-time based TL as the canonical load signal** and derives:

- **ATL**: 7-day exponential response
- **CTL**: 42-day exponential response
- **ACWR**: ATL / CTL

### Empirical COROS-style TL reproduction

The per-hour TL curve is modeled with a **4-parameter softplus regression** (`softplus4`) and tuned empirically to mimic observed COROS behavior. This was the best model after testing various parameterizations.

- Parameters live in `backend/app/core/training_load_defaults.py`
- Daily propagation is in `backend/app/training_load.py`
- Full recompute is supported after imports/updates

This gives a practical, stable approximation while keeping the model transparent and adjustable.

## Strava sync and import

```bash
# refresh new Strava activities
curl -X POST "http://localhost:8000/api/integrations/strava/import/refresh"

# full Strava backfill
curl -X POST "http://localhost:8000/api/integrations/strava/import/backfill?per_page=100&max_pages=40"

# FIT import
cd backend && source venv/bin/activate && python scripts/import_fit.py
```

Key behavior:

- Maps Strava sport types to normalized internal session types
- Imports moving + elapsed durations, distance, elevation, HR metrics
- Preserves manually edited fields where relevant (e.g., race flag)
- Supports incremental refresh and full history backfill

## Tech stack and libraries

Backend:

- FastAPI, Uvicorn
- SQLAlchemy (SQLite)
- Pydantic / pydantic-settings
- `fitparse` / `fitdecode` for FIT ingestion

Frontend:

- Alpine.js
- Tailwind CSS (CDN)
- Chart.js
- marked + DOMPurify for coach markdown rendering

## Project direction

Roadmap is intentionally paused for now. See [ROADMAP.md](ROADMAP.md) for the current maintenance-mode status.
