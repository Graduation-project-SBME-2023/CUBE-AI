from pydantic_settings import BaseSettings
from os import getenv

class Settings(BaseSettings):
    DATABASE_URL: str = getenv("DATABASE_URL", "postgresql+asyncpg://workflow_user:password@postgres:5432/workflow_db")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()