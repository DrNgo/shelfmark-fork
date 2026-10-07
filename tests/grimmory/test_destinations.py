"""Tests for Grimmory upload destination keys (`grimmory:<libraryId>:<pathId>`)."""

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
