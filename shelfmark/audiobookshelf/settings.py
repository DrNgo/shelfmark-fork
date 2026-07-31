"""Audiobookshelf settings registration and config-backed client construction."""

from typing import TYPE_CHECKING, Any

from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)
from shelfmark.core.utils import normalize_http_url

if TYPE_CHECKING:
    from shelfmark.audiobookshelf.client import AudiobookshelfClient

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
    ]
