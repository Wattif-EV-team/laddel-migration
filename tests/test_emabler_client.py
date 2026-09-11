"""Tests for the eMabler API client: status handling and the pagination loop."""

from __future__ import annotations

from typing import Any

import pytest

from laddel_migration.clients.emabler import (
    MAX_PAGE_SIZE,
    EmablerClient,
    EmablerError,
    EmablerWriteBlocked,
    build_session,
)
from laddel_migration.config import EmablerSettings

_SETTINGS = EmablerSettings(
    base_url="https://api.example.com/api/v2/cpo",
    api_key="secret",
    requests_per_minute=1000,
)

_CHARGERS = "/v2/chargers"


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
    """Returns each queued response in turn, recording the params it was called with."""

    def __init__(self, *responses: _FakeResponse) -> None:
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, params))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _client(*responses: _FakeResponse) -> tuple[EmablerClient, _FakeSession]:
    session = _FakeSession(*responses)
    return EmablerClient(_SETTINGS, session=session), session  # type: ignore[arg-type]


def _page(*, items: list[dict[str, Any]], has_next: bool, total: int = 0) -> _FakeResponse:
    return _FakeResponse(
        200,
        {
            "page": 1,
            "limit": MAX_PAGE_SIZE,
            "totalRecords": total or len(items),
            "hasNextPage": has_next,
            "hasPreviousPage": False,
            "items": items,
        },
    )


def test_get_returns_body_without_unwrapping_an_envelope() -> None:
    client, session = _client(_FakeResponse(200, {"items": [{"id": 1}], "hasNextPage": False}))

    result = client.get(_CHARGERS, params={"limit": 1})

    assert result == {"items": [{"id": 1}], "hasNextPage": False}
    assert session.calls == [("https://api.example.com/api/v2/cpo/v2/chargers", {"limit": 1})]


def test_get_raises_with_problem_details_detail_on_400() -> None:
    client, _ = _client(_FakeResponse(400, {"title": "Bad Request", "detail": "limit too large"}))

    with pytest.raises(EmablerError, match="limit too large"):
        client.get(_CHARGERS)


def test_get_raises_with_error_message_on_401() -> None:
    client, _ = _client(_FakeResponse(401, {"message": "User not authorized"}))

    with pytest.raises(EmablerError, match="User not authorized"):
        client.get(_CHARGERS)


def test_get_all_pages_follows_has_next_page_and_concatenates_items() -> None:
    client, session = _client(
        _page(items=[{"id": 1}, {"id": 2}], has_next=True, total=3),
        _page(items=[{"id": 3}], has_next=False, total=3),
    )

    items = client.get_all_pages(_CHARGERS, page_size=2)

    assert items == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [params for _, params in session.calls] == [
        {"page": 1, "limit": 2},
        {"page": 2, "limit": 2},
    ]


def test_get_all_pages_stops_on_empty_page_despite_has_next_page() -> None:
    """A server that always claims another page must not spin forever."""
    client, session = _client(_page(items=[], has_next=True))

    assert client.get_all_pages(_CHARGERS) == []
    assert len(session.calls) == 1


def test_get_all_pages_rejects_an_out_of_range_page_size() -> None:
    client, _ = _client(_page(items=[], has_next=False))

    with pytest.raises(ValueError, match="between 1 and 100"):
        client.get_all_pages(_CHARGERS, page_size=MAX_PAGE_SIZE + 1)


def test_get_all_pages_rejects_a_non_object_body() -> None:
    client, _ = _client(_FakeResponse(200, ["not", "a", "page"]))  # type: ignore[arg-type]

    with pytest.raises(EmablerError, match="expected a JSON object"):
        client.get_all_pages(_CHARGERS)


def test_build_session_sends_the_api_key_header_not_a_bearer_token() -> None:
    session = build_session(_SETTINGS)

    assert session.headers["x-api-key"] == "secret"
    assert "Authorization" not in session.headers


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_session_blocks_every_mutating_method(method: str) -> None:
    """eMabler is a live production CSMS; this integration must never write to it."""
    session = build_session(_SETTINGS)

    with pytest.raises(EmablerWriteBlocked, match="read-only"):
        session.request(method, "https://api.example.com/api/v2/cpo/v2/chargers")


def test_session_blocks_the_convenience_write_helpers() -> None:
    """`post`/`patch`/`delete` all funnel through `request`, so they are blocked too."""
    session = build_session(_SETTINGS)
    url = "https://api.example.com/api/v2/cpo/v2/chargers/1/reboot"

    for call in (session.post, session.put, session.patch, session.delete):
        with pytest.raises(EmablerWriteBlocked):
            call(url)


def test_client_exposes_no_write_methods() -> None:
    """A regression guard: adding create/update/delete here must fail the suite."""
    for forbidden in ("create", "update", "delete", "post", "patch", "put"):
        assert not hasattr(EmablerClient, forbidden), (
            f"EmablerClient.{forbidden} exists - the eMabler integration is read-only."
        )
