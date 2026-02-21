from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Training OS"
    DATABASE_URL: str = "sqlite:///./training_os.db"

settings = Settings()
