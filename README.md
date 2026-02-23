# Training OS

Training OS is a personal training tracker with a clean split between:

- **what happened** (sessions + day notes), and
- **what was planned** (weekly plan text).

It is intentionally simple today, and ready to plug into LLM tooling later.

## What this repo currently provides

- FastAPI backend with SQLite storage
- Weekly calendar UI (Alpine + Tailwind via CDN)
- FIT importer with parser fallback (`fitparse` then `fitdecode`)
- Weekly analysis page with 20-week metric charts
- Prompt folders split into generic/public and private/local

## Local setup

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- API root: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

### Frontend

```bash
cd frontend
python3 -m http.server 3000
```

- App: `http://localhost:3000`
- Analysis: `http://localhost:3000/analysis.html`

## Data import workflow

1. Put `.fit` files in `backend/data/fit_exports/`
2. Run:

```bash
cd backend
source venv/bin/activate
python scripts/import_fit.py
```

1. Check reports in `backend/data/reports/`

## Repository conventions

- Runtime data stays local under `backend/data/` (ignored in git)
- Private prompts live in `backend/prompts/private/` (ignored except README)
- Frontend is static (no npm tooling required)

## API surface (V1)

- `GET /api/sessions?start_date=...&end_date=...`
- `POST /api/sessions`
- `PUT /api/sessions/{id}`
- `DELETE /api/sessions/{id}`
- `GET /api/day-notes?start_date=...&end_date=...`
- `POST /api/day-notes`
- `GET /api/plans/{year}/{week_number}`
- `POST /api/plans`
- `GET /api/summary/week/{year}/{week_number}`

## Roadmap

Execution plan and upcoming milestones are tracked in `ROADMAP.md`.
