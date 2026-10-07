"""Grimmory upload destinations: the `grimmory:<libraryId>:<pathId>` key (fork-only).

An admin picks a (library, path) pair for an ebook; the pair travels as an
opaque `destination_key`, the same field audiobook routing uses. The key is
only ever *trusted* after it has been checked against a fresh library listing
(see `shelfmark.download.outputs.booklore`): the options built here are for
display only.
"""

from __future__ import annotations

import re
from typing import Any

GRIMMORY_KEY_PREFIX = "grimmory:"

# ASCII digits only: `\d` would also accept "³" and other Unicode digits.
_KEY_PATTERN = re.compile(r"grimmory:([0-9]+):([0-9]+)")


def grimmory_destination_key(library_id: int, path_id: int) -> str:
    """Build the destination key for one Grimmory library path."""
    return f"{GRIMMORY_KEY_PREFIX}{library_id}:{path_id}"


def parse_grimmory_destination_key(key: object) -> tuple[int, int] | None:
    """Return `(library_id, path_id)` for a well-formed key, else None.

    Strict on purpose: a key that is almost right (padded, upper-cased, an
    audiobook key) is a key that names nothing, and the upload must fail
    rather than guess.
    """
    if not isinstance(key, str):
        return None
    match = _KEY_PATTERN.fullmatch(key)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _row_id(value: object) -> int | None:
    """Read a Grimmory id that may arrive as an int or a digit string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _library_paths(library: object) -> list[dict[str, Any]]:
    if not isinstance(library, dict):
        return []
    paths = library.get("paths")
    if not isinstance(paths, list):
        return []
    return [
        path for path in paths if isinstance(path, dict) and _row_id(path.get("id")) is not None
    ]


def library_path_exists(libraries: object, library_id: int, path_id: int) -> bool:
    """Whether `GET /api/v1/libraries` output contains this exact library path."""
    if not isinstance(libraries, list):
        return False
    for library in libraries:
        if not isinstance(library, dict) or _row_id(library.get("id")) != library_id:
            continue
        if any(_row_id(path.get("id")) == path_id for path in _library_paths(library)):
            return True
    return False


def build_destination_options(libraries: object) -> list[dict[str, str]]:
    """Build picker options, one per (library, path), ordered by library then path.

    A library with a single path is named by the library alone; with more, each
    option is "<library> — <path>" so the admin can tell them apart.
    """
    if not isinstance(libraries, list):
        return []

    rows: list[tuple[str, str, dict[str, str]]] = []
    for library in libraries:
        if not isinstance(library, dict):
            continue
        library_id = _row_id(library.get("id"))
        paths = _library_paths(library)
        if library_id is None or not paths:
            continue

        library_name = str(library.get("name") or f"Library {library_id}")
        for path in paths:
            path_id = _row_id(path.get("id"))
            if path_id is None:
                continue
            path_name = str(path.get("path") or f"Path {path_id}")
            name = library_name if len(paths) == 1 else f"{library_name} — {path_name}"
            rows.append(
                (
                    library_name.casefold(),
                    path_name.casefold(),
                    {"key": grimmory_destination_key(library_id, path_id), "name": name},
                )
            )

    rows.sort(key=lambda row: (row[0], row[1]))
    return [option for _, _, option in rows]
