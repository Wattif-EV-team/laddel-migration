"""Tests for the Ampeco API client: status handling and retry/rate-limit wiring."""

from __future__ import annotations

from typing import Any

import pytest

from laddel_migration.clients.ampeco import AmpecoClient, AmpecoError, build_session
from laddel_migration.config import AmpecoSettings

_SETTINGS = AmpecoSettings(
    base_url="https://tenant.example.com",
    api_token="secret",
    requests_per_minute=1000,
)


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None) -> None:
        self.status_code = status_code
        self._body = body
        self.content = b"" if body is None else b"{...}"
        self.text = "" if body is None else str(body)

    def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def get(self, url: str, timeout: float | None = None) -> _FakeResponse:
        self.calls.append(("GET", url, None))
        return self.response

    def post(
        self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
    ) -> _FakeResponse:
        self.calls.append(("POST", url, json))
        return self.response

    def patch(
        self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
    ) -> _FakeResponse:
        self.calls.append(("PATCH", url, json))
        return self.response


def _client(response: _FakeResponse) -> tuple[AmpecoClient, _FakeSession]:
    session = _FakeSession(response)
    return AmpecoClient(_SETTINGS, session=session), session  # type: ignore[arg-type]


def test_create_returns_data_envelope_on_201() -> None:
    client, session = _client(_FakeResponse(201, {"data": {"id": 1007, "name": "X"}}))

    result = client.create("/public-api/resources/partners/v2.0", {"name": "X"})

    assert result == {"id": 1007, "name": "X"}
    assert session.calls == [
        ("POST", "https://tenant.example.com/public-api/resources/partners/v2.0", {"name": "X"})
    ]


def test_create_raises_with_api_message_on_422() -> None:
    client, _ = _client(_FakeResponse(422, {"message": "Validation failed: regNo"}))

    with pytest.raises(AmpecoError, match="Validation failed: regNo"):
        client.create("/public-api/resources/partners/v2.0", {"name": "X"})


def test_update_targets_id_url_and_returns_data() -> None:
    client, session = _client(_FakeResponse(200, {"data": {"id": 1007}}))

    result = client.update("/public-api/resources/partners/v2.0", 1007, {"city": "Oslo"})

    assert result == {"id": 1007}
    assert session.calls[0] == (
        "PATCH",
        "https://tenant.example.com/public-api/resources/partners/v2.0/1007",
        {"city": "Oslo"},
    )


def test_get_returns_data_on_200() -> None:
    client, _ = _client(_FakeResponse(200, {"data": [{"id": 1}]}))

    assert client.get("/public-api/resources/partners/v2.0") == [{"id": 1}]


def test_build_session_sets_bearer_auth_header() -> None:
    session = build_session(_SETTINGS)

    assert session.headers["Authorization"] == "Bearer secret"
    assert session.headers["Content-Type"] == "application/json"


def test_build_session_retries_transient_statuses_for_write_methods() -> None:
    session = build_session(_SETTINGS)
    adapter = session.get_adapter("https://tenant.example.com")
    retry = adapter.max_retries  # type: ignore[attr-defined]

    assert retry.total == 3
    assert 429 in retry.status_forcelist
    assert 503 in retry.status_forcelist
    assert {"GET", "POST", "PATCH"}.issubset(retry.allowed_methods)
