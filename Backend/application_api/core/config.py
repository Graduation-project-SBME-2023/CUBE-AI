from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    SQL_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: float = 10.0

    ORTHANC_URL: str
    ORTHANC_USER: str
    ORTHANC_PASSWORD: str
    ORTHANC_REQUEST_TIMEOUT: float = 30.0

    # Shared secret the Orthanc Lua hook must present on POST
    # /sync/orthanc-webhook. None disables the check, which is only
    # acceptable when the endpoint is not reachable from outside the
    # docker-compose network (see database_audit.md D-30 for the tracked
    # gap in enforcing that at the ingress).
    ORTHANC_WEBHOOK_TOKEN: str | None = None

    # How often the periodic reconciliation pass (sync_all_from_orthanc)
    # runs from the application lifespan, independent of the Lua webhook.
    RECONCILIATION_INTERVAL_SECONDS: float = 300.0

    model_config = SettingsConfigDict(
        env_file=("deploy/.env", "../deploy/.env", "../../deploy/.env"),
        env_file_encoding="utf-8",
    )


settings = Settings()
