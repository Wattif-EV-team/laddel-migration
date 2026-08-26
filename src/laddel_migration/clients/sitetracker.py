"""Thin wrapper around the SiteTracker (Salesforce) sObject REST API.

SiteTracker runs on Salesforce, so this is the standard OAuth2 *password grant*
against a Connected App. The client fetches a bearer token lazily (on the first
request, and again on a ``401``), then talks to the sObject REST API under the
org's ``instance_url``.

The public surface mirrors :class:`~laddel_migration.clients.ampeco.AmpecoClient`
(``get`` / ``create`` / ``update``), plus ``delete`` for one-off cleanup scripts,
so the generic create-or-update loop can drive either target system unchanged.
Notable Salesforce specifics handled here:

* create (``POST /sobjects/{Object}``) returns ``201`` with ``{"id", "success"}``
  — returned as-is so the loop can read ``data["id"]``;
* update (``PATCH /sobjects/{Object}/{id}``) returns ``204 No Content``;
* delete (``DELETE /sobjects/{Object}/{id}``) also returns ``204 No Content``;
* expired tokens surface as ``401`` and trigger a single re-auth + retry.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests
from requests_ratelimiter import LimiterAdapter
from urllib3.util.retry import Retry

from ..config import SiteTrackerSettings
from ..logging import get_logger

logger = get_logger(__name__)

# Transient statuses worth retrying. 500 is intentionally excluded: a write that
# returns 500 may have partially applied server-side, and blindly re-POSTing
# could create a duplicate. 401 is handled separately (token refresh + retry).
_RETRY_STATUSES = (429, 502, 503, 504)
_RETRY_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})

_DEFAULT_TIMEOUT = 30.0


class SiteTrackerError(RuntimeError):
    """Raised when the SiteTracker API returns an unexpected status code."""


def fetch_token(settings: SiteTrackerSettings, *, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Fetch an OAuth2 bearer token via the Salesforce password grant."""
    response = requests.post(
        settings.token_url,
        data={
            "grant_type": "password",
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "username": settings.username,
            "password": settings.password,
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        raise SiteTrackerError(
            f"OAuth token request failed: expected 200, got {response.status_code}: "
            f"{_error_message(response)}"
        )
    token = response.json().get("access_token")
    if not token:
        raise SiteTrackerError("OAuth token response did not contain an access_token.")
    return token


def build_session(settings: SiteTrackerSettings) -> requests.Session:
    """Create a :class:`requests.Session` with retry and rate limiting.

    The bearer token is *not* set here — it is fetched lazily and injected by the
    client on first use (and refreshed on ``401``).
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
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class SiteTrackerClient:
    """Minimal create/update/get client for the SiteTracker sObject REST API."""

    def __init__(
        self,
        settings: SiteTrackerSettings,
        *,
        session: requests.Session | None = None,
        token: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings
        self._instance_url = settings.instance_url
        self._api_version = settings.api_version
        self._timeout = timeout
        self._session = session if session is not None else build_session(settings)
        self._token = token
        if token is not None:
            self._session.headers["Authorization"] = f"Bearer {token}"

    def _url(self, path: str) -> str:
        # Salesforce's SOQL pagination cursor (``nextRecordsUrl``) is already an
        # absolute path under ``/services/data/{version}/...`` — passing it
        # through the normal prefixing would double it up, so use it as-is.
        if path.startswith("/services/data/"):
            return f"{self._instance_url}{path}"
        return f"{self._instance_url}/services/data/{self._api_version}{path}"

    def authenticate(self) -> str:
        """Fetch a fresh bearer token and inject it into the session headers."""
        self._token = fetch_token(self._settings, timeout=self._timeout)
        self._session.headers["Authorization"] = f"Bearer {self._token}"
        return self._token

    def _request(self, verb: str, url: str, **kwargs: Any) -> Any:
        """Send a request, fetching/refreshing the token on demand.

        A missing token is fetched before the first call; a ``401`` triggers a
        single re-auth and one retry (the org may have expired the token).
        """
        if self._token is None:
            self.authenticate()
        send = getattr(self._session, verb)
        response = send(url, timeout=self._timeout, **kwargs)
        if response.status_code == 401:
            logger.debug("SiteTracker %s %s -> 401, refreshing token", verb.upper(), url)
            self.authenticate()
            response = send(url, timeout=self._timeout, **kwargs)
        return response

    def get(self, path: str, *, expected_status: int = 200) -> Any:
        """GET ``path`` and return the parsed JSON body."""
        response = self._request("get", self._url(path))
        return self._handle(response, expected_status, "GET", path)

    def query(self, soql: str) -> list[dict[str, Any]]:
        """Run a SOQL query and return every record, following pagination.

        Follows ``nextRecordsUrl`` while ``done`` is ``False``, concatenating
        ``records`` across all pages (the default query path caps each page at
        2,000 rows and the object at 10,000 total). Records are returned as-is
        (each still carries Salesforce's ``attributes`` envelope key) — callers
        that render a table should drop it themselves.
        """
        path = f"/query/?q={quote(soql)}"
        records: list[dict[str, Any]] = []
        while True:
            body = self.get(path)
            records.extend(body.get("records", []))
            if body.get("done", True):
                return records
            path = body["nextRecordsUrl"]

    def create(self, path: str, payload: dict[str, Any], *, expected_status: int = 201) -> Any:
        """POST ``payload`` to ``path`` and return the created record envelope.

        Salesforce returns ``{"id": ..., "success": true, "errors": []}``; the
        generic loop reads ``["id"]`` from it.
        """
        logger.debug("POST %s payload=%s", path, payload)
        response = self._request("post", self._url(path), json=payload)
        return self._handle(response, expected_status, "POST", path)

    def update(
        self,
        path: str,
        resource_id: object,
        payload: dict[str, Any],
        *,
        expected_status: int = 204,
    ) -> Any:
        """PATCH ``path/{resource_id}`` with ``payload`` (returns ``None`` on 204)."""
        url = f"{self._url(path)}/{resource_id}"
        logger.debug("PATCH %s/%s payload=%s", path, resource_id, payload)
        response = self._request("patch", url, json=payload)
        return self._handle(response, expected_status, "PATCH", f"{path}/{resource_id}")

    def delete(self, path: str, resource_id: object, *, expected_status: int = 204) -> Any:
        """DELETE ``path/{resource_id}`` (returns ``None`` on 204)."""
        url = f"{self._url(path)}/{resource_id}"
        logger.debug("DELETE %s/%s", path, resource_id)
        response = self._request("delete", url)
        return self._handle(response, expected_status, "DELETE", f"{path}/{resource_id}")

    @staticmethod
    def _handle(response: Any, expected_status: int, method: str, path: str) -> Any:
        if response.status_code != expected_status:
            raise SiteTrackerError(
                f"{method} {path} failed: expected {expected_status}, "
                f"got {response.status_code}: {_error_message(response)}"
            )
        logger.debug("%s %s -> %s", method, path, response.status_code)
        if not getattr(response, "content", b""):
            return None
        return response.json()


def _error_message(response: Any) -> str:
    """Best-effort extraction of an API error message for logging.

    Salesforce error bodies are typically a list of ``{"message", "errorCode"}``.
    """
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - error bodies are not guaranteed to be JSON
        return getattr(response, "text", "") or "<no body>"
    if isinstance(body, list) and body:
        first = body[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)
    if isinstance(body, dict):
        return str(body.get("error_description") or body.get("message") or body)
    return str(body)
