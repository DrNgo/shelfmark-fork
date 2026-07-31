"""Read-only Audiobookshelf API client.

Only GET endpoints are used: Shelfmark writes audiobook files to disk and lets
Audiobookshelf scan them, so nothing here should ever mutate an ABS library.
"""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

_HTTP_STATUS_UNAUTHORIZED = HTTPStatus.UNAUTHORIZED
_HTTP_STATUS_FORBIDDEN = HTTPStatus.FORBIDDEN
_DEFAULT_PAGE_SIZE = 500
_MAX_PAGES = 1000

ABS_CLIENT_ERRORS = (
    requests.exceptions.RequestException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

BOOK_MEDIA_TYPE = "book"


@dataclass(frozen=True)
class AudiobookshelfLibrary:
    """A library as Audiobookshelf reports it."""

    id: str
    name: str
    media_type: str

    @property
    def is_book_library(self) -> bool:
        """True for audiobook libraries; podcast libraries are not routable."""
        return self.media_type == BOOK_MEDIA_TYPE


def _normalize_json_object(payload: object, *, context: str) -> dict[str, Any]:
    """Return a JSON object payload with string keys or raise on unexpected shapes."""
    if not isinstance(payload, Mapping):
        msg = f"Unexpected {context} response payload"
        raise TypeError(msg)

    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            msg = f"Unexpected {context} response payload"
            raise TypeError(msg)
        normalized[key] = value

    return normalized


def _normalize_json_object_list(payload: object, *, context: str) -> list[dict[str, Any]]:
    """Return a list of JSON objects or raise on unexpected item shapes."""
    if not isinstance(payload, list):
        msg = f"Unexpected {context} response payload"
        raise TypeError(msg)

    return [_normalize_json_object(item, context=context) for item in payload]


class AudiobookshelfClient:
    """Client for the subset of the Audiobookshelf API that Shelfmark reads."""

    def __init__(self, url: str, api_token: str, timeout: int = 30) -> None:
        """Initialize the client with a base URL, API token, and request timeout."""
        self.base_url = normalize_http_url(url)
        self.api_token = api_token
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> object:
        """Make a request to Audiobookshelf and return the parsed JSON response."""
        url = self.base_url + endpoint
        logger.debug("Audiobookshelf API: %s %s", method, url)

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                timeout=self.timeout,
                verify=get_ssl_verify(url),
            )

            if not response.ok:
                with suppress(Exception):
                    logger.error("Audiobookshelf API error response: %s", response.text[:500])

            response.raise_for_status()
            return response.json()

        except requests.exceptions.JSONDecodeError as e:
            logger.exception("Invalid JSON response from Audiobookshelf")
            msg = f"Invalid JSON response: {e}"
            raise ValueError(msg) from e
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            reason = e.response.reason if e.response is not None else "unknown"
            logger.exception("Audiobookshelf API HTTP error: %s %s", status_code, reason)
            raise
        except requests.exceptions.RequestException:
            logger.exception("Audiobookshelf API request failed")
            raise

    def test_connection(self) -> tuple[bool, str]:
        """Test connectivity and token validity. Returns (success, message)."""
        logger.info("Testing Audiobookshelf connection to: %s", self.base_url)
        try:
            libraries = self.get_libraries()
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to Audiobookshelf. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (_HTTP_STATUS_UNAUTHORIZED, _HTTP_STATUS_FORBIDDEN):
                return False, "Invalid API token"
            return False, f"HTTP error {status if status is not None else 'unknown'}"
        except ABS_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e!s}"
        else:
            book_count = sum(1 for lib in libraries if lib.is_book_library)
            logger.info(
                "Audiobookshelf connection successful: %d libraries (%d book)",
                len(libraries),
                book_count,
            )
            return True, f"Connected — found {len(libraries)} libraries ({book_count} audiobook)"

    def get_libraries(self) -> list[AudiobookshelfLibrary]:
        """List every Audiobookshelf library.

        Raises on any transport or payload failure: an empty list strictly means
        "this server has no libraries", never "the request failed". Callers that
        cache or route on this need that distinction.
        """
        payload = _normalize_json_object(
            self._request("GET", "/api/libraries"),
            context="Audiobookshelf libraries",
        )
        raw_libraries = _normalize_json_object_list(
            payload.get("libraries", []),
            context="Audiobookshelf libraries",
        )

        libraries: list[AudiobookshelfLibrary] = []
        for raw in raw_libraries:
            library_id = str(raw.get("id") or "").strip()
            if not library_id:
                continue
            libraries.append(
                AudiobookshelfLibrary(
                    id=library_id,
                    name=str(raw.get("name") or library_id),
                    media_type=str(raw.get("mediaType") or "").strip().lower(),
                )
            )

        return libraries

    def get_book_libraries(self) -> list[AudiobookshelfLibrary]:
        """List audiobook libraries only, dropping podcast libraries."""
        return [lib for lib in self.get_libraries() if lib.is_book_library]

    def get_library_items(
        self,
        library_id: str,
        *,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Fetch every expanded item in a library, following pagination."""
        items: list[dict[str, Any]] = []
        page = 0

        while page < _MAX_PAGES:
            payload = _normalize_json_object(
                self._request(
                    "GET",
                    f"/api/libraries/{library_id}/items",
                    params={"expanded": 1, "limit": page_size, "page": page},
                ),
                context="Audiobookshelf library items",
            )
            results = _normalize_json_object_list(
                payload.get("results", []),
                context="Audiobookshelf library items",
            )
            # A page that comes back empty ends the walk even when `total`
            # claims more, so a stale or wrong total can't spin forever.
            if not results:
                break

            items.extend(results)

            total = payload.get("total")
            if isinstance(total, int) and len(items) >= total:
                break

            page += 1

        return items
