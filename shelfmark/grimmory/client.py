"""Grimmory (formerly Booklore) API client: connection primitives shared by uploads
and the library index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

BOOKLORE_DESTINATION_LIBRARY = "library"
BOOKLORE_DESTINATION_BOOKDROP = "bookdrop"
BOOKLORE_DISPLAY_NAME = "Grimmory"

_BOOKS_PAGE_ENDPOINT = "/api/v1/books/page"


class BookloreError(Exception):
    """Raised when Booklore integration fails."""


@dataclass(frozen=True)
class BookloreConfig:
    """Configuration required to upload files into Booklore."""

    base_url: str
    username: str
    password: str
    library_id: int
    path_id: int
    verify_tls: bool = True
    upload_to_bookdrop: bool = False
    refresh_after_upload: bool = False


def parse_int(value: object, label: str) -> int:
    if value is None or value == "":
        msg = f"{label} is required"
        raise BookloreError(msg)
    if not isinstance(value, (int, float, str)):
        msg = f"{label} must be a number"
        raise BookloreError(msg)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        msg = f"{label} must be a number"
        raise BookloreError(msg) from exc


def parse_destination(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == BOOKLORE_DESTINATION_BOOKDROP:
        return BOOKLORE_DESTINATION_BOOKDROP
    return BOOKLORE_DESTINATION_LIBRARY


def booklore_login(booklore_config: BookloreConfig) -> str:
    """Authenticate with Booklore and return an API token."""
    url = f"{booklore_config.base_url}/api/v1/auth/login"
    payload = {
        "username": booklore_config.username,
        "password": booklore_config.password,
    }

    try:
        response = requests.post(url, json=payload, timeout=30, verify=booklore_config.verify_tls)
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {BOOKLORE_DISPLAY_NAME}"
        raise BookloreError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} connection timed out"
        raise BookloreError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} login failed: {exc}"
        raise BookloreError(msg) from exc

    if response.status_code in {401, 403}:
        msg = f"{BOOKLORE_DISPLAY_NAME} authentication failed"
        raise BookloreError(msg)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} login failed ({response.status_code})"
        raise BookloreError(msg) from exc

    try:
        data = response.json()
    except ValueError as exc:
        msg = f"Invalid {BOOKLORE_DISPLAY_NAME} login response"
        raise BookloreError(msg) from exc

    token = data.get("accessToken")
    if not token:
        msg = f"{BOOKLORE_DISPLAY_NAME} did not return an access token"
        raise BookloreError(msg)

    return token


def booklore_list_libraries(booklore_config: BookloreConfig, token: str) -> list[dict[str, Any]]:
    """Fetch the available Booklore libraries for the current user."""
    url = f"{booklore_config.base_url}/api/v1/libraries"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(url, headers=headers, timeout=30, verify=booklore_config.verify_tls)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        msg = f"Failed to fetch {BOOKLORE_DISPLAY_NAME} libraries: {exc}"
        raise BookloreError(msg) from exc

    try:
        return response.json()
    except ValueError as exc:
        msg = f"Invalid {BOOKLORE_DISPLAY_NAME} libraries response"
        raise BookloreError(msg) from exc


def list_books(
    booklore_config: BookloreConfig,
    token: str,
    *,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch one page of books, returning the rows and the total page count.

    Uses the legacy page/size mode — supplying no sort, facet or query keeps the
    endpoint on plain offset pagination, which needs no cursor to resume.

    What comes back is scoped to the authenticated user: an admin sees every
    library, anyone else only their assigned ones. The account in
    BOOKLORE_USERNAME therefore decides how much of the library gets indexed.
    """
    url = f"{booklore_config.base_url}{_BOOKS_PAGE_ENDPOINT}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(
            url,
            headers=headers,
            params={"page": page, "size": size},
            timeout=60,
            verify=booklore_config.verify_tls,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {BOOKLORE_DISPLAY_NAME}"
        raise BookloreError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} book listing timed out"
        raise BookloreError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"Failed to fetch {BOOKLORE_DISPLAY_NAME} books: {exc}"
        raise BookloreError(msg) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        msg = f"Invalid {BOOKLORE_DISPLAY_NAME} book listing response"
        raise BookloreError(msg) from exc

    if not isinstance(payload, dict):
        msg = f"Unexpected {BOOKLORE_DISPLAY_NAME} book listing payload"
        raise BookloreError(msg)

    # A missing or non-list `content` is a malformed response, not an empty
    # library: treating it as empty would let replace_items() wipe out every
    # cached Grimmory row on a transient API hiccup, when a sync failure is
    # supposed to record why and leave the previous index in place. An
    # explicit `"content": []` is a legitimately empty library and must still
    # succeed.
    content = payload.get("content")
    if not isinstance(content, list):
        msg = f"Unexpected {BOOKLORE_DISPLAY_NAME} book listing payload: missing 'content' list"
        raise BookloreError(msg)

    books = [row for row in content if isinstance(row, dict)]

    raw_total = payload.get("totalPages")
    total_pages = raw_total if isinstance(raw_total, int) and raw_total > 0 else 1

    return books, total_pages
