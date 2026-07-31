"""Audiobookshelf settings registration and config-backed client construction."""

from typing import TYPE_CHECKING, Any

from shelfmark.audiobookshelf.destinations import DESTINATION_MAP_KEY
from shelfmark.audiobookshelf.library_sync import (
    LIBRARY_INDEX_ENABLED_KEY,
    LIBRARY_INDEX_INTERVAL_KEY,
)
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    PasswordField,
    SettingsField,
    TableField,
    TextField,
    register_on_save,
    register_settings,
)
from shelfmark.core.utils import normalize_http_url

if TYPE_CHECKING:
    from shelfmark.audiobookshelf.client import AudiobookshelfClient, AudiobookshelfLibrary

logger = setup_logger(__name__)

_SETTINGS_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _resolve_setting_text(current_values: dict[str, Any], key: str, *, default: str = "") -> str:
    """Prefer current form values, then fall back to persisted config text."""
    from shelfmark.core.config import config

    current_value = normalize_optional_text(current_values.get(key))
    if current_value is not None:
        return current_value

    config_value = normalize_optional_text(config.get(key, default))
    if config_value is not None:
        return config_value

    return default


def build_client_from_config() -> AudiobookshelfClient | None:
    """Build a client from saved settings, or None when ABS isn't usable.

    Returns None rather than raising so every caller degrades to "no
    Audiobookshelf awareness" instead of failing a download or a search.
    """
    from shelfmark.audiobookshelf.client import AudiobookshelfClient
    from shelfmark.core.config import config

    if not config.get("AUDIOBOOKSHELF_ENABLED", False):
        return None

    url = normalize_http_url(normalize_optional_text(config.get("AUDIOBOOKSHELF_URL", "")) or "")
    token = normalize_optional_text(config.get("AUDIOBOOKSHELF_API_TOKEN", "")) or ""
    if not url or not token:
        return None

    return AudiobookshelfClient(url, token)


