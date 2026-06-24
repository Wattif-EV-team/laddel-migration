"""Thin wrapper around the Ampeco Public API.

The wrapper injects the static bearer ``API Token``, combines retry and rate
limiting into a single adapter, and validates each response's status code. It
returns the ``data`` envelope Ampeco wraps successful responses in. Semantic
failures (4xx) surface immediately as :class:`AmpecoError`; only transient
statuses (429/5xx) and connection errors are retried, by the adapter.
"""

from __future__ import annotations

from typing import Any

import requests
from requests_ratelimiter import LimiterAdapter
from urllib3.util.retry import Retry

from ..config import AmpecoSettings
from ..logging import get_logger

logger = get_logger(__name__)

# Transient statuses worth retrying. 500 is intentionally excluded: a write that
# returns 500 may have partially applied server-side, and blindly re-POSTing
# could create a duplicate. 429/502/503/504 indicate the request was throttled,
# rejected by a gateway, or never processed, so retrying is safe.
_RETRY_STATUSES = (429, 502, 503, 504)
_RETRY_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})

_DEFAULT_TIMEOUT = 30.0


class AmpecoError(RuntimeError):
    """Raised when the Ampeco API returns an unexpected status code."""


def build_session(settings: AmpecoSettings) -> requests.Session:
    """Create a :class:`requests.Session` with auth, retry and rate limiting.

    A single :class:`~requests_ratelimiter.LimiterAdapter` carries both the
    urllib3 retry policy and the per-minute rate limit, mounted for http/https.
    """
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=_RETRY_STATUSES,
        allowed_methods=_RETRY_METHODS,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = LimiterAdapter(per_minute=settings.requests_per_minute, max_retries=retry)

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {settings.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class AmpecoClient:
    """Minimal create/update/get client for the Ampeco Public API."""

    def __init__(
        self,
        settings: AmpecoSettings,
        *,
        session: requests.Session | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = settings.base_url
        self._timeout = timeout
        self._session = session if session is not None else build_session(settings)

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def get(self, path: str, *, expected_status: int = 200) -> Any:
        """GET ``path`` and return the response ``data`` envelope."""
        response = self._session.get(self._url(path), timeout=self._timeout)
        return self._handle(response, expected_status, "GET", path)

    def create(self, path: str, payload: dict[str, Any], *, expected_status: int = 201) -> Any:
        """POST ``payload`` to ``path`` and return the created resource data."""
        logger.debug("POST %s payload=%s", path, payload)
        response = self._session.post(self._url(path), json=payload, timeout=self._timeout)
        return self._handle(response, expected_status, "POST", path)

    def update(
        self,
        path: str,
        resource_id: object,
        payload: dict[str, Any],
        *,
        expected_status: int = 200,
    ) -> Any:
        """PATCH ``path/{resource_id}`` with ``payload`` and return the data."""
        url = f"{self._url(path)}/{resource_id}"
        logger.debug("PATCH %s/%s payload=%s", path, resource_id, payload)
        response = self._session.patch(url, json=payload, timeout=self._timeout)
        return self._handle(response, expected_status, "PATCH", f"{path}/{resource_id}")

    @staticmethod
    def _handle(response: Any, expected_status: int, method: str, path: str) -> Any:
        if response.status_code != expected_status:
            raise AmpecoError(
                f"{method} {path} failed: expected {expected_status}, "
                f"got {response.status_code}: {_error_message(response)}"
            )
        logger.debug("%s %s -> %s", method, path, response.status_code)
        if not getattr(response, "content", b""):
            return None
        body = response.json()
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


def _error_message(response: Any) -> str:
    """Best-effort extraction of an API error message for logging."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - error bodies are not guaranteed to be JSON
        return getattr(response, "text", "") or "<no body>"
    if isinstance(body, dict):
        return str(body.get("message") or body)
    return str(body)
