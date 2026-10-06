from pydantic import field_validator
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

    @field_validator("ORTHANC_WEBHOOK_TOKEN", mode="before")
    @classmethod
    def _empty_webhook_token_is_none(cls, value: str | None) -> str | None:
        # docker-compose always injects this key via `${ORTHANC_WEBHOOK_TOKEN:-}`,
        # so an unset .env value arrives here as "" rather than absent. Without
        # this, verify_webhook_token() treats "" as "a token is configured" and
        # rejects every webhook call (Lua hook and agent-system direct sync
        # alike) with 401, since neither sends an empty token header.
        return value or None

    # How often the periodic reconciliation pass (sync_all_from_orthanc)
    # runs from the application lifespan, independent of the Lua webhook.
    RECONCILIATION_INTERVAL_SECONDS: float = 300.0

    # The owner of every session until authentication exists.
    # Sessions created before the session-graph migration belong to the
    # `legacy` user; set this to `legacy` to keep working in them.
    DEFAULT_USERNAME: str = "default"
    # A pending graph change past this age is `expired`.
    APPROVAL_TTL_H: float = 72.0
    # Agent checkpoints of runs terminal for longer than this are purged.
    AGENT_CHECKPOINT_RETENTION_DAYS: int = 14
    # How often the lifespan sweep runs that purge (0 disables the sweep;
    # POST /agent-checkpoints/purge still works).
    AGENT_CHECKPOINT_PURGE_INTERVAL_SECONDS: float = 3600.0

    model_config = SettingsConfigDict(
        env_file=("deploy/.env", "../deploy/.env", "../../deploy/.env"),
        env_file_encoding="utf-8",
        # deploy/.env is the SHARED compose environment file: it carries
        # POSTGRES_*, CORS_ORIGINS, COMPOSE_PROJECT_DIR and other services'
        # keys as well as this one's. pydantic-settings v2 forbids undeclared
        # inputs by default, so without this the Application API refuses to
        # start the moment an unpinned `pydantic-settings` resolves to a
        # version with that default — which is what happens on every image
        # rebuild, since deploy/Dockerfile.application-api does not pin it.
        extra="ignore",
    )


settings = Settings()
