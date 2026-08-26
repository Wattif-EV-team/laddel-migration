"""Tests for the SiteTracker client: status handling, 401 re-auth, token fetch."""

from __future__ import annotations

from typing import Any

import pytest

from laddel_migration.clients.sitetracker import (
    SiteTrackerClient,
    SiteTrackerError,
    build_session,
    fetch_token,
)
from laddel_migration.config import SiteTrackerSettings

_SETTINGS = SiteTrackerSettings(
    token_url="https://login.example.com/services/oauth2/token",
    instance_url="https://acme.my.salesforce.com",
    client_id="cid",
    client_secret="csecret",
    username="user@acme.no",
    password="pw",
    api_version="v63.0",
    requests_per_minute=1000,
)


class _FakeResponse:
    def __init__(self, status_code: int, body: Any | None) -> None:
        self.status_code = status_code
        self._body = body
        self.content = b"" if body is None else b"{...}"
        self.text = "" if body is None else str(body)

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeSession:
    """Returns a queued response per call, recording each request."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _next(self) -> _FakeResponse:
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

    def get(self, url: str, timeout: float | None = None) -> _FakeResponse:
        self.calls.append(("GET", url, None))
        return self._next()

    def post(
        self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
    ) -> _FakeResponse:
        self.calls.append(("POST", url, json))
        return self._next()

    def patch(
        self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None
    ) -> _FakeResponse:
        self.calls.append(("PATCH", url, json))
        return self._next()

    def delete(self, url: str, timeout: float | None = None) -> _FakeResponse:
        self.calls.append(("DELETE", url, None))
        return self._next()


def _client(responses: list[_FakeResponse]) -> tuple[SiteTrackerClient, _FakeSession]:
    session = _FakeSession(responses)
    client = SiteTrackerClient(_SETTINGS, session=session, token="preset")  # type: ignore[arg-type]
    return client, session


def test_create_returns_body_with_id_on_201() -> None:
    client, session = _client([_FakeResponse(201, {"id": "001X", "success": True})])

    result = client.create("/sobjects/Account", {"Name": "Acme"})

    assert result == {"id": "001X", "success": True}
    assert session.calls == [
        (
            "POST",
            "https://acme.my.salesforce.com/services/data/v63.0/sobjects/Account",
            {"Name": "Acme"},
        )
    ]


def test_update_targets_id_url_and_returns_none_on_204() -> None:
    client, session = _client([_FakeResponse(204, None)])

    result = client.update("/sobjects/Account", "001X", {"BillingCity": "Oslo"})

    assert result is None
    assert session.calls[0] == (
        "PATCH",
        "https://acme.my.salesforce.com/services/data/v63.0/sobjects/Account/001X",
        {"BillingCity": "Oslo"},
    )


def test_delete_targets_id_url_and_returns_none_on_204() -> None:
    client, session = _client([_FakeResponse(204, None)])

    result = client.delete("/sobjects/Account", "001X")

    assert result is None
    assert session.calls[0] == (
        "DELETE",
        "https://acme.my.salesforce.com/services/data/v63.0/sobjects/Account/001X",
        None,
    )


def test_delete_raises_with_salesforce_error_message_on_404() -> None:
    client, _ = _client(
        [_FakeResponse(404, [{"message": "Entity is deleted", "errorCode": "NOT_FOUND"}])]
    )

    with pytest.raises(SiteTrackerError, match="got 404"):
        client.delete("/sobjects/Account", "001X")


def test_create_raises_with_salesforce_error_message() -> None:
    client, _ = _client(
        [_FakeResponse(400, [{"message": "Required fields missing", "errorCode": "X"}])]
    )

    with pytest.raises(SiteTrackerError, match="Required fields missing"):
        client.create("/sobjects/Account", {"Name": "Acme"})


def test_401_triggers_reauth_then_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = iter(["fresh-token"])
    monkeypatch.setattr(
        "laddel_migration.clients.sitetracker.fetch_token", lambda *a, **k: next(tokens)
    )
    client, session = _client(
        [_FakeResponse(401, None), _FakeResponse(201, {"id": "001Y", "success": True})]
    )

    result = client.create("/sobjects/Account", {"Name": "Acme"})

    assert result == {"id": "001Y", "success": True}
    assert len(session.calls) == 2  # first 401, then retry
    assert session.headers["Authorization"] == "Bearer fresh-token"


def test_fetch_token_returns_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, data: dict[str, Any], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["data"] = data
        return _FakeResponse(200, {"access_token": "abc123"})

    monkeypatch.setattr("laddel_migration.clients.sitetracker.requests.post", fake_post)

    token = fetch_token(_SETTINGS)

    assert token == "abc123"
    assert captured["url"] == _SETTINGS.token_url
    assert captured["data"]["grant_type"] == "password"
    assert captured["data"]["client_id"] == "cid"


def test_fetch_token_raises_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "laddel_migration.clients.sitetracker.requests.post",
        lambda *a, **k: _FakeResponse(400, {"error_description": "bad creds"}),
    )

    with pytest.raises(SiteTrackerError, match="bad creds"):
        fetch_token(_SETTINGS)


def test_build_session_sets_json_headers_but_no_auth() -> None:
    session = build_session(_SETTINGS)

    assert session.headers["Content-Type"] == "application/json"
    assert "Authorization" not in session.headers


def test_query_paginates_via_next_records_url() -> None:
    page1 = _FakeResponse(
        200,
        {
            "records": [{"Id": "001A", "attributes": {"type": "Account"}}],
            "done": False,
            "nextRecordsUrl": "/services/data/v63.0/query/01g000000000000AAA-2000",
        },
    )
    page2 = _FakeResponse(
        200,
        {"records": [{"Id": "001B", "attributes": {"type": "Account"}}], "done": True},
    )
    client, session = _client([page1, page2])

    records = client.query("SELECT Id FROM Account")

    assert [r["Id"] for r in records] == ["001A", "001B"]
    assert session.calls[0][1] == (
        "https://acme.my.salesforce.com/services/data/v63.0/query/?q=SELECT%20Id%20FROM%20Account"
    )
    # The second call must use nextRecordsUrl as-is, not double-prefixed.
    assert session.calls[1][1] == (
        "https://acme.my.salesforce.com/services/data/v63.0/query/01g000000000000AAA-2000"
    )


def test_url_does_not_double_prefix_absolute_paths() -> None:
    client, _ = _client([_FakeResponse(200, {"records": [], "done": True})])

    url = client._url("/services/data/v63.0/query/01g-2000")

    assert url == "https://acme.my.salesforce.com/services/data/v63.0/query/01g-2000"
