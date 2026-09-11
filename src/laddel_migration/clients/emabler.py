"""Thin, **strictly read-only** wrapper around the eMabler Entity Management API.

eMabler is the CSMS the ``laddel`` fleet is migrating *away from*, and it is a
LIVE PRODUCTION system running real chargers. We only ever GET from it, to
recover facts that exist nowhere in the ``laddel`` database — the per-charger
OCPP protocol version above all.

⚠️  **Never add a create/update/delete method to this module.** The eMabler API
exposes plenty of mutating endpoints (remote start/stop, reboot, firmware
update, tariff and driver writes); none of them are ours to call. This is not
just a convention: :class:`_ReadOnlySession` refuses any non-GET request at
runtime, so a mistake fails loudly here instead of reaching production
hardware. If a genuine write need ever appears, it must be an explicit,
separately-reviewed decision — not a quiet method added to this client.

The wrapper otherwise mirrors :mod:`laddel_migration.clients.ampeco`: it
injects the static ``x-api-key`` credential, combines retry and rate limiting
into a single adapter, and validates each response's status code. Two
differences from the Ampeco client:

* eMabler returns the payload directly, with no ``data`` envelope to unwrap.
* List endpoints are page-based (``page`` / ``limit``, max 100 per page) and
  advertise ``hasNextPage``, so this client owns the pagination loop.
"""

from __future__ import annotations

from typing import Any

import requests
from requests_ratelimiter import LimiterAdapter
from urllib3.util.retry import Retry

from ..config import EmablerSettings
from ..logging import get_logger

logger = get_logger(__name__)

# Transient statuses worth retrying. Reads are idempotent, but we keep the same
# conservative list as the Ampeco client so the two behave identically.
_RETRY_STATUSES = (429, 502, 503, 504)

# The only HTTP methods this client may ever issue. eMabler is a live production
# CSMS and this integration is read-only by design; see the module docstring.
_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
_RETRY_METHODS = frozenset({"GET"})

_DEFAULT_TIMEOUT = 30.0

# The API caps `limit` at 100 (see docs/emabler-entity.json, getChargers).
MAX_PAGE_SIZE = 100

# Safety valve for the pagination loop: a server that always reports
# ``hasNextPage: true`` would otherwise spin forever. 1000 pages x 100 rows is
# far beyond any plausible fleet size.
_MAX_PAGES = 1000


class EmablerError(RuntimeError):
    """Raised when the eMabler API returns an unexpected status or shape."""


class EmablerWriteBlocked(EmablerError):
    """Raised when something tries to send a mutating request to eMabler.

    Getting this exception means a bug, not a configuration problem: nothing in
    this project is allowed to modify the outgoing CSMS. Fix the caller rather
    than relaxing the guard.
    """


class _ReadOnlySession(requests.Session):
    """A :class:`requests.Session` that physically cannot mutate anything.

    Every convenience method (``post``, ``put``, ``patch``, ``delete``, ...)
    funnels through :meth:`requests.Session.request`, so overriding this single
    method is enough to block them all — including calls made directly against
    the session object rather than through :class:`EmablerClient`.
    """

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:
        if method.upper() not in _READ_ONLY_METHODS:
            raise EmablerWriteBlocked(
                f"Refusing to send {method.upper()} {url}: the eMabler integration is "
                "read-only. eMabler is a live production CSMS and this project must "
                "never modify it — only GET."
            )
        return super().request(method, url, *args, **kwargs)


def build_session(settings: EmablerSettings) -> requests.Session:
    """Create a read-only :class:`requests.Session` with auth, retry and rate limiting."""
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=_RETRY_STATUSES,
        allowed_methods=_RETRY_METHODS,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = LimiterAdapter(per_minute=settings.requests_per_minute, max_retries=retry)

    session = _ReadOnlySession()
    session.headers.update(
        {
            "x-api-key": settings.api_key,
            "Accept": "application/json",
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class EmablerClient:
    """Read-only client for the eMabler Entity Management API.

    Exposes GET-shaped methods only. Do not add ``create``/``update``/``delete``
    counterparts to the Ampeco client's — see the module docstring.
    """

    def __init__(
        self,
        settings: EmablerSettings,
        *,
        session: requests.Session | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = settings.base_url
        self._timeout = timeout
        self._session = session if session is not None else build_session(settings)

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expected_status: int = 200,
    ) -> Any:
        """GET ``path`` and return the parsed JSON body (no envelope unwrapping)."""
        logger.debug("GET %s params=%s", path, params)
        response = self._session.get(self._url(path), params=params, timeout=self._timeout)
        return self._handle(response, expected_status, "GET", path)

    def get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = MAX_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Fetch every page of a paginated list endpoint and return all items.

        Follows ``hasNextPage`` rather than computing the page count from
        ``totalRecords``, so a fleet changing size mid-walk cannot make us stop
        early. Also stops on an empty page, which protects against a server that
        keeps claiming another page exists.
        """
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}, got {page_size}")

        items: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self.get(path, params={**(params or {}), "page": page, "limit": page_size})
            if not isinstance(body, dict):
                raise EmablerError(
                    f"GET {path} page {page}: expected a JSON object, got {type(body).__name__}"
                )

            batch = body.get("items") or []
            items.extend(batch)
            total = body.get("totalRecords")
            logger.info(
                "eMabler %s page %d: %d item(s), %d fetched so far%s",
                path,
                page,
                len(batch),
                len(items),
                f" of {total}" if isinstance(total, int) else "",
            )

            if not batch or not body.get("hasNextPage"):
                return items

            page += 1
            if page > _MAX_PAGES:
                raise EmablerError(
                    f"GET {path} exceeded the {_MAX_PAGES}-page safety limit; "
                    "the API kept reporting hasNextPage."
                )

    @staticmethod
    def _handle(response: Any, expected_status: int, method: str, path: str) -> Any:
        if response.status_code != expected_status:
            raise EmablerError(
                f"{method} {path} failed: expected {expected_status}, "
                f"got {response.status_code}: {_error_message(response)}"
            )
        logger.debug("%s %s -> %s", method, path, response.status_code)
        if not getattr(response, "content", b""):
            return None
        return response.json()


def _error_message(response: Any) -> str:
    """Best-effort extraction of an API error message for logging.

    eMabler returns either an ``error`` object (``errorCode`` / ``message``) or
    an RFC 7807 ``problemDetails`` (``title`` / ``detail``), depending on the
    failure; try both before falling back to the raw body.
    """
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - error bodies are not guaranteed to be JSON
        return getattr(response, "text", "") or "<no body>"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("detail") or body.get("title") or body)
    return str(body)
