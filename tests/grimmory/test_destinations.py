"""Tests for Grimmory upload destination keys (`grimmory:<libraryId>:<pathId>`)."""

import pytest

from shelfmark.grimmory.destinations import (
    build_destination_options,
    grimmory_destination_key,
    library_path_exists,
    parse_grimmory_destination_key,
)

LIBRARIES = [
    {
        "id": 5,
        "name": "Light Novels",
        "paths": [{"id": 8, "path": "/books/light-novels"}],
    },
    {
        "id": 3,
        "name": "Fiction",
        "paths": [
            {"id": 4, "path": "/books/fiction-b"},
            {"id": 3, "path": "/books/fiction-a"},
        ],
    },
]


class TestKeyFormat:
    def test_builds_the_namespaced_key(self):
        assert grimmory_destination_key(3, 4) == "grimmory:3:4"

    def test_parses_a_well_formed_key(self):
        assert parse_grimmory_destination_key("grimmory:3:4") == (3, 4)

    def test_round_trips(self):
        assert parse_grimmory_destination_key(grimmory_destination_key(12, 40)) == (12, 40)

    def test_rejects_anything_else(self):
        # Review Focus #1: an audiobook key, junk, padding and non-ASCII digits are
        # all malformed. Whitespace is not trimmed here: the queue already trims.
        for key in (
            "",
            "lib-kids",
            "grimmory:3",
            "grimmory:3:4:5",
            "grimmory:a:4",
            "grimmory:3:-4",
            "grimmory: 3:4",
            " grimmory:3:4",
            "grimmory:3:4 ",
            "GRIMMORY:3:4",
            "grimmory:³:4",
            "grimmory::4",
            None,
            34,
        ):
            assert parse_grimmory_destination_key(key) is None, repr(key)


class TestLibraryPathExists:
    def test_finds_a_path_in_its_library(self):
        assert library_path_exists(LIBRARIES, 3, 4) is True

    def test_a_path_of_another_library_does_not_count(self):
        assert library_path_exists(LIBRARIES, 3, 8) is False

    def test_a_missing_library(self):
        assert library_path_exists(LIBRARIES, 99, 3) is False

    def test_string_ids_from_the_api_still_match(self):
        libraries = [{"id": "3", "paths": [{"id": "4"}]}]

        assert library_path_exists(libraries, 3, 4) is True

    def test_junk_payloads_never_match(self):
        for libraries in (None, {}, "3", [None, "x", {"id": 3, "paths": "4"}]):
            assert library_path_exists(libraries, 3, 4) is False, repr(libraries)


class TestBuildDestinationOptions:
    def test_one_option_per_library_path_sorted_by_library_then_path(self):
        assert build_destination_options(LIBRARIES) == [
            {"key": "grimmory:3:3", "name": "Fiction — /books/fiction-a"},
            {"key": "grimmory:3:4", "name": "Fiction — /books/fiction-b"},
            {"key": "grimmory:5:8", "name": "Light Novels"},
        ]

    def test_a_single_path_library_is_named_by_the_library_alone(self):
        options = build_destination_options([{"id": 1, "name": "Manga", "paths": [{"id": 2}]}])

        assert options == [{"key": "grimmory:1:2", "name": "Manga"}]

    def test_unnamed_libraries_and_paths_get_fallback_names(self):
        options = build_destination_options([{"id": 7, "paths": [{"id": 1}, {"id": 2}]}])

        assert options == [
            {"key": "grimmory:7:1", "name": "Library 7 — Path 1"},
            {"key": "grimmory:7:2", "name": "Library 7 — Path 2"},
        ]

    def test_sorting_ignores_case(self):
        options = build_destination_options(
            [
                {"id": 1, "name": "zebra", "paths": [{"id": 1}]},
                {"id": 2, "name": "Apple", "paths": [{"id": 2}]},
            ]
        )

        assert [option["name"] for option in options] == ["Apple", "zebra"]

    def test_skips_rows_that_cannot_make_a_key(self):
        options = build_destination_options(
            [
                None,
                {"name": "No id", "paths": [{"id": 1}]},
                {"id": 2, "name": "No paths"},
                {"id": 3, "name": "Bad paths", "paths": "x"},
                {"id": 4, "name": "Pathless rows", "paths": [None, {"path": "/x"}]},
                {"id": True, "name": "Bool id", "paths": [{"id": 1}]},
                {"id": "x", "name": "Text id", "paths": [{"id": 1}]},
            ]
        )

        assert options == []

    def test_a_non_list_payload_gives_no_options(self):
        assert build_destination_options({"content": []}) == []


