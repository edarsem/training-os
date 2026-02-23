# Training OS Roadmap

## Phase 1: Foundation (Completed)

- [x] **Core Data Model**: Strict separation of Intent (Weekly Plan) vs. Reality (Sessions, Day Notes).
- [x] **Backend API**: FastAPI server with SQLite database.
- [x] **CRUD Operations**: Endpoints for managing sessions, day notes, and weekly plans.
- [x] **Aggregation**: Endpoint to fetch a comprehensive weekly summary (`/api/summary/week/{year}/{week}`).
- [x] **Frontend UI**: Minimal Alpine.js + Tailwind CSS calendar view.
- [x] **Prompt Management**: Directory structure for versioned generic and private LLM prompts.

## Phase 2: LLM Integration (Next Steps)

- [ ] **LLM Client**: Integrate an LLM provider (e.g., OpenAI, Anthropic) into the backend.
- [ ] **Weekly Analysis Endpoint**: Create an endpoint that feeds the weekly summary and the `weekly_analysis_v1.txt` prompt to the LLM.
- [ ] **AI Coach UI**: Add a section in the frontend to display the AI coach's analysis and suggestions for the week.
- [ ] **Chat Interface (Optional)**: A simple chat interface to ask questions about past training data.

## Phase 3: Advanced Features & Integrations

- [x] **Data Import/Export**: Ability to import historical data (e.g., from Strava/Coros CSV exports or `.fit` files).
- [ ] **Authentication**: Basic user authentication to support multiple users or secure a deployed instance.
- [ ] **Wearable API Integration**: Direct integration with Coros/Garmin/Strava APIs to automatically sync completed sessions.
- [ ] **Advanced Analytics**: More complex queries (e.g., "Compare the last 3 training blocks").
- [ ] **Dynamic Plan Generation**: Allow the LLM to draft the upcoming week's plan based on past performance and goals.
