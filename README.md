# Training OS

A clean, extensible, well-structured foundation for a personal training management system.

## Vision

The goal of Training OS is not to be a fancy app, but a robust foundation that will later support LLM agents, tool use, and open-source usage. It strictly separates **intent** (what you planned to do) from **reality** (what actually happened).

This separation is crucial for future LLM integration, allowing an AI coach to analyze the delta between the plan and the execution, read your daily notes on fatigue, and suggest adjustments.

## Architecture

The project is divided into two main parts:

1. **Backend (FastAPI + SQLite)**
   - **Domain-Driven Design**: Clear separation between infrastructure (SQLAlchemy models), application logic (Pydantic schemas), and API endpoints.
   - **LLM-Friendly API**: Endpoints like `/api/summary/week/{year}/{week}` return clean, structured JSON that is perfect for LLM tool calling (e.g., OpenAI Functions).
   - **Prompts Directory**: System prompts for future LLM agents are stored as versioned text files in `backend/prompts/`.

2. **Frontend (Alpine.js + Tailwind CSS)**
   - **Minimal & Boring**: Built with zero build steps using Alpine.js and Tailwind via CDN.
   - **Calendar View**: A simple weekly view to see the plan alongside actual sessions and daily notes.

## Data Model

- **Actual Data (Reality)**
  - `Session`: Represents a completed workout (run, trail, strength, etc.) with duration, distance, elevation, and notes.
  - `DayNote`: Free-text notes for a specific day (context, fatigue, stress).
- **Training Plan (Intent)**
  - `WeeklyPlan`: Textual description of the week's goals, target distance, and target sessions.

*Note: Plan and Actual data are never mixed in the same table.*

## How to Run Locally

### 1. Start the Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.
You can view the interactive API documentation at `http://localhost:8000/docs`.

### 2. Start the Frontend

Since the frontend is a simple HTML file, you can serve it using any static file server. For example, using Python:

```bash
cd frontend
python3 -m http.server 3000
```

Then open `http://localhost:3000` in your browser.

## Future LLM Integration

This system is designed to be queried by an LLM. In V2, you can add an endpoint that:

1. Fetches the `WeekSummaryResponse` for the current week.
2. Reads the prompt from `backend/prompts/weekly_analysis_v1.txt`.
3. Sends both to an LLM (e.g., GPT-4 or Claude 3).
4. Returns the AI coach's analysis to the frontend.
