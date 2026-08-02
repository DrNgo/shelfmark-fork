"""Grimmory connection and library-index settings."""

from typing import Any

from shelfmark.config.booklore_settings import check_booklore_connection
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)
from shelfmark.grimmory.client import BOOKLORE_DISPLAY_NAME
from shelfmark.library.index import SOURCE_GRIMMORY
from shelfmark.library.providers.grimmory import (
    LIBRARY_INDEX_ENABLED_KEY,
    LIBRARY_INDEX_INTERVAL_KEY,
)
from shelfmark.library.scheduler import run_sync_now


def sync_grimmory_library_index(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild the Grimmory slice of the index on demand."""
    del current_values
    result = run_sync_now(SOURCE_GRIMMORY)
    return {"success": result.success, "message": result.message}


@register_settings(
    name="grimmory",
    display_name=BOOKLORE_DISPLAY_NAME,
    icon="book-open",
    order=61,
)
def grimmory_settings() -> list[SettingsField]:
    """Grimmory connection settings."""
    return [
        HeadingField(
            key="grimmory_heading",
            title=f"{BOOKLORE_DISPLAY_NAME} Integration",
            description=(
                f"Connection to your {BOOKLORE_DISPLAY_NAME} server (formerly BookLore). "
                "Used to flag ebooks you already own, and to upload downloads when the "
                "book output mode is set to API upload."
            ),
            link_url="https://github.com/grimmory-tools/grimmory",
            link_text="grimmory-tools/grimmory",
        ),
        CheckboxField(
            key="BOOKLORE_ENABLED",
            label=f"Enable {BOOKLORE_DISPLAY_NAME} integration",
            default=False,
            description="Turn on duplicate detection against your ebook library.",
        ),
        TextField(
            key="BOOKLORE_HOST",
            label="Grimmory URL",
            description=f"Base URL of your {BOOKLORE_DISPLAY_NAME} instance",
            placeholder="http://grimmory:6060",
            required=True,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        TextField(
            key="BOOKLORE_USERNAME",
            label="Username",
            description=(
                f"{BOOKLORE_DISPLAY_NAME} account username. What this account can see is "
                "what gets indexed — a non-admin only sees its assigned libraries."
            ),
            required=True,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        PasswordField(
            key="BOOKLORE_PASSWORD",
            label="Password",
            description=f"{BOOKLORE_DISPLAY_NAME} account password",
            required=True,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_grimmory",
            label="Test Connection",
            description="Verify the URL and credentials, and report what this account can see",
            style="primary",
            callback=check_booklore_connection,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        HeadingField(
            key="grimmory_library_index_heading",
            title="Already In Library",
            description=(
                f"Shelfmark keeps a local index of your {BOOKLORE_DISPLAY_NAME} library and "
                "flags ebook search results you already own. The flag is advisory — "
                "re-acquiring a better edition is still one click away."
            ),
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        CheckboxField(
            key=LIBRARY_INDEX_ENABLED_KEY,
            label="Flag ebooks already in your library",
            default=True,
            description="Turn off to stop indexing and hide the badges.",
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        NumberField(
            key=LIBRARY_INDEX_INTERVAL_KEY,
            label="Refresh interval (hours)",
            default=1,
            min_value=1,
            max_value=168,
            description=(
                f"How often the index is rebuilt. Books added to {BOOKLORE_DISPLAY_NAME} "
                "since the last refresh will not be flagged yet."
            ),
            show_when={"field": LIBRARY_INDEX_ENABLED_KEY, "value": True},
        ),
        ActionButton(
            key="sync_grimmory_library_index",
            label="Sync Library Now",
            description="Rebuild the index immediately instead of waiting for the next refresh",
            callback=sync_grimmory_library_index,
            show_when={"field": LIBRARY_INDEX_ENABLED_KEY, "value": True},
        ),
    ]
