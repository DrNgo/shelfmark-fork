"""Grimmory connection and library-check settings."""

from shelfmark.config.booklore_settings import check_booklore_connection
from shelfmark.core.library_providers.grimmory import ENABLED_KEY
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)
from shelfmark.grimmory.client import BOOKLORE_DISPLAY_NAME


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
            label=f"{BOOKLORE_DISPLAY_NAME} URL",
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
                f"Shelfmark checks your {BOOKLORE_DISPLAY_NAME} library and marks ebook "
                "search results you already own; Hardcover sync and automatic downloads "
                "skip them."
            ),
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        CheckboxField(
            key=ENABLED_KEY,
            label="Flag ebooks already in your library",
            default=True,
            description="Turn off to stop checking this library.",
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
    ]
