from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.api.api import router as api_router, refresh_strava_activities_until_known
from app.core.config import settings
from app.core.database import engine, Base, SessionLocal, run_sqlite_schema_updates
from app.llm.profile_prompt_compiler import ensure_compiled_profile_prompt

# Create database tables
Base.metadata.create_all(bind=engine)
run_sqlite_schema_updates()
ensure_compiled_profile_prompt(prompts_root=settings.BASE_DIR / "prompts", force=True)

app = FastAPI(
    title="Training OS API",
    description="A clean, extensible foundation for personal training management.",
    version="1.0.0"
)

# Configure CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For V1, allow all. In prod, restrict to frontend URL.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.on_event("startup")
def startup_auto_refresh_strava() -> None:
    if not settings.STRAVA_AUTO_REFRESH_ON_STARTUP:
        return

    db = SessionLocal()
    try:
        result = refresh_strava_activities_until_known(
            per_page=int(settings.STRAVA_AUTO_REFRESH_PER_PAGE),
            max_pages=int(settings.STRAVA_AUTO_REFRESH_MAX_PAGES),
            db=db,
        )
        print(
            "[startup] Strava refresh completed "
            f"(imported={result.imported_count}, updated={result.updated_count}, skipped={result.skipped_count}, "
            f"pages={result.pages_fetched})"
        )
    except HTTPException as exc:
        print(f"[startup] Strava auto-refresh skipped/failed: {exc.detail}")
    except Exception as exc:
        print(f"[startup] Strava auto-refresh failed: {exc}")
    finally:
        db.close()

@app.get("/")
def root():
    return {"message": "Welcome to Training OS API. See /docs for documentation."}
