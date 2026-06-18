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
class Settings:
    """Top-level application settings."""

    source_db: DatabaseSettings
    target_db: DatabaseSettings


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
    return Settings(source_db=source_db, target_db=target_db)
