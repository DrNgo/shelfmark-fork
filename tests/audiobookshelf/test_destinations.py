"""Tests for the audiobook destination map (multi-library routing)."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

from shelfmark.audiobookshelf import destinations


def config_getter(values: dict[str, Any]):
    """Build a config.get replacement backed by a plain dict."""

    def getter(key: str, default: Any = "", *, user_id: int | None = None) -> Any:
        del user_id
        return values.get(key, default)

    return getter


def patch_config(values: dict[str, Any]):
    """Patch the shared config singleton's get() for the duration of a with-block."""
    from shelfmark.core.config import config

    return patch.object(config, "get", config_getter(values))


def rows(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"AUDIOBOOK_DESTINATIONS": list(entries)}


class TestGetDestinationMap:
    """Reading the stored destination map out of config."""

    def test_returns_empty_when_unconfigured(self):
        with patch_config({}):
            assert destinations.get_destination_map() == []

    def test_parses_rows_into_destinations(self):
        with patch_config(
            rows(
                {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
                {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"},
            )
        ):
            mapped = destinations.get_destination_map()

        assert [d.key for d in mapped] == ["lib-fiction", "lib-kids"]
        assert mapped[0].name == "Fiction"
        assert mapped[0].path == "/audiobooks/fiction"

    def test_drops_rows_without_a_path(self):
        """A library with no local path is not routable — it must not appear as an option."""
        with patch_config(
            rows(
                {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
                {"key": "lib-podcasts", "name": "Podcasts", "path": "   "},
            )
        ):
            mapped = destinations.get_destination_map()

        assert [d.key for d in mapped] == ["lib-fiction"]

    def test_drops_rows_without_a_key(self):
        with patch_config(rows({"key": "", "name": "Orphan", "path": "/audiobooks/orphan"})):
            assert destinations.get_destination_map() == []

    def test_first_row_wins_for_duplicate_keys(self):
        with patch_config(
            rows(
                {"key": "lib-fiction", "name": "Fiction", "path": "/first"},
                {"key": "lib-fiction", "name": "Fiction again", "path": "/second"},
            )
        ):
            mapped = destinations.get_destination_map()

        assert len(mapped) == 1
        assert mapped[0].path == "/first"

    def test_name_falls_back_to_key(self):
        with patch_config(rows({"key": "lib-fiction", "path": "/audiobooks/fiction"})):
            assert destinations.get_destination_map()[0].name == "lib-fiction"

    def test_strips_surrounding_whitespace(self):
        with patch_config(
            rows({"key": " lib-fiction ", "name": " Fiction ", "path": " /audiobooks/fiction "})
        ):
            mapped = destinations.get_destination_map()

        assert mapped[0].key == "lib-fiction"
        assert mapped[0].name == "Fiction"
        assert mapped[0].path == "/audiobooks/fiction"

    def test_ignores_a_non_list_config_value(self):
        """Hand-edited config files can hold anything; never crash a download over it."""
        with patch_config({"AUDIOBOOK_DESTINATIONS": "not-a-table"}):
            assert destinations.get_destination_map() == []

    def test_ignores_non_object_rows(self):
        with patch_config({"AUDIOBOOK_DESTINATIONS": ["nope", 42, None]}):
            assert destinations.get_destination_map() == []


class TestResolveDestinationPath:
    """Turning an explicit destination key into a path, or declining to."""

    def test_returns_none_without_a_key(self):
        with patch_config(rows({"key": "lib-fiction", "path": "/audiobooks/fiction"})):
            assert destinations.resolve_destination_path(None) is None
            assert destinations.resolve_destination_path("  ") is None

    def test_resolves_a_configured_key(self):
        with patch_config(rows({"key": "lib-fiction", "path": "/audiobooks/fiction"})):
            resolved = destinations.resolve_destination_path("lib-fiction")

        assert resolved == Path("/audiobooks/fiction")

    def test_unknown_key_falls_back_with_a_warning(self):
        """A dangling key (library removed from the map) must never fail a download."""
        with (
            patch_config(rows({"key": "lib-fiction", "path": "/audiobooks/fiction"})),
            patch.object(destinations.logger, "warning") as mock_warning,
        ):
            resolved = destinations.resolve_destination_path("lib-deleted")

        assert resolved is None
        assert mock_warning.called
        warning_text = mock_warning.call_args.args[0] % tuple(mock_warning.call_args.args[1:])
        assert "lib-deleted" in warning_text

    def test_expands_the_user_placeholder_inside_the_mapped_path(self):
        with patch_config(rows({"key": "lib-fiction", "path": "/audiobooks/{User}/fiction"})):
            resolved = destinations.resolve_destination_path("lib-fiction", username="ada")

        assert resolved == Path("/audiobooks/ada/fiction")


class TestDestinationOptions:
    """Options surfaced to the approve dialog."""

    def test_lists_configured_destinations(self):
        with patch_config(
            rows(
                {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
                {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"},
            )
        ):
            options = destinations.list_destination_options()

        assert options == [
            {"key": "lib-fiction", "name": "Fiction"},
            {"key": "lib-kids", "name": "Kids"},
        ]

    def test_omits_paths_so_the_ui_never_leaks_the_filesystem_layout(self):
        with patch_config(rows({"key": "lib-fiction", "name": "Fiction", "path": "/srv/media"})):
            options = destinations.list_destination_options()

        assert "path" not in options[0]
