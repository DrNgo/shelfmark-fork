"""The audiobook destination map: one Audiobookshelf library, one local path.

Audiobookshelf reports library paths as *its* container sees them, and Shelfmark
is the process that actually writes the files — so a library's ABS-reported path
is not usable here. The map pairs an ABS library id (which names the destination
in the UI) with a path this container has confirmed it can write to.

Resolution never raises. An explicit key that no longer maps to anything falls
back down the normal destination chain with a warning: a stale key should cost
you a routing decision, never a download.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shelfmark.core.logger import setup_logger

# Fork note: reusing upstream's placeholder expansion rather than copying it keeps
# `{User}` behaving identically in mapped paths and in DESTINATION_AUDIOBOOK.
from shelfmark.core.utils import _expand_user_destination_placeholder

logger = setup_logger(__name__)

DESTINATION_MAP_KEY = "AUDIOBOOK_DESTINATIONS"


@dataclass(frozen=True)
class AudiobookDestination:
    """One routable destination: an ABS library paired with a local path."""

    key: str
    name: str
    path: str


def _row_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        return ""
    return value.strip()


def get_destination_map() -> list[AudiobookDestination]:
    """Read the configured destination map, dropping rows that cannot route.

    A row needs both a library key and a local path to be usable, so partially
    filled rows (the natural state of a table the admin is still editing) are
    skipped rather than surfaced as broken options.
    """
    from shelfmark.core.config import config

    raw = config.get(DESTINATION_MAP_KEY, [])
    if not isinstance(raw, list):
        logger.warning("%s is not a list; ignoring it", DESTINATION_MAP_KEY)
        return []

    mapped: list[AudiobookDestination] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue

        key = _row_text(row, "key")
        path = _row_text(row, "path")
        if not key or not path or key in seen:
            continue

        seen.add(key)
        mapped.append(AudiobookDestination(key=key, name=_row_text(row, "name") or key, path=path))

    return mapped


def get_destination(destination_key: str) -> AudiobookDestination | None:
    """Look up a single destination by key, or None when it is not mapped."""
    return next((d for d in get_destination_map() if d.key == destination_key), None)


def resolve_destination_path(
    destination_key: str | None,
    *,
    user_id: int | None = None,
    username: str | None = None,
) -> Path | None:
    """Resolve an explicit destination key to a path, or None to fall back.

    Returning None (rather than raising) is what makes a dangling key harmless:
    the caller continues down the per-user override → global default chain.
    """
    key = (destination_key or "").strip()
    if not key:
        return None

    destination = get_destination(key)
    if destination is None:
        logger.warning(
            "Destination key %r is not in the audiobook destination map; "
            "falling back to the default audiobook destination",
            key,
        )
        return None

    return Path(
        _expand_user_destination_placeholder(
            destination.path,
            user_id=user_id,
            username=username,
        )
    )


def authorize_destination_key(payload: dict[str, Any], *, is_admin: bool) -> dict[str, Any]:
    """Drop a caller-supplied destination key unless the caller is an admin.

    Routing is an admin decision in v1. Without this, any signed-in user could
    POST a library id to the download endpoint and write into a library they
    were never meant to reach.
    """
    if is_admin:
        return payload

    extra = payload.get("extra")
    extra_has_key = isinstance(extra, dict) and "destination_key" in extra
    if "destination_key" not in payload and not extra_has_key:
        return payload

    logger.warning("Ignoring destination_key from a non-admin download request")
    authorized = {k: v for k, v in payload.items() if k != "destination_key"}
    if extra_has_key and isinstance(extra, dict):
        authorized["extra"] = {k: v for k, v in extra.items() if k != "destination_key"}
    return authorized


def list_destination_options() -> list[dict[str, str]]:
    """List destinations for the approve dialog: keys and names, never paths."""
    return [{"key": d.key, "name": d.name} for d in get_destination_map()]
