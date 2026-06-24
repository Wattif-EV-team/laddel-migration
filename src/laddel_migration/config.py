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
class SiteTrackerSettings:
    """Connection settings for the SiteTracker (Salesforce) REST API.

    Auth is the standard Salesforce OAuth2 *password grant* against a Connected
    App; the client fetches a bearer token from ``token_url`` and calls the
    sObject REST API under ``instance_url``.
    """

    token_url: str
    instance_url: str
    client_id: str
    client_secret: str
    username: str
    password: str
    api_version: str = "v63.0"
    requests_per_minute: int = 1000

    @property
    def safe_instance_url(self) -> str:
        """The instance URL, safe to log (it carries no secret)."""
        return self.instance_url


@dataclass(frozen=True, slots=True)
class Settings:
    """Top-level application settings.

    ``ampeco`` / ``sitetracker`` are ``None`` when their environment variables
    are not set, so database-only commands (build, verify, sql) work without any
    API credentials. Use :func:`require_ampeco` / :func:`require_sitetracker`
    from code that needs to call the respective API.
    """

    source_db: DatabaseSettings
    target_db: DatabaseSettings
    ampeco: AmpecoSettings | None
    sitetracker: SiteTrackerSettings | None = None


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
    sitetracker = _load_sitetracker()
    return Settings(
        source_db=source_db,
        target_db=target_db,
        ampeco=ampeco,
        sitetracker=sitetracker,
    )


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


# Environment variables that configure the SiteTracker (Salesforce) API. All
# except the api version are required together; the api version has a default.
_SITETRACKER_REQUIRED = (
    ("token_url", "SITETRACKER_TOKEN_URL"),
    ("instance_url", "SITETRACKER_INSTANCE_URL"),
    ("client_id", "SITETRACKER_CLIENT_ID"),
    ("client_secret", "SITETRACKER_CLIENT_SECRET"),
    ("username", "SITETRACKER_USERNAME"),
    ("password", "SITETRACKER_PASSWORD"),
)


def _load_sitetracker() -> SiteTrackerSettings | None:
    """Build :class:`SiteTrackerSettings` from the environment, or ``None``.

    Returns ``None`` only when *none* of the required variables are set. If some
    but not all are set the configuration is half-finished, so we raise rather
    than silently proceed without credentials.
    """
    values = {field: os.environ.get(var) for field, var in _SITETRACKER_REQUIRED}
    present = [v for v in values.values() if v]
    if not present:
        return None
    missing = [
        var
        for (field, var), value in zip(_SITETRACKER_REQUIRED, values.values(), strict=True)
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Incomplete SiteTracker configuration: missing "
            + ", ".join(missing)
            + " (set all SITETRACKER_* variables or none)."
        )
    return SiteTrackerSettings(
        token_url=values["token_url"],  # type: ignore[arg-type]
        instance_url=values["instance_url"].rstrip("/"),  # type: ignore[union-attr]
        client_id=values["client_id"],  # type: ignore[arg-type]
        client_secret=values["client_secret"],  # type: ignore[arg-type]
        username=values["username"],  # type: ignore[arg-type]
        password=values["password"],  # type: ignore[arg-type]
        api_version=os.environ.get("SITETRACKER_API_VERSION", "v63.0"),
        requests_per_minute=int(os.environ.get("SITETRACKER_REQUESTS_PER_MINUTE", "1000")),
    )


def require_sitetracker(settings: Settings) -> SiteTrackerSettings:
    """Return the SiteTracker settings, raising a clear error if they are unset."""
    if settings.sitetracker is None:
        raise RuntimeError(
            "SiteTracker API is not configured. Set the SITETRACKER_* variables "
            "(token URL, instance URL, client id/secret, username, password) in "
            "your environment or .env file."
        )
    return settings.sitetracker
