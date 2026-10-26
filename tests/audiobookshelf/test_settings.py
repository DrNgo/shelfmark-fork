"""Tests for the Audiobookshelf settings tab and its connection test."""

from typing import Any
from unittest.mock import patch

from shelfmark.audiobookshelf import settings as abs_settings
from shelfmark.core.settings_registry import ActionButton


def config_getter(values: dict[str, Any]):
    """Build a config.get replacement backed by a plain dict."""

    def getter(key: str, default: Any = "") -> Any:
        return values.get(key, default)

    return getter


def patch_config(values: dict[str, Any]):
    """Patch the shared config singleton's get() for the duration of a with-block."""
    from shelfmark.core.config import config

    return patch.object(config, "get", config_getter(values))


def get_field(fields, key):
    return next((f for f in fields if f.key == key), None)


class FakeClient:
    """Stand-in for AudiobookshelfClient recording its constructor args."""

    last_args: tuple[str, str] | None = None

    def __init__(self, url: str, api_token: str) -> None:
        FakeClient.last_args = (url, api_token)

    def test_connection(self) -> tuple[bool, str]:
        return True, "Connected — found 3 libraries (2 audiobook)"


class TestSettingsFields:
    """The tab exposes the fields the integration needs."""

    def test_exposes_enable_and_test_button_but_no_connection_fields(self):
        """URL and token are upstream's General-tab fields; this tab must not duplicate them."""
        fields = abs_settings.audiobookshelf_settings()

        assert isinstance(get_field(fields, "test_audiobookshelf"), ActionButton)
        assert get_field(fields, "AUDIOBOOKSHELF_ENABLED") is not None
        for key in ("AUDIOBOOKSHELF_URL", "AUDIOBOOKSHELF_TOKEN", "AUDIOBOOKSHELF_API_TOKEN"):
            assert get_field(fields, key) is None


class TestConnectionKeys:
    """The client reads upstream's token, falling back to the fork's old one."""

    def _built_args(self, values):
        with (
            patch_config({"AUDIOBOOKSHELF_ENABLED": True, **values}),
            patch("shelfmark.audiobookshelf.client.AudiobookshelfClient", FakeClient),
        ):
            FakeClient.last_args = None
            abs_settings.build_client_from_config()
        return FakeClient.last_args

    def test_upstream_token_wins(self):
        values = {
            "AUDIOBOOKSHELF_URL": "http://abs:13378",
            "AUDIOBOOKSHELF_TOKEN": "new",
            "AUDIOBOOKSHELF_API_TOKEN": "old",
        }
        assert self._built_args(values) == ("http://abs:13378", "new")

    def test_legacy_token_still_works(self):
        values = {"AUDIOBOOKSHELF_URL": "http://abs:13378", "AUDIOBOOKSHELF_API_TOKEN": "old"}
        assert self._built_args(values) == ("http://abs:13378", "old")


class TestConnectionCallback:
    """The Test Connection button validates before it dials."""

    def test_requires_a_url(self):
        with patch_config({}):
            result = abs_settings.check_audiobookshelf_connection({})

        assert result["success"] is False
        assert "URL" in result["message"]

    def test_requires_a_token(self):
        with patch_config({}):
            result = abs_settings.check_audiobookshelf_connection(
                {"AUDIOBOOKSHELF_URL": "http://abs:13378"}
            )

        assert result["success"] is False
        assert "token" in result["message"].lower()

    def test_prefers_unsaved_form_values_over_saved_config(self):
        """Typing a new URL and hitting Test must dial the typed one, not the saved one."""
        saved = {
            "AUDIOBOOKSHELF_URL": "http://old-host:13378",
            "AUDIOBOOKSHELF_API_TOKEN": "old-token",
        }

        with (
            patch_config(saved),
            patch("shelfmark.audiobookshelf.client.AudiobookshelfClient", FakeClient),
        ):
            result = abs_settings.check_audiobookshelf_connection(
                {"AUDIOBOOKSHELF_URL": "http://new-host:13378"},
            )

        assert result["success"] is True
        assert FakeClient.last_args == ("http://new-host:13378", "old-token")

    def test_returns_the_client_message(self):
        saved = {
            "AUDIOBOOKSHELF_URL": "http://abs:13378",
            "AUDIOBOOKSHELF_API_TOKEN": "token",
        }

        with (
            patch_config(saved),
            patch("shelfmark.audiobookshelf.client.AudiobookshelfClient", FakeClient),
        ):
            result = abs_settings.check_audiobookshelf_connection({})

        assert result["success"] is True
        assert "3 libraries" in result["message"]

    def test_reports_an_invalid_url_without_dialing(self):
        with patch_config({"AUDIOBOOKSHELF_API_TOKEN": "token"}):
            result = abs_settings.check_audiobookshelf_connection({"AUDIOBOOKSHELF_URL": "   "})

        assert result["success"] is False


class TestBuildClientFromConfig:
    """Feature code asks config for a client and gets None when ABS isn't set up."""

    def test_returns_none_when_disabled(self):
        values = {
            "AUDIOBOOKSHELF_ENABLED": False,
            "AUDIOBOOKSHELF_URL": "http://abs:13378",
            "AUDIOBOOKSHELF_API_TOKEN": "token",
        }

        with patch_config(values):
            assert abs_settings.build_client_from_config() is None

    def test_returns_none_when_url_or_token_missing(self):
        with patch_config({"AUDIOBOOKSHELF_ENABLED": True, "AUDIOBOOKSHELF_URL": "http://abs"}):
            assert abs_settings.build_client_from_config() is None

    def test_returns_a_client_when_fully_configured(self):
        values = {
            "AUDIOBOOKSHELF_ENABLED": True,
            "AUDIOBOOKSHELF_URL": "http://abs:13378/",
            "AUDIOBOOKSHELF_API_TOKEN": "token",
        }

        with patch_config(values):
            client = abs_settings.build_client_from_config()

        assert client is not None
        assert client.base_url == "http://abs:13378"
