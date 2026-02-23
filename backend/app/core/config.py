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

settings = Settings()