class TestDisplayOptions:
    """`get_booklore_destination_options` reads through the settings-dropdown cache."""

    SETTINGS = {
        "BOOKS_OUTPUT_MODE": "booklore",
        "BOOKLORE_HOST": "http://grimmory:6060/",
        "BOOKLORE_USERNAME": "shelfmark",
        "BOOKLORE_PASSWORD": "secret",
    }

    @pytest.fixture
    def settings(self, monkeypatch):
        from shelfmark.config import booklore_settings

        values = dict(self.SETTINGS)
        monkeypatch.setattr(
            booklore_settings.config,
            "get",
            lambda key, default=None, **_kw: values.get(key, default),
        )
        monkeypatch.setattr(
            booklore_settings,
            "_BOOKLORE_OPTIONS_CACHE",
            {"key": None, "library_options": [], "path_options": [], "destination_options": []},
        )
        return booklore_settings, values

    def test_lists_options_from_one_library_read(self, settings, monkeypatch):
        booklore_settings, _ = settings
        calls = []
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(
            booklore_settings,
            "booklore_list_libraries",
            lambda cfg, token: calls.append(cfg.base_url) or LIBRARIES,
        )

        first = booklore_settings.get_booklore_destination_options()
        second = booklore_settings.get_booklore_destination_options()

        assert first == second == build_destination_options(LIBRARIES)
        assert calls == ["http://grimmory:6060"]

    def test_the_settings_dropdowns_still_get_their_options(self, settings, monkeypatch):
        booklore_settings, _ = settings
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(booklore_settings, "booklore_list_libraries", lambda cfg, t: LIBRARIES)

        booklore_settings.get_booklore_destination_options()

        assert [o["value"] for o in booklore_settings.get_booklore_library_options()] == ["5", "3"]

    def test_unreachable_grimmory_gives_no_options(self, settings, monkeypatch):
        booklore_settings, _ = settings

        def fail(cfg):
            raise booklore_settings.BookloreError("Could not connect to Grimmory")

        monkeypatch.setattr(booklore_settings, "booklore_login", fail)

        assert booklore_settings.get_booklore_destination_options() == []

    def test_other_output_modes_give_no_options(self, settings, monkeypatch):
        booklore_settings, values = settings
        values["BOOKS_OUTPUT_MODE"] = "folder"
        monkeypatch.setattr(
            booklore_settings,
            "booklore_login",
            lambda cfg: pytest.fail("must not contact Grimmory"),
        )

        assert booklore_settings.get_booklore_destination_options() == []

    def test_missing_credentials_give_no_options(self, settings, monkeypatch):
        booklore_settings, values = settings
        values["BOOKLORE_PASSWORD"] = ""
        monkeypatch.setattr(
            booklore_settings,
            "booklore_login",
            lambda cfg: pytest.fail("must not contact Grimmory"),
        )

        assert booklore_settings.get_booklore_destination_options() == []

    @pytest.mark.parametrize("destination", ["bookdrop", " Bookdrop "])
    def test_bookdrop_mode_gives_no_options(self, settings, monkeypatch, destination):
        """Bookdrop uploads ignore the pick, so the picker must not be offered."""
        booklore_settings, values = settings
        values["BOOKLORE_DESTINATION"] = destination
        monkeypatch.setattr(
            booklore_settings,
            "booklore_login",
            lambda cfg: pytest.fail("must not contact Grimmory"),
        )

        assert booklore_settings.get_booklore_destination_options() == []
