from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.api import router as api_router
from app.core.config import settings
from app.core.database import engine, Base, run_sqlite_schema_updates
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

@app.get("/")
def root():
    return {"message": "Welcome to Training OS API. See /docs for documentation."}
