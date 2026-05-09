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
    api_log_level: str = Field(default="info", alias="API_LOG_LEVEL")
    api_cors_origins: str = Field(default="", alias="API_CORS_ORIGINS")

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

    # Alert lifecycle cadence -- how long an alert can stay open before we
    # nag the recipients (reminder) and before we escalate to admins. Set
    # either to 0 to disable that stage entirely.
    alert_reminder_hours: int = Field(default=24, alias="ALERT_REMINDER_HOURS")
    alert_escalation_hours: int = Field(default=48, alias="ALERT_ESCALATION_HOURS")

    # Leading-indicator thresholds for the "near_*" alert types. The
    # near_capacity alert fires when inventory >= ceil(max * pct/100) but
    # below the hard cap; near_low_inventory fires when inventory is within
    # ``buffer`` boxes of the minimum but still above it.
    near_capacity_percent: int = Field(default=90, alias="NEAR_CAPACITY_PERCENT")
    near_low_inventory_buffer: int = Field(
        default=10, alias="NEAR_LOW_INVENTORY_BUFFER"
    )
    # A box that has been in 'received' state for at least this many days
    # is considered stuck; we open one box_stuck alert per warehouse with
    # ``value`` = the number of stuck boxes. 0 disables this trigger.
    box_stuck_threshold_days: int = Field(
        default=30, alias="BOX_STUCK_THRESHOLD_DAYS"
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
