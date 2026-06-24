"""Tests for environment-driven settings, focused on the Ampeco config."""

from __future__ import annotations

import pytest

from laddel_migration.config import Settings, load_settings, require_ampeco

_DB_ENV = {
    "DB_HOST": "db.example.com",
    "DB_USER": "u",
    "DB_PASSWORD": "p",
}


def _set_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _DB_ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("AMPECO_BASE_URL", "AMPECO_API_TOKEN", "AMPECO_REQUESTS_PER_MINUTE"):
        monkeypatch.delenv(key, raising=False)


def test_ampeco_loaded_and_base_url_trailing_slash_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_db_env(monkeypatch)
    monkeypatch.setenv("AMPECO_BASE_URL", "https://tenant.example.com/")
    monkeypatch.setenv("AMPECO_API_TOKEN", "secret-token")

    settings = load_settings(load_env=False)

    assert settings.ampeco is not None
    assert settings.ampeco.base_url == "https://tenant.example.com"
    assert settings.ampeco.api_token == "secret-token"
    assert settings.ampeco.requests_per_minute == 1000


def test_ampeco_requests_per_minute_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_db_env(monkeypatch)
    monkeypatch.setenv("AMPECO_BASE_URL", "https://tenant.example.com")
    monkeypatch.setenv("AMPECO_API_TOKEN", "t")
    monkeypatch.setenv("AMPECO_REQUESTS_PER_MINUTE", "250")

    settings = load_settings(load_env=False)

    assert settings.ampeco is not None
    assert settings.ampeco.requests_per_minute == 250


def test_ampeco_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_db_env(monkeypatch)

    settings = load_settings(load_env=False)

    assert settings.ampeco is None


def test_half_configured_ampeco_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_db_env(monkeypatch)
    monkeypatch.setenv("AMPECO_BASE_URL", "https://tenant.example.com")

    with pytest.raises(RuntimeError, match="both AMPECO_BASE_URL and"):
        load_settings(load_env=False)


def test_require_ampeco_raises_when_missing() -> None:
    settings = Settings(source_db=None, target_db=None, ampeco=None)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="not configured"):
        require_ampeco(settings)
