from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    PROJECT_NAME: str = "Training OS"

    BASE_DIR: Path = Path(__file__).resolve().parents[2]
    DATA_DIR: Path = BASE_DIR / "data"
    DATABASE_URL: str = f"sqlite:///{(DATA_DIR / 'training_os.db').as_posix()}"
    FIT_IMPORT_DIR: Path = BASE_DIR / "data" / "fit_exports"
    REPORTS_DIR: Path = BASE_DIR / "data" / "reports"
    STRAVA_TOKEN_STORE_PATH: Path = DATA_DIR / "strava_tokens.json"

    STRAVA_CLIENT_ID: str | None = None
    STRAVA_CLIENT_SECRET: str | None = None
    STRAVA_API_BASE_URL: str = "https://www.strava.com/api/v3"
    STRAVA_OAUTH_URL: str = "https://www.strava.com/oauth/token"

    LLM_PROVIDER: str = "mistral"
    MISTRAL_API_KEY: str | None = None
    MISTRAL_API_BASE_URL: str = "https://api.mistral.ai/v1"
    MISTRAL_MODEL: str = "mistral-small-latest"
    LLM_TIMEOUT_SECONDS: int = 45
    LLM_MAX_TOKENS: int = 2000
    LLM_TEMPERATURE: float = 0.5
    LLM_DEFAULT_LANGUAGE: str = "en"
    LLM_USER_LANGUAGE: str | None = None
    LLM_GENERIC_PROMPT_BASENAME: str = "system_base"
    LLM_PRIVATE_PROMPT_BASENAME: str | None = None
    LLM_PRIVATE_TEMPLATE_BASENAME: str = "profile"

settings = Settings()
