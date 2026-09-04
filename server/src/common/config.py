"""T-M2-01: Environment configuration with startup validation."""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        default="dev-secret-do-not-use-in-production",
        validation_alias=AliasChoices("SECRET_KEY", "secret_key"),
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

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


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
