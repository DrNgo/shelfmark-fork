"""Tests for the audiobook destination map settings: columns, validation, save."""

from typing import Any
from unittest.mock import patch

from shelfmark.audiobookshelf import settings as abs_settings
from shelfmark.audiobookshelf.client import AudiobookshelfLibrary
from shelfmark.core.settings_registry import TableField
from tests.audiobookshelf.test_destinations import patch_config

FICTION = AudiobookshelfLibrary(id="lib-fiction", name="Fiction", media_type="book")
KIDS = AudiobookshelfLibrary(id="lib-kids", name="Kids", media_type="book")


class FakeLibraryClient:
    """Client stub returning a fixed set of audiobook libraries."""

    def __init__(self, libraries: list[AudiobookshelfLibrary] | None = None) -> None:
        self._libraries = libraries or [FICTION, KIDS]

    def get_book_libraries(self) -> list[AudiobookshelfLibrary]:
        return self._libraries


class UnreachableClient:
    """Client stub standing in for an Audiobookshelf server that is down."""

    def get_book_libraries(self) -> list[AudiobookshelfLibrary]:
        msg = "Connection refused"
        raise ConnectionError(msg)


def patch_client(client: object | None):
    return patch.object(abs_settings, "build_client_from_config", lambda: client)


def option_values(columns: list[dict[str, Any]], column_key: str) -> list[str]:
    column = next(c for c in columns if c["key"] == column_key)
    return [opt["value"] for opt in column.get("options", [])]


class TestDestinationTableField:
    """The tab exposes an editable library → path map."""

    def test_tab_includes_the_destination_map_table(self):
        fields = abs_settings.audiobookshelf_settings()
        table = next((f for f in fields if f.key == "AUDIOBOOK_DESTINATIONS"), None)

        assert isinstance(table, TableField)

    def test_columns_are_library_and_path(self):
        with patch_config({}), patch_client(FakeLibraryClient()):
            columns = abs_settings.audiobook_destination_columns()

        assert [c["key"] for c in columns] == ["key", "path"]

    def test_library_options_come_from_audiobookshelf(self):
        with patch_config({}), patch_client(FakeLibraryClient()):
            columns = abs_settings.audiobook_destination_columns()

        assert option_values(columns, "key") == ["lib-fiction", "lib-kids"]

    def test_keeps_configured_keys_as_options_when_audiobookshelf_is_down(self):
        """Otherwise the table blanks saved rows the moment ABS is unreachable."""
        configured = {
            "AUDIOBOOK_DESTINATIONS": [
                {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"}
            ]
        }

        with patch_config(configured), patch_client(UnreachableClient()):
            columns = abs_settings.audiobook_destination_columns()

        assert option_values(columns, "key") == ["lib-fiction"]

    def test_marks_configured_keys_that_audiobookshelf_no_longer_reports(self):
        configured = {
            "AUDIOBOOK_DESTINATIONS": [
                {"key": "lib-removed", "name": "Removed", "path": "/audiobooks/removed"}
            ]
        }

        with patch_config(configured), patch_client(FakeLibraryClient()):
            columns = abs_settings.audiobook_destination_columns()

        key_column = next(c for c in columns if c["key"] == "key")
        removed = next(opt for opt in key_column["options"] if opt["value"] == "lib-removed")

        assert "not found" in removed["label"].lower()

    def test_no_options_when_audiobookshelf_is_unconfigured(self):
        with patch_config({}), patch_client(None):
            columns = abs_settings.audiobook_destination_columns()

        assert option_values(columns, "key") == []


class TestCheckAudiobookDestinations:
    """The per-row writability check."""

    def test_reports_when_nothing_is_configured(self):
        with patch_config({}):
            result = abs_settings.check_audiobook_destinations({})

        assert result["success"] is False
        assert "no audiobook destinations" in result["message"].lower()

    def test_validates_every_configured_path(self, tmp_path):
        fiction = tmp_path / "fiction"
        kids = tmp_path / "kids"
        values = {
            "AUDIOBOOK_DESTINATIONS": [
                {"key": "lib-fiction", "name": "Fiction", "path": str(fiction)},
                {"key": "lib-kids", "name": "Kids", "path": str(kids)},
            ]
        }

        with patch_config({}):
            result = abs_settings.check_audiobook_destinations(values)

        assert result["success"] is True
        assert "2" in result["message"]

    def test_names_the_destination_that_failed(self, tmp_path):
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o500)
        values = {
            "AUDIOBOOK_DESTINATIONS": [
                {"key": "lib-kids", "name": "Kids", "path": str(readonly / "nested")},
            ]
        }

        try:
            with patch_config({}):
                result = abs_settings.check_audiobook_destinations(values)
        finally:
            readonly.chmod(0o700)

        assert result["success"] is False
        assert "Kids" in result["message"]

    def test_rejects_a_row_with_a_library_but_no_path(self):
        values = {"AUDIOBOOK_DESTINATIONS": [{"key": "lib-kids", "name": "Kids", "path": ""}]}

        with patch_config({}):
            result = abs_settings.check_audiobook_destinations(values)

        assert result["success"] is False
        assert "Kids" in result["message"]

    def test_prefers_unsaved_form_values_over_saved_config(self, tmp_path):
        saved = {"AUDIOBOOK_DESTINATIONS": [{"key": "lib-old", "name": "Old", "path": "/nope"}]}
        edited = {
            "AUDIOBOOK_DESTINATIONS": [
                {"key": "lib-kids", "name": "Kids", "path": str(tmp_path / "kids")}
            ]
        }

        with patch_config(saved):
            result = abs_settings.check_audiobook_destinations(edited)

        assert result["success"] is True


class TestOnSaveStampsLibraryNames:
    """Names are stamped at save time so the approve dialog never dials ABS."""

    def test_fills_in_the_library_name(self):
        values = {"AUDIOBOOK_DESTINATIONS": [{"key": "lib-kids", "path": "/audiobooks/kids"}]}

        with patch_client(FakeLibraryClient()):
            result = abs_settings.on_save_audiobookshelf(values)

        assert result["error"] is False
        assert result["values"]["AUDIOBOOK_DESTINATIONS"][0]["name"] == "Kids"

    def test_keeps_the_previous_name_when_audiobookshelf_is_down(self):
        values = {
            "AUDIOBOOK_DESTINATIONS": [
                {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"}
            ]
        }

        with patch_client(UnreachableClient()):
            result = abs_settings.on_save_audiobookshelf(values)

        assert result["values"]["AUDIOBOOK_DESTINATIONS"][0]["name"] == "Kids"

    def test_leaves_other_tabs_values_untouched(self):
        values = {"AUDIOBOOKSHELF_URL": "http://abs:13378"}

        with patch_client(FakeLibraryClient()):
            result = abs_settings.on_save_audiobookshelf(values)

        assert result["values"] == {"AUDIOBOOKSHELF_URL": "http://abs:13378"}