def check_audiobookshelf_connection(
    current_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Test the Audiobookshelf connection using current form values."""
    from shelfmark.audiobookshelf.client import ABS_CLIENT_ERRORS, AudiobookshelfClient

    current_values = current_values or {}

    raw_url = _resolve_setting_text(current_values, "AUDIOBOOKSHELF_URL")
    token = _resolve_setting_text(current_values, "AUDIOBOOKSHELF_API_TOKEN")

    if not raw_url:
        return {"success": False, "message": "Audiobookshelf URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "Audiobookshelf URL is invalid"}
    if not token:
        return {"success": False, "message": "API token is required"}

    try:
        client = AudiobookshelfClient(url, token)
        success, message = client.test_connection()
    except (*ABS_CLIENT_ERRORS, *_SETTINGS_ERRORS) as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": success, "message": message}


def _configured_destination_rows() -> list[dict[str, Any]]:
    """Read the persisted destination rows without validating them."""
    from shelfmark.core.config import config

    raw = config.get(DESTINATION_MAP_KEY, [])
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _known_libraries() -> list[AudiobookshelfLibrary] | None:
    """List audiobook libraries, or None when Audiobookshelf could not be asked.

    The None case matters: "Audiobookshelf says this library is gone" and
    "Audiobookshelf did not answer" must not look the same in the UI.
    """
    from shelfmark.audiobookshelf.client import ABS_CLIENT_ERRORS

    client = build_client_from_config()
    if client is None:
        return None

    try:
        return client.get_book_libraries()
    except (*ABS_CLIENT_ERRORS, *_SETTINGS_ERRORS) as e:
        logger.warning("Could not list Audiobookshelf libraries: %s", e)
        return None


def _row_text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def audiobook_destination_columns() -> list[dict[str, Any]]:
    """Build the destination table's columns, with live library names as options.

    Configured keys are always kept as options even when Audiobookshelf drops
    them or is unreachable — the table blanks any select value it considers
    invalid, which would silently erase a working destination map.
    """
    libraries = _known_libraries()
    options: list[dict[str, str]] = [
        {"value": lib.id, "label": lib.name} for lib in (libraries or [])
    ]
    known = {option["value"] for option in options}

    for row in _configured_destination_rows():
        key = _row_text(row, "key")
        if not key or key in known:
            continue
        known.add(key)
        label = _row_text(row, "name") or key
        # Only claim a library is missing when Audiobookshelf actually answered.
        if libraries is not None:
            label = f"{label} (not found in Audiobookshelf)"
        options.append({"value": key, "label": label})

    return [
        {
            "key": "key",
            "label": "Audiobookshelf library",
            "type": "select",
            "options": options,
            "placeholder": "Select a library...",
        },
        {
            "key": "path",
            "label": "Local path",
            "type": "text",
            "placeholder": "/audiobooks/fiction",
        },
    ]


def check_audiobook_destinations(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate every mapped destination path using current form values."""
    # Imported lazily: postprocess.destination imports this package's routing.
    from shelfmark.config.download_settings_handlers import _resolve_destination_test_path
    from shelfmark.download.postprocess.destination import validate_destination

    current_values = current_values or {}
    if DESTINATION_MAP_KEY in current_values:
        raw = current_values.get(DESTINATION_MAP_KEY)
        rows = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
    else:
        rows = _configured_destination_rows()

    if not rows:
        return {
            "success": False,
            "message": "No audiobook destinations are configured.",
        }

    failures: list[str] = []
    checked = 0

    for row in rows:
        key = _row_text(row, "key")
        path = _row_text(row, "path")
        label = _row_text(row, "name") or key or "(no library)"

        if not key:
            failures.append(f"{label}: no library selected")
            continue
        if not path:
            failures.append(f"{label}: no path set")
            continue

        test_path, _ = _resolve_destination_test_path(path)
        errors: list[str] = []

        def _status_callback(status: str, message: str | None) -> None:
            if status == "error" and message:
                errors.append(message)  # noqa: B023

        if validate_destination(test_path, _status_callback):
            checked += 1
        else:
            reason = errors[-1] if errors else f"cannot access {test_path}"
            failures.append(f"{label}: {reason}")

    if failures:
        return {"success": False, "message": "; ".join(failures)}

    return {
        "success": True,
        "message": f"All {checked} audiobook destinations are writable",
    }


def sync_library_index_now(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh the library index on demand and report what happened.

    Never raises: a settings button that returns a 500 tells the admin nothing
    about their setup, which is the only thing they are here to find out.
    """
    del current_values

    from shelfmark.audiobookshelf import library_sync
    from shelfmark.audiobookshelf.client import ABS_CLIENT_ERRORS

    try:
        result = library_sync.run_sync_now()
    except (*ABS_CLIENT_ERRORS, *_SETTINGS_ERRORS) as e:
        return {"success": False, "message": f"Library sync failed: {e!s}"}

    return {"success": result.success, "message": result.message}


def on_save_audiobookshelf(values: dict[str, Any]) -> dict[str, Any]:
    """Stamp library names onto destination rows as they are saved.

    The approve dialog reads this map straight from config, so the names have
    to be stored here — approving a request must never depend on Audiobookshelf
    being up.
    """
    saved = dict(values)
    raw = saved.get(DESTINATION_MAP_KEY)
    if not isinstance(raw, list):
        return {"error": False, "values": saved}

    libraries = _known_libraries()
    names = {lib.id: lib.name for lib in (libraries or [])}

    stamped: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        key = _row_text(row, "key")
        stamped.append(
            {
                **row,
                "key": key,
                "path": _row_text(row, "path"),
                "name": names.get(key) or _row_text(row, "name") or key,
            }
        )

    saved[DESTINATION_MAP_KEY] = stamped
    return {"error": False, "values": saved}


register_on_save("audiobookshelf", on_save_audiobookshelf)


@register_settings(
    name="audiobookshelf",
    display_name="Audiobookshelf",
    icon="book",
    order=60,
)
def audiobookshelf_settings() -> list[SettingsField]:
    """Audiobookshelf connection settings."""
    return [
        HeadingField(
            key="audiobookshelf_heading",
            title="Audiobookshelf Integration",
            description=(
                "Read-only access to your Audiobookshelf server, used to name your "
                "libraries when routing audiobook downloads and to flag books you "
                "already own. Shelfmark never writes to Audiobookshelf."
            ),
            link_url="https://www.audiobookshelf.org",
            link_text="audiobookshelf.org",
        ),
        CheckboxField(
            key="AUDIOBOOKSHELF_ENABLED",
            label="Enable Audiobookshelf integration",
            default=False,
            description="Turn on library-aware routing and duplicate detection.",
        ),
        TextField(
            key="AUDIOBOOKSHELF_URL",
            label="Audiobookshelf URL",
            description=(
                "Base URL Shelfmark uses to reach Audiobookshelf. This is a "
                "server-to-server address and may differ from the browser link set "
                "as the Audiobook Library URL under General."
            ),
            placeholder="http://audiobookshelf:13378",
            required=True,
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        PasswordField(
            key="AUDIOBOOKSHELF_API_TOKEN",
            label="API Token",
            description="Found in Audiobookshelf: Settings > Users > (your user) > API Token",
            required=True,
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_audiobookshelf",
            label="Test Connection",
            description="Verify the URL and token, and list what libraries were found",
            style="primary",
            callback=check_audiobookshelf_connection,
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        HeadingField(
            key="audiobook_destinations_heading",
            title="Audiobook Destinations",
            description=(
                "Route approved audiobooks to a folder per Audiobookshelf library. "
                "Paths are the ones this container writes to, which are usually not "
                "the paths Audiobookshelf reports. Leave the table empty to send "
                "every audiobook to the single destination set under Downloads."
            ),
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        TableField(
            key=DESTINATION_MAP_KEY,
            label="Library destinations",
            description=(
                "Only libraries with a path can be chosen when approving a request. "
                "Skip a library to keep it out of the picker."
            ),
            columns=audiobook_destination_columns,
            default=[],
            add_label="Add destination",
            empty_message="No library destinations configured yet.",
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_audiobook_destinations",
            label="Test Destinations",
            description="Check that every mapped path exists and is writable",
            callback=check_audiobook_destinations,
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        HeadingField(
            key="audiobookshelf_library_index_heading",
            title="Already In Library",
            description=(
                "Shelfmark keeps a local index of every book-type Audiobookshelf "
                "library and flags search results you already own. The flag is "
                "advisory — re-acquiring a better edition is still one click away."
            ),
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        CheckboxField(
            key=LIBRARY_INDEX_ENABLED_KEY,
            label="Flag books already in your library",
            default=True,
            description="Turn off to stop indexing and hide the badges.",
            show_when={"field": "AUDIOBOOKSHELF_ENABLED", "value": True},
        ),
        NumberField(
            key=LIBRARY_INDEX_INTERVAL_KEY,
            label="Refresh interval (hours)",
            default=1,
            min_value=1,
            max_value=168,
            description=(
                "How often the index is rebuilt. Books added to Audiobookshelf "
                "since the last refresh will not be flagged yet."
            ),
            show_when={"field": LIBRARY_INDEX_ENABLED_KEY, "value": True},
        ),
        ActionButton(
            key="sync_audiobookshelf_library_index",
            label="Sync Library Now",
            description="Rebuild the index immediately instead of waiting for the next refresh",
            callback=sync_library_index_now,
            show_when={"field": LIBRARY_INDEX_ENABLED_KEY, "value": True},
        ),
    ]
