"""Typed runtime configuration loaded from environment variables.

Secrets and connection details are read from the environment (optionally
populated from a local ``.env`` file). Nothing here depends on the current
working directory beyond the optional ``.env`` convenience load.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set it in your environment or in a local .env file."
        )
    return value


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """MySQL connection settings for a single database."""

    host: str
    port: int
    user: str
    password: str
    database: str

    @property
    def safe_dsn(self) -> str:
        """A DSN-style string with the password redacted, for logging."""
        return f"mysql://{self.user}:***@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True, slots=True)
class AmpecoSettings:
    """Connection settings for the Ampeco Public API."""

    base_url: str
    api_token: str
    requests_per_minute: int = 1000

    @property
    def safe_base_url(self) -> str:
        """The base URL, safe to log (it carries no secret)."""
        return self.base_url


@dataclass(frozen=True, slots=True)
class Settings:
    """Top-level application settings.

    ``ampeco`` is ``None`` when the Ampeco environment variables are not set, so
    database-only commands (build, verify, sql) work without API credentials.
    Use :func:`require_ampeco` from code that needs to call the API.
    """

    source_db: DatabaseSettings
    target_db: DatabaseSettings
    ampeco: AmpecoSettings | None


def load_settings(*, load_env: bool = True) -> Settings:
    """Build :class:`Settings` from environment variables.

    Parameters
    ----------
    load_env:
        When ``True`` (default) load variables from a ``.env`` file if present.
    """
    if load_env:
        load_dotenv()

    host = _require("DB_HOST")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = _require("DB_USER")
    password = _require("DB_PASSWORD")

    source_db = DatabaseSettings(
        host=host,
        port=port,
        user=user,
        password=password,
        database=os.environ.get("DB_SOURCE_NAME", "laddel"),
    )
    target_db = DatabaseSettings(
        host=host,
        port=port,
        user=user,
        password=password,
        database=os.environ.get("DB_TARGET_NAME", "target"),
    )
    ampeco = _load_ampeco()
    return Settings(source_db=source_db, target_db=target_db, ampeco=ampeco)


def _load_ampeco() -> AmpecoSettings | None:
    """Build :class:`AmpecoSettings` from the environment, or ``None`` if unset.

    Returns ``None`` only when *neither* Ampeco variable is set. If exactly one
    is set the configuration is half-finished, so we raise rather than silently
    proceed without credentials.
    """
    base_url = os.environ.get("AMPECO_BASE_URL")
    api_token = os.environ.get("AMPECO_API_TOKEN")
    if not base_url and not api_token:
        return None
    if not base_url or not api_token:
        raise RuntimeError(
            "Incomplete Ampeco configuration: set both AMPECO_BASE_URL and "
            "AMPECO_API_TOKEN (or neither)."
        )
    return AmpecoSettings(
        base_url=base_url.rstrip("/"),
        api_token=api_token,
        requests_per_minute=int(os.environ.get("AMPECO_REQUESTS_PER_MINUTE", "1000")),
    )


def require_ampeco(settings: Settings) -> AmpecoSettings:
    """Return the Ampeco settings, raising a clear error if they are unset."""
    if settings.ampeco is None:
        raise RuntimeError(
            "Ampeco API is not configured. Set AMPECO_BASE_URL and "
            "AMPECO_API_TOKEN in your environment or .env file."
        )
    return settings.ampeco
