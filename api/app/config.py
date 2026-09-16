from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://warehouse:warehouse@db:5432/warehouse",
        alias="DATABASE_URL",
    )
    # SQLAlchemy connection-pool tuning. The defaults assume a single
    # uvicorn worker with a handful of SSE subscribers + a couple of
    # APScheduler jobs; bump these if you run multiple workers or see
    # ``QueuePool limit ... reached`` in the logs. ``pool_recycle`` exists
    # because long-idle connections eventually get dropped by Postgres /
    # PgBouncer and we'd rather rotate them ourselves than have a request
    # fail with a stale-socket error.
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(default=1800, alias="DB_POOL_RECYCLE")
    api_log_level: str = Field(default="info", alias="API_LOG_LEVEL")
    api_cors_origins: str = Field(default="", alias="API_CORS_ORIGINS")

    # Private S3-compatible storage for ERP delivery/return documents.
    object_storage_endpoint: str = Field(default="", alias="OBJECT_STORAGE_ENDPOINT")
    object_storage_region: str = Field(default="us-east-1", alias="OBJECT_STORAGE_REGION")
    object_storage_bucket: str = Field(
        default="warehouse-documents", alias="OBJECT_STORAGE_BUCKET"
    )
    object_storage_access_key: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY")
    object_storage_secret_key: str = Field(default="", alias="OBJECT_STORAGE_SECRET_KEY")
    object_storage_secure: bool = Field(default=False, alias="OBJECT_STORAGE_SECURE")
    document_max_bytes: int = Field(default=10 * 1024 * 1024, alias="DOCUMENT_MAX_BYTES")

    entra_tenant_id: str = Field(default="", alias="ENTRA_TENANT_ID")
    entra_client_id: str = Field(default="", alias="ENTRA_CLIENT_ID")
    entra_client_secret: str = Field(default="", alias="ENTRA_CLIENT_SECRET")
    entra_api_audience: str = Field(default="", alias="ENTRA_API_AUDIENCE")

    alert_email_from: str = Field(default="", alias="ALERT_EMAIL_FROM")
    alert_email_to: str = Field(default="", alias="ALERT_EMAIL_TO")

    # Public base URL of the SPA. Used to build deep-links from alert emails
    # back into the app (e.g. https://warehouse.example.com -> .../alerts/42).
    # Leave empty in dev or behind reverse proxies that aren't reachable from
    # the recipient's network -- the CTA button is hidden when unset.
    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")

    # Leading-indicator thresholds for the "near_*" alert types. The
    # near_capacity alert fires when inventory >= ceil(max * pct/100) but
    # below the hard cap; near_low_inventory fires when inventory is within
    # ``buffer`` boxes of the minimum but still above it.
    near_capacity_percent: int = Field(default=90, alias="NEAR_CAPACITY_PERCENT")
    near_low_inventory_buffer: int = Field(
        default=10, alias="NEAR_LOW_INVENTORY_BUFFER"
    )
    # Local-time configuration used by the productivity feature.
    # ``app_timezone`` defines the wall-clock day boundary for "daily"
    # productivity numbers; an unknown zone silently falls back to UTC
    # (see app.routers.productivity._today_in_app_tz).
    app_timezone: str = Field(default="UTC", alias="APP_TIMEZONE")
    # Hour of day (0-23, in ``app_timezone``) at which the end-of-day
    # productivity report job runs. Set to 18:00 by default so a
    # standard 9-5 day is fully captured.
    productivity_report_hour: int = Field(
        default=18, alias="PRODUCTIVITY_REPORT_HOUR"
    )
    # When False (default), warehouses with zero productivity entries
    # for the day are skipped so recipients don't get a fleet of empty
    # "0 pages today" emails. Set to True to always send a per-warehouse
    # report regardless of whether anything was logged.
    productivity_report_include_empty: bool = Field(
        default=False, alias="PRODUCTIVITY_REPORT_INCLUDE_EMPTY"
    )

    # Local (break-glass) auth.
    local_jwt_secret: str = Field(default="", alias="LOCAL_JWT_SECRET")
    local_jwt_ttl_minutes: int = Field(default=480, alias="LOCAL_JWT_TTL_MINUTES")
    bootstrap_admin_username: str = Field(
        default="admin", alias="BOOTSTRAP_ADMIN_USERNAME"
    )
    bootstrap_admin_password: str = Field(
        default="ChangeMe!2026", alias="BOOTSTRAP_ADMIN_PASSWORD"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def alert_email_to_list(self) -> list[str]:
        return [e.strip() for e in self.alert_email_to.split(",") if e.strip()]

    @property
    def jwks_url(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self.entra_tenant_id}/discovery/v2.0/keys"
        )

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"

    @property
    def issuer_v1(self) -> str:
        return f"https://sts.windows.net/{self.entra_tenant_id}/"

    @property
    def auth_configured(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_client_id and self.entra_api_audience)

    @property
    def local_auth_enabled(self) -> bool:
        return bool(self.local_jwt_secret)

    @property
    def object_storage_configured(self) -> bool:
        return bool(
            self.object_storage_endpoint
            and self.object_storage_bucket
            and self.object_storage_access_key
            and self.object_storage_secret_key
        )

    @property
    def graph_configured(self) -> bool:
        # ALERT_EMAIL_TO is no longer required here -- the alert dispatcher
        # builds the recipient list from per-warehouse ACL and falls back to
        # ALERT_EMAIL_TO only if that comes back empty. We only need the
        # tenant + client credentials and a mailbox to send from.
        return (
            self.auth_configured
            and bool(self.entra_client_secret)
            and bool(self.alert_email_from)
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
