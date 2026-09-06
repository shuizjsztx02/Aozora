"""T-M2-01: Environment configuration with startup validation."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default value for ``secret_key`` — must be overridden in production.
_DEV_SECRET_KEY = "dev-secret-do-not-use-in-production"


class Settings(BaseSettings):
    """Validated application settings.

    Each field reads from the environment variable matching its alias
    (case-insensitive via pydantic-settings).  Missing required fields
    cause a ``ValidationError`` at load time, which prevents startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- runtime ---
    env: str = Field(
        default="development",
        validation_alias=AliasChoices("ENV", "env"),
    )

    # --- database (REQUIRED — no default) ---
    database_url: str = Field(
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )

    # --- security ---
    secret_key: str = Field(
        default=_DEV_SECRET_KEY,
        validation_alias=AliasChoices("SECRET_KEY", "secret_key"),
    )

    # --- jwt ---
    jwt_algorithm: Literal["HS256", "RS256", "ES256"] = Field(
        default="HS256",
        validation_alias=AliasChoices("JWT_ALGORITHM", "jwt_algorithm"),
    )
    access_token_expire_minutes: int = Field(
        default=15,
        gt=0,
        validation_alias=AliasChoices("ACCESS_TOKEN_EXPIRE_MINUTES", "access_token_expire_minutes"),
    )
    refresh_token_expire_days: int = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices("REFRESH_TOKEN_EXPIRE_DAYS", "refresh_token_expire_days"),
    )

    # --- auth ---
    dev_auth_code: str = Field(
        default="000000",
        validation_alias=AliasChoices("DEV_AUTH_CODE", "dev_auth_code"),
    )

    # --- redis (optional for first release) ---
    redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
    )

    # --- api ---
    api_prefix: str = Field(
        default="/api/v1",
        validation_alias=AliasChoices("API_PREFIX", "api_prefix"),
    )

    # --- cors ---
    cors_origins: str = Field(
        default="*",
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"),
    )

    # --- logging ---
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "log_level"),
    )

    # --- exports ---
    # N-05: export directory. Defaults to a /tmp sub-directory (ephemeral
    # on most OSes); override via EXPORT_DIR for persistent deployments.
    # Files are auto-deleted when their export expires (N-06).
    export_dir: str = Field(
        default="",
        validation_alias=AliasChoices("EXPORT_DIR", "export_dir"),
    )

    # --- ai gateway (T-M4-01) ---
    # D-EXEC-04 (AI account/quota/limits/rotation) is pending external
    # confirmation; defaults below are conservative placeholders.
    # A missing key does NOT block startup — the gateway degrades to 503
    # at runtime so dev/staging environments run without provider access.
    ai_provider_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_PROVIDER_API_KEY", "ai_provider_api_key"),
    )
    ai_provider_model: str = Field(
        default="qwen3.7-flash",
        validation_alias=AliasChoices("AI_PROVIDER_MODEL", "ai_provider_model"),
    )
    ai_provider_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices("AI_PROVIDER_BASE_URL", "ai_provider_base_url"),
    )
    # Per-attempt timeout. Contract (APP-CONTEXT §6.4): the client cancels
    # at 25 s, so 2 × timeout + overhead must stay below that budget.
    ai_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias=AliasChoices("AI_TIMEOUT_SECONDS", "ai_timeout_seconds"),
    )
    # Per-user burst protection — applies to ALL users (guests and active);
    # it guards the provider against runaway loops, not a product quota.
    ai_rate_limit_per_minute: int = Field(
        default=10,
        gt=0,
        validation_alias=AliasChoices("AI_RATE_LIMIT_PER_MINUTE", "ai_rate_limit_per_minute"),
    )
    # Daily quota for GUEST users only; ACTIVE users have no daily cap
    # (product decision 2026-09-06). Counted per calendar day in the
    # user's own timezone (User.timezone).
    ai_daily_quota_guest: int = Field(
        default=20,
        gt=0,
        validation_alias=AliasChoices("AI_DAILY_QUOTA_GUEST", "ai_daily_quota_guest"),
    )
    # Cost accounting: price per 1M tokens in micro-CNY (1e-6 元).
    # 0 = not priced yet (until D-EXEC-04 confirms real rates).
    ai_price_per_1m_tokens: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("AI_PRICE_PER_1M_TOKENS", "ai_price_per_1m_tokens"),
    )

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check_production_safety(self) -> "Settings":
        """Block obviously-insecure defaults when running in production."""
        if self.env != "production":
            return self
        if self.secret_key == _DEV_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be set to a unique, unpredictable value in production"
            )
        if self.dev_auth_code:
            raise ValueError(
                "DEV_AUTH_CODE must be empty in production; SMS provider not yet integrated"
            )
        if self.cors_origins.strip() == "*":
            raise ValueError(
                "CORS_ORIGINS must be an explicit allow-list in production"
            )
        return self


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def load_config() -> Settings:
    """Load and validate configuration from environment / .env file."""
    return Settings()


def get_config() -> Settings:
    """Return cached settings, loading on first call."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = load_config()
    return _settings


def set_config(settings: Settings) -> None:
    """Override settings (used in tests)."""
    global _settings  # noqa: PLW0603
    _settings = settings


def reset_config() -> None:
    """Clear cached settings (used in tests)."""
    global _settings  # noqa: PLW0603
    _settings = None
