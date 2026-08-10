from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    SQL_ECHO: bool = False

    ORTHANC_URL: str
    ORTHANC_USER: str
    ORTHANC_PASSWORD: str
    ORTHANC_REQUEST_TIMEOUT: float = 30.0

    model_config = SettingsConfigDict(
        env_file=("deploy/.env", "../deploy/.env", "../../deploy/.env"),
        env_file_encoding="utf-8",
    )


settings = Settings()
