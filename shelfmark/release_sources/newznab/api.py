"""Newznab API client - connects to any Newznab-compatible indexer or aggregator."""

from __future__ import annotations

import re
from typing import Any

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_error, parse_torznab_xml

logger = setup_logger(__name__)

_HTTP_TOO_MANY_REQUESTS = 429

# ``apikey=…``, ``api_key=…`` or ``key=…`` in a URL or message (requests puts the
# full request URL, query string included, in its exception text).
_SECRET_PARAM_RE = re.compile(r"(?i)\b(apikey|api_key|key)=([^&\s'\"]+)")


def redact_secrets(text: object) -> str:
    """Return ``str(text)`` with API-key query values replaced by ``REDACTED``."""
    return _SECRET_PARAM_RE.sub(r"\1=REDACTED", str(text))


def _describe_request_error(e: requests.exceptions.RequestException) -> str:
    """Name a failed request without its text, which carries the URL and API key."""
    status = getattr(e.response, "status_code", None)
    return type(e).__name__ + (f" (HTTP {status})" if status is not None else "")


class NewznabSearchError(RuntimeError):
    """A Newznab search could not be completed - never the same as "no results".

    ``rate_limited`` marks an HTTP 429 or a Newznab request/download-limit error.
    """

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


class NewznabClient:
    """Client for any Newznab-compatible indexer API."""

    def __init__(self, url: str, api_key: str, timeout: int = 30) -> None:
        self.base_url = normalize_http_url(url)
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()

    def _api_url(self) -> str:
        """Return the Newznab API endpoint URL."""
        base = self.base_url.rstrip("/")
        # Many indexers expose the API at /api; others at the root with ?page=rss.
        # Prefer /api if the base URL doesn't already end with it.
        if not base.endswith("/api"):
            return base + "/api"
        return base

    def _get(
        self,
        params: dict[str, Any],
        *,
        accept_xml: bool = False,
    ) -> requests.Response:
        """Make a GET request to the Newznab API endpoint."""
        params = {k: v for k, v in params.items() if v is not None}
        if self.api_key:
            params["apikey"] = self.api_key

        url = self._api_url()
        logger.debug(
            "Newznab API: GET %s params=%s",
            url,
            {k: ("REDACTED" if k == "apikey" else v) for k, v in params.items()},
        )

        headers = {}
        if accept_xml:
            headers["Accept"] = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"

        response = self._session.get(
            url=url,
            params=params,
            headers=headers,
            timeout=self.timeout,
            verify=get_ssl_verify(url),
        )
        response.raise_for_status()
        return response

    def test_connection(self) -> tuple[bool, str]:
        """Test connection via the capabilities endpoint. Returns (success, message)."""
        logger.info("Testing Newznab connection to: %s", self.base_url)
        try:
            response = self._get({"t": "caps"})
            # Caps endpoint returns XML; a 200 is sufficient to confirm connectivity.
            # Try to extract the server title from the XML for a friendly message.
            text = response.text or ""
            title = "Newznab indexer"
            # Try <server title="..."/> attribute (NZBHydra2 style), then <title> element
            m = re.search(r'<server[^>]+title="([^"]+)"', text, re.IGNORECASE)
            if not m:
                m = re.search(r"<title>([^<]+)</title>", text, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
            logger.info("Newznab connection successful: %s", title)
        except requests.exceptions.ConnectionError:
            return False, "Could not connect. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if e.response is not None and e.response.status_code == 401:
                return False, "Invalid API key"
            return False, f"HTTP error {status}"
        except requests.exceptions.RequestException as e:
            return False, f"Connection failed: {redact_secrets(e)}"
        else:
            return True, f"Connected to {title}"

    def search(
        self,
        query: str,
        categories: list[int] | None = None,
        search_type: str = "search",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search the Newznab indexer and return parsed results.

        Args:
            query: Search string.
            categories: Optional list of Newznab category IDs (e.g. [7000, 3030]).
            search_type: Newznab search type ("search", "book", "audio").
            limit: Max results to return.
            offset: Result page offset.

        Returns:
            List of result dicts shaped like Prowlarr JSON search results so that
            the shared ``_prowlarr_result_to_release`` converter can process them.
            An empty list strictly means the indexer answered with no matches.

        Raises:
            NewznabSearchError: The request failed, or the indexer answered with a
                Newznab ``<error>`` instead of a result feed.
        """
        if not query:
            return []

        params: dict[str, Any] = {
            "t": search_type,
            "q": query,
            "limit": limit,
            "offset": offset,
        }
        if categories:
            params["cat"] = ",".join(str(c) for c in categories)

        try:
            response = self._get(params, accept_xml=True)
        except requests.exceptions.RequestException as e:
            status = getattr(e.response, "status_code", None)
            msg = f"Newznab search failed: {_describe_request_error(e)}"
            logger.warning("%s", msg)
            raise NewznabSearchError(msg, rate_limited=status == _HTTP_TOO_MANY_REQUESTS) from e

        text = response.text or ""
        results = parse_torznab_xml(text)
        logger.debug("Newznab search '%s': %d results", query, len(results))
        if not results:
            error = parse_torznab_error(text)
            if error is not None:
                msg = (
                    f"Newznab search failed: indexer error {error.code}: "
                    f"{error.description or 'no description'}"
                )
                raise NewznabSearchError(msg, rate_limited=error.rate_limited)
            preview = text[:300].strip() or "<empty>"
            logger.debug("Newznab empty response body: %s", preview)
        return results
