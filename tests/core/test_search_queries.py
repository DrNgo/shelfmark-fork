"""The fallback query ladder and the identity check that stops it.

The 19 books are the ones measured against the live Prowlarr on 2026-10-07 (see the
design spec). Each fixture is built the way the release endpoint builds its input:
Hardcover's full-fetch parser (`get_book` -> `_parse_book`) with series fields, then the
endpoint's title override (`book.title = title_param`, main.py), so `search_title` is
the one `get_book` computed and `current_query` is today's real query.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from shelfmark.core.search_queries import (
    RankingIdentity,
    ReleaseMatch,
    SearchIdentity,
    any_identity_hit,
    build_fallback_queries,
    build_ranking_identity,
    build_search_identity,
    classify_release,
    clean_query,
    is_identity_hit,
    normalize_position,
)
from shelfmark.metadata_providers import BookMetadata
from shelfmark.metadata_providers.hardcover import HardcoverProvider


def _endpoint_book(
    book_id: int,
    title: str,
    subtitle: str | None,
    authors: list[str],
    series: str | None = None,
    position: object = None,
) -> BookMetadata:
    """A get_book result as /api/releases sees it, title override applied."""
    raw: dict[str, object] = {
        "id": book_id,
        "title": title,
        "subtitle": subtitle,
        "contributions": [{"author": {"name": name}} for name in authors],
    }
    if series is not None:
        raw["featured_book_series"] = {
            "position": position,
            "series": {"id": 1, "name": series, "primary_books_count": 20},
        }
    book = HardcoverProvider(api_key="test-token")._parse_book(raw)
    # main.py: `if title_param: book.title = title_param` - the release modal sends the
    # search result's title, which for Hardcover is the same `title` field.
    book.title = title
    return book


def _ladder(book: BookMetadata) -> list[str]:
    return build_fallback_queries(
        title=book.title,
        current_query=book.search_title or book.title,
        series_name=book.series_name,
        series_position=book.series_position,
    )


MT = "Mushoku Tensei: Jobless Reincarnation (Light Novel)"
MT_SUB = "Jobless Reincarnation (Light Novel)"
OL = "Overlord (Light Novel)"
DXD = "High School DxD (Light Novel)"
SH = "The Rising of the Shield Hero (Light Novel)"
DM = "Death March to the Parallel World Rhapsody"

# (book, today's current query, exact fallback list)
MEASURED_BOOKS = [
    (
        _endpoint_book(730298, f"{MT}, Vol. 3", MT_SUB, ["Rifujin na Magonote"], MT, 3),
        "Jobless Reincarnation",
        [
            "Mushoku Tensei Jobless Reincarnation Vol. 3",
            "Mushoku Tensei Jobless Reincarnation v03",
            "Mushoku Tensei Jobless Reincarnation Volume 03",
        ],
    ),
    (
        _endpoint_book(730294, f"{MT}, Vol. 7", MT_SUB, ["Rifujin na Magonote"], MT, 7),
        "Jobless Reincarnation",
        [
            "Mushoku Tensei Jobless Reincarnation Vol. 7",
            "Mushoku Tensei Jobless Reincarnation v07",
            "Mushoku Tensei Jobless Reincarnation Volume 07",
        ],
    ),
    (
        _endpoint_book(
            730290,
            f"{MT}, Vol. 11",
            MT_SUB,
            ["Rifujin na Magonote", "Shirotaka"],
            MT,
            11,
        ),
        "Jobless Reincarnation",
        [
            "Mushoku Tensei Jobless Reincarnation Vol. 11",
            "Mushoku Tensei Jobless Reincarnation v11",
            "Mushoku Tensei Jobless Reincarnation Volume 11",
        ],
    ),
    (
        _endpoint_book(
            427621,
            "Leviathan Wakes",
            "Cow at Sea",
            ["James S. A. Corey"],
            "The Expanse",
            1,
        ),
        "Leviathan Wakes",
        ["The Expanse Vol. 1", "The Expanse v01", "The Expanse Volume 01"],
    ),
    (
        _endpoint_book(
            886465,
            f"{OL}, Vol. 2: The Dark Warrior",
            "The Dark Warrior",
            ["Kugane Maruyama"],
            OL,
            2,
        ),
        "The Dark Warrior",
        [
            "Overlord Vol. 2",
            "Overlord v02",
            "Overlord Volume 02",
            "Overlord Vol. 2 The Dark Warrior",
        ],
    ),
    (
        # "Part I" is the book's name; Vol. 5 is still a distinct volume. The name rung
        # equals today's query, so it is dropped.
        _endpoint_book(
            885683,
            f"{OL}, Vol. 5: The Men of the Kingdom Part I",
            "The Men of the Kingdom Part I",
            ["Kugane Maruyama"],
            OL,
            5,
        ),
        "The Men of the Kingdom Part I",
        [
            "Overlord Vol. 5",
            "Overlord v05",
            "Overlord Volume 05",
            "Overlord Vol. 5 The Men of the Kingdom Part I",
        ],
    ),
    (
        _endpoint_book(
            1230950,
            f"{OL}, Vol. 10: The Ruler of Conspiracy",
            "The Ruler of Conspiracy",
            ["Kugane Maruyama"],
            OL,
            10,
        ),
        "The Ruler of Conspiracy",
        [
            "Overlord Vol. 10",
            "Overlord v10",
            "Overlord Volume 10",
            "Overlord Vol. 10 The Ruler of Conspiracy",
        ],
    ),
    (
        _endpoint_book(
            2486566,
            f"{DXD}, Vol. 3: Excalibur of the Moonlit Schoolyard",
            "Excalibur of the Moonlit Schoolyard",
            ["Ichiei Ishibumi"],
            DXD,
            3,
        ),
        "Excalibur of the Moonlit Schoolyard",
        [
            "High School DxD Vol. 3",
            "High School DxD v03",
            "High School DxD Volume 03",
            "High School DxD Vol. 3 Excalibur of the Moonlit Schoolyard",
        ],
    ),
    (
        _endpoint_book(
            2486567,
            f"{DXD}, Vol. 4: Vampire of the Suspended Classroom",
            "Vampire of the Suspended Classroom",
            ["Ichiei Ishibumi"],
            DXD,
            4,
        ),
        "Vampire of the Suspended Classroom",
        [
            "High School DxD Vol. 4",
            "High School DxD v04",
            "High School DxD Volume 04",
            "High School DxD Vol. 4 Vampire of the Suspended Classroom",
        ],
    ),
    (
        _endpoint_book(
            2575261,
            f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
            None,
            ["Ichiei Ishibumi"],
            DXD,
            5,
        ),
        # No subtitle: today's query is the full title with "(Light Novel)" removed,
        # which is also the cleaned-title rung, so that rung is not sent twice.
        "High School DxD Vol. 5 Hellcat of the Underworld Training Camp",
        [
            "High School DxD Vol. 5",
            "High School DxD v05",
            "Hellcat of the Underworld Training Camp",
            "High School DxD Volume 05",
        ],
    ),
    (
        _endpoint_book(
            2575267,
            f"{DXD}, Vol. 6: Holy Behind the Gymnasium",
            None,
            ["Ichiei Ishibumi"],
            DXD,
            6,
        ),
        # No subtitle: today's query is the full title with "(Light Novel)" removed,
        # which is also the cleaned-title rung, so that rung is not sent twice.
        "High School DxD Vol. 6 Holy Behind the Gymnasium",
        [
            "High School DxD Vol. 6",
            "High School DxD v06",
            "Holy Behind the Gymnasium",
            "High School DxD Volume 06",
        ],
    ),
    (
        _endpoint_book(1282767, f"{SH}, Vol. 3", None, ["Aneko Yusagi"], SH, 3),
        # Today's query is the cleaned title, which is also rung 1.
        "The Rising of the Shield Hero Vol. 3",
        [
            "The Rising of the Shield Hero v03",
            "The Rising of the Shield Hero Volume 03",
        ],
    ),
    (
        _endpoint_book(1283002, f"{SH}, Vol. 8", None, ["Aneko Yusagi"], SH, 8),
        # Today's query is the cleaned title, which is also rung 1.
        "The Rising of the Shield Hero Vol. 8",
        [
            "The Rising of the Shield Hero v08",
            "The Rising of the Shield Hero Volume 08",
        ],
    ),
    (
        _endpoint_book(1561928, f"{SH}, Vol. 15", "The Manga Companion", ["Aneko Yusagi"], SH, 15),
        # Today's query is the cleaned title, which is also rung 1.
        "The Rising of the Shield Hero Vol. 15",
        [
            "The Rising of the Shield Hero v15",
            "The Rising of the Shield Hero Volume 15",
        ],
    ),
    (
        _endpoint_book(785991, f"{DM}, Vol. 5", None, ["Hiro Ainana"], DM, 5),
        f"{DM}, Vol. 5",
        [f"{DM} Vol. 5", f"{DM} v05", f"{DM} Volume 05"],
    ),
    (
        _endpoint_book(785985, f"{DM}, Vol. 12", None, ["Hiro Ainana"], DM, 12),
        f"{DM}, Vol. 12",
        [f"{DM} Vol. 12", f"{DM} v12", f"{DM} Volume 12"],
    ),
    (
        _endpoint_book(427578, "Project Hail Mary", "A Novel", ["Andy Weir"]),
        "Project Hail Mary",
        [],
    ),
    (
        _endpoint_book(
            511526,
            "The Housemaid",
            "An Absolutely Addictive Psychological Thriller with a Jaw-dropping Twist",
            ["Freida McFadden"],
        ),
        "The Housemaid",
        [],
    ),
    (
        _endpoint_book(476001, "Reminders of Him", None, ["Colleen Hoover"]),
        "Reminders of Him",
        [],
    ),
]


class TestMeasuredBooks:
    @pytest.mark.parametrize(
        ("book", "current_query", "expected"),
        MEASURED_BOOKS,
        ids=[book.title for book, _, _ in MEASURED_BOOKS],
    )
    def test_each_measured_book_gets_its_exact_ladder(self, book, current_query, expected):
        assert (book.search_title or book.title) == current_query
        assert _ladder(book) == expected

    def test_there_are_nineteen(self):
        assert len(MEASURED_BOOKS) == 19


class TestCleaning:
    def test_medium_labels_go_anywhere_in_any_case(self):
        assert clean_query("Overlord (light NOVEL), Vol. 2") == "Overlord Vol. 2"
        assert clean_query("A (Novel) B (LN)") == "A B"

    def test_other_parentheses_stay(self):
        assert clean_query("Overlord (Manga), Vol. 2") == "Overlord (Manga) Vol. 2"

    def test_colons_and_commas_become_spaces(self):
        assert clean_query("Series:Book,  Vol. 3") == "Series Book Vol. 3"

    def test_junk_is_empty(self):
        assert clean_query(None) == ""
        assert clean_query(42) == ""

    def test_every_rung_is_cleaned(self):
        ladder = build_fallback_queries(
            title="Spice (Light Novel), Vol. 2: Wolf, Again",
            current_query="Nothing Alike",
            series_name="Spice (LN)",
            series_position=2,
        )
        assert ladder == [
            "Spice Vol. 2",
            "Spice v02",
            "Wolf Again",
            "Spice Volume 02",
            "Spice Vol. 2 Wolf Again",
        ]


class TestPositions:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, 0),
            (3, 3),
            (3.0, 3),
            ("3", 3),
            (" 07 ", 7),
            ("3.0", 3),
            (12, 12),
            (10_000, 10_000),
            ("10000", 10_000),
        ],
    )
    def test_usable_positions(self, value, expected):
        assert normalize_position(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            True,
            False,
            -1,
            -1.0,
            "-2",
            1.5,
            "1.5",
            math.nan,
            math.inf,
            "nan",
            "inf",
            "",
            "x",
            [3],
            10_001,
            "10001",
            1e300,
            "1234567",
            "9" * 5000,
            # Exact parsing: as a float this would round to 3.0.
            "3.0000000000000001",
            "1e3",
            "1_0",
            "\u0663",
            "+-3",
        ],
    )
    def test_unusable_positions(self, value):
        assert normalize_position(value) is None

    def test_volume_zero_is_a_volume(self):
        assert build_fallback_queries(
            title="Spice, Vol. 0",
            current_query="Spice, Vol. 0",
            series_name="Spice",
            series_position=0,
        ) == ["Spice Vol. 0", "Spice v00", "Spice Volume 00"]

    def test_numeric_string_and_integral_float_positions_match_the_title(self):
        for position in ("3", 3.0):
            assert build_fallback_queries(
                title="Spice, Vol. 3",
                current_query="x",
                series_name="Spice",
                series_position=position,
            )[:2] == ["Spice Vol. 3", "Spice v03"]

    @pytest.mark.parametrize("position", [1.5, True, -3, math.nan])
    def test_an_unusable_position_gives_no_series_rungs(self, position):
        assert build_fallback_queries(
            title="Spice, Vol. 3",
            current_query="x",
            series_name="Spice",
            series_position=position,
        ) == ["Spice Vol. 3"]

    def test_position_only_from_metadata_when_the_title_has_none(self):
        assert build_fallback_queries(
            title="Wolf and Parchment",
            current_query="Wolf and Parchment",
            series_name="Spice",
            series_position=4,
        ) == ["Spice Vol. 4", "Spice v04", "Spice Volume 04"]

    def test_position_only_from_the_title_when_metadata_has_none(self):
        assert build_fallback_queries(
            title="Spice, Vol. 4",
            current_query="Spice, Vol. 4",
            series_name="Spice",
            series_position=None,
        ) == ["Spice Vol. 4", "Spice v04", "Spice Volume 04"]

    def test_series_only_from_the_title_when_metadata_has_none(self):
        assert build_fallback_queries(
            title="Spice and Wolf Volume 4: Pagan Town",
            current_query="Pagan Town",
            series_name=None,
            series_position=4,
        ) == [
            "Spice and Wolf Vol. 4",
            "Spice and Wolf v04",
            "Spice and Wolf Volume 04",
            "Spice and Wolf Volume 4 Pagan Town",
        ]


class TestDistinguishingIdentities:
    def test_conflicting_positions_give_no_series_rungs(self):
        assert build_fallback_queries(
            title="Spice, Vol. 5: Wolf",
            current_query="Wolf",
            series_name="Spice",
            series_position=6,
        ) == ["Spice Vol. 5 Wolf"]

    @pytest.mark.parametrize(
        ("title", "position", "expected"),
        [
            ("Spice, Vol. 1-3", 1, ["Spice Vol. 1-3"]),
            ("Spice, Vol. 1\u20133", 1, ["Spice Vol. 1\u20133"]),
            ("Spice, Vols. 1-3", 1, ["Spice Vols. 1-3"]),
            ("Spice Omnibus 1", 1, ["Spice Omnibus 1"]),
            ("Spice: Collected Edition 1", 1, ["Spice Collected Edition 1"]),
            ("Spice Box Set 1", 1, ["Spice Box Set 1"]),
            ("Spice, Vol. 2 Part 1", 2, ["Spice Vol. 2 Part 1"]),
            ("Spice Part II", 2, ["Spice Part II"]),
            ("Spice, Vol. 1.5", 1.5, ["Spice Vol. 1.5"]),
        ],
    )
    def test_ranges_collections_parts_and_fractions_keep_only_their_own_text(
        self, title, position, expected
    ):
        assert (
            build_fallback_queries(
                title=title,
                current_query="nothing alike",
                series_name="Spice",
                series_position=position,
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("book_id", "volume", "name"),
        [
            (885683, 5, "The Men of the Kingdom Part I"),
            (885684, 6, "The Men of the Kingdom Part II"),
        ],
    )
    def test_a_part_in_the_book_name_keeps_the_series_rungs(self, book_id, volume, name):
        # Overlord vols 5 and 6 are distinct volumes whose names end in "Part I"/"Part II";
        # "Overlord v05" is what found the real light-novel release on 2026-10-07.
        book = _endpoint_book(
            book_id,
            f"{OL}, Vol. {volume}: {name}",
            name,
            ["Kugane Maruyama"],
            OL,
            volume,
        )

        assert _ladder(book) == [
            f"Overlord Vol. {volume}",
            f"Overlord v{volume:02d}",
            f"Overlord Volume {volume:02d}",
            f"Overlord Vol. {volume} {name}",
        ]

    def test_a_collected_word_in_the_book_name_keeps_the_series_rungs(self):
        ladder = build_fallback_queries(
            title="Death March, Vol. 5: The Collected Heroes",
            current_query="The Collected Heroes",
            series_name="Death March",
            series_position=5,
        )
        assert ladder[:2] == ["Death March Vol. 5", "Death March v05"]
        assert "Death March Volume 05" in ladder

    def test_an_omnibus_before_the_volume_marker_still_suppresses(self):
        assert build_fallback_queries(
            title="Spice Omnibus, Vol. 2",
            current_query="nothing alike",
            series_name="Spice",
            series_position=2,
        ) == ["Spice Omnibus Vol. 2"]

    def test_roman_numeral_volumes_only_get_the_cleaned_title(self):
        assert build_fallback_queries(
            title="Spice, Vol. III",
            current_query="Spice",
            series_name=None,
            series_position=None,
        ) == ["Spice Vol. III"]

    def test_roman_numeral_title_overrides_a_metadata_position(self):
        assert build_fallback_queries(
            title="Spice, Vol. III",
            current_query="Spice",
            series_name="Spice",
            series_position=3,
        ) == ["Spice Vol. III"]


class TestStandalones:
    @pytest.mark.parametrize(
        ("title", "current_query", "series_name", "series_position"),
        [
            ("Project Hail Mary", "Project Hail Mary", None, None),
            ("Mistborn: The Final Empire", "The Final Empire", None, None),
            ("Mistborn: The Final Empire", "The Final Empire", "Mistborn", None),
            ("Dune", "Dune", None, 1),
            ("", "", None, None),
        ],
    )
    def test_no_series_and_no_volume_means_no_fallbacks(
        self, title, current_query, series_name, series_position
    ):
        assert (
            build_fallback_queries(
                title=title,
                current_query=current_query,
                series_name=series_name,
                series_position=series_position,
            )
            == []
        )

    def test_oversized_title_volumes_are_not_parsed(self):
        for title in ("Spice, Vol. 9999999", "Spice, Vol. 10001", f"Spice, Vol. {'9' * 5000}"):
            assert build_fallback_queries(
                title=title, current_query="x", series_name="Spice", series_position=None
            ) == [clean_query(title)]

    def test_junk_input_never_raises(self):
        assert (
            build_fallback_queries(
                title=None, current_query=None, series_name=7, series_position=object()
            )
            == []
        )


class TestIdentityPredicate:
    DXD5 = SearchIdentity(series_key="High School DxD", position=5, title_tokens=("hellcat",))
    STANDALONE = SearchIdentity(title_tokens=("project", "hail", "mary"))

    def _hit(self, title: str, identity: SearchIdentity, content_type: str = "ebook") -> bool:
        return is_identity_hit(
            title,
            series_key=identity.series_key,
            position=identity.position,
            title_tokens=identity.title_tokens,
            content_type=content_type,
            title_names_volume=identity.title_names_volume,
        )

    @staticmethod
    def _leviathan() -> SearchIdentity:
        # A series book whose title carries no volume marker (The Expanse 1).
        return build_search_identity(
            title="Leviathan Wakes",
            current_query="Leviathan Wakes",
            series_name="The Expanse",
            series_position=1,
        )

    def test_a_series_book_without_a_volume_marker_is_flagged(self):
        identity = self._leviathan()

        assert (identity.series_key, identity.position) == ("The Expanse", 1)
        assert identity.title_names_volume is False
        assert identity.title_tokens == ("leviathan", "wakes")

    @pytest.mark.parametrize(
        "title",
        [
            "Leviathan Wakes - James S.A. Corey EPUB",
            "James.S.A.Corey-Leviathan.Wakes.2011.RETAIL.EPUB",
            "Reader Corey, James S A - The Expanse 01 - Leviathan Wakes (Retail)",
        ],
    )
    def test_its_natural_release_name_is_a_hit(self, title):
        assert self._hit(title, self._leviathan())

    @pytest.mark.parametrize(
        "title",
        [
            "The Expanse 1-3 Leviathan Wakes Calibans War Abaddons Gate",
            "The Expanse Books 1 - 3: Leviathan Wakes, Caliban's War, Abaddon's Gate (epub)",
            "Leviathan Wakes / Caliban's War omnibus (epub)",
            "The Expanse Box Set Leviathan Wakes (epub)",
            "The Expanse Vol. 2 Leviathan Wakes (epub)",
            "The Expanse Vol. 1.5 Leviathan Wakes (epub)",
        ],
    )
    def test_a_multi_volume_or_other_volume_release_is_not(self, title):
        assert not self._hit(title, self._leviathan())

    @pytest.mark.parametrize(
        "title",
        [
            "Leviathan Wakes & Caliban's War (epub)",
            "The Expanse Trilogy: Leviathan Wakes, Caliban's War",
            "Leviathan Wakes Expanse Books One through Three",
            "The Expanse Book 2 Caliban's War (Leviathan Wakes sequel)",
            "The Expanse #2 Calibans War - Leviathan Wakes sequel",
            "Leviathan Wakes Collection (epub)",
        ],
    )
    def test_a_set_or_another_numbered_book_naming_the_title_is_not(self, title):
        assert not self._hit(title, self._leviathan())

    def test_its_own_number_does_not_block_the_title(self):
        assert self._hit("Leviathan Wakes (The Expanse #1) epub", self._leviathan())
        assert self._hit("Leviathan Wakes - The Expanse Book 1 (epub)", self._leviathan())

    @pytest.mark.parametrize(
        ("title", "series"),
        [("The Hunger Games", "The Hunger Games"), ("Red Rising", "Red Rising Saga")],
    )
    @pytest.mark.parametrize(
        "release",
        [
            "{title} 2",
            "{title} 3 Mockingjay",
            "{title} - Catching Fire",
            "{title}: The Ballad of Songbirds and Snakes",
            "{series} 2 Golden Son",
        ],
    )
    def test_a_title_made_of_series_words_never_stops_by_title(self, title, series, release):
        identity = build_search_identity(
            title=title, current_query=title, series_name=series, series_position=1
        )

        assert not self._hit(release.format(title=title, series=series), identity)

    def test_a_wrong_title_from_the_same_series_is_not(self):
        assert not self._hit("The Expanse Calibans War (epub)", self._leviathan())
        assert not self._hit("Calibans War - James S.A. Corey EPUB", self._leviathan())

    def test_a_volume_marker_title_keeps_the_strict_series_rule(self):
        identity = build_search_identity(
            title=f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
            current_query="Hellcat of the Underworld Training Camp",
            series_name=DXD,
            series_position=5,
        )

        assert identity.title_names_volume is True
        # The title tokens alone are not enough: the series rule decides.
        assert not self._hit(
            "High School DxD Hellcat of the Underworld Training Camp (epub)", identity
        )
        assert self._hit("High School DxD v05 Hellcat of the Underworld Training Camp", identity)

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp by Ichiei Ishibumi [ENG / EPUB]",
            "Ichiei Ishibumi - [High School DxD - Volume 05] - Hellcat of the Underworld Training Camp",
            "High School DxD v05 (2015) (Digital) (danke-Empire)",
            "Seven.Seas-High.School.DxD.Vol.05.2016.Retail.eBook-BitBook",
            "High School DxD [5] (epub)",
            "High School DxD - 05 (epub)",
            "High School DxD Vol 5",
        ],
    )
    def test_the_right_volume_is_a_hit(self, title):
        assert self._hit(title, self.DXD5)

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD, Vol. 15 by Ichiei Ishibumi [ENG / EPUB]",
            "High School DxD v04 (2015) (Digital)",
            "High School DxD Vol. 5-6 (epub)",
            "High School DxD (epub)",
        ],
    )
    def test_a_wrong_missing_or_extra_volume_is_not(self, title):
        assert not self._hit(title, self.DXD5)

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD Vol. 5.5 (epub)",
            "High School DxD Vol. 5a (epub)",
            "High School DxD Vol. 5 & 6 (epub)",
            "High School DxD Vol. 5 and 6 (epub)",
            "High School DxD Vol. 5 to 7 (epub)",
            "High School DxD Vol. 5\u20147 (epub)",
            "High School DxD Vol. 5\u20137 (epub)",
            "High School DxD Vol. 5+6 (epub)",
            "High School DxD v05-07 (Digital)",
            "High School DxD v05-v07 (Digital)",
            "High School DxD [5] Vol. 5.5",
        ],
    )
    def test_an_incomplete_volume_token_is_not(self, title):
        assert not self._hit(title, self.DXD5)

    @pytest.mark.parametrize(("position", "hit"), [(1, True), (10, False)])
    def test_series_then_number_then_dash_names_the_volume(self, position, hit):
        expanse = SearchIdentity(series_key="The Expanse", position=position)
        title = "Reader Corey, James S A - The Expanse 01 - Leviathan Wakes (Retail)"

        assert self._hit(title, expanse) is hit

    def test_a_year_after_the_volume_is_not_a_fraction(self):
        assert self._hit("High.School.DxD.Vol.05.2016.eBook", self.DXD5)

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD S01E05 1080p WEB-DL x264",
            "High School DxD Vol. 5 [BD 720p]",
            "High School DxD - 05 (mkv)",
            "High School DxD Episode 5",
        ],
    )
    def test_video_is_not(self, title):
        assert not self._hit(title, self.DXD5)

    def test_another_series_is_not(self):
        assert not self._hit("Overlord, Vol. 5 by Kugane Maruyama [ENG / EPUB]", self.DXD5)

    def test_an_audiobook_does_not_stop_an_ebook_search(self):
        title = "High School DxD, Volume 5 by Ichiei Ishibumi [ENG / M4B]"
        assert not self._hit(title, self.DXD5)
        assert self._hit(title, self.DXD5, content_type="audiobook")

    def test_standalone_needs_its_title_tokens(self):
        assert self._hit("Project Hail Mary by Andy Weir [ENG / EPUB]", self.STANDALONE)
        assert self._hit("Andy.Weir-Project.Hail.Mary.2021.RETAIL.EPUB", self.STANDALONE)
        assert not self._hit("Project Hail (epub)", self.STANDALONE)
        assert not self._hit("Project Hail Mary 2160p WEB-DL", self.STANDALONE)

    def test_suppressed_identity_uses_the_full_title(self):
        # "Spice, Vol. 5: Wolf" with metadata position 6 conflicts, so the series rule is
        # off; today's query would be just "Wolf", which another series' release has.
        identity = build_search_identity(
            title="Spice, Vol. 5: Wolf",
            current_query="Wolf",
            series_name="Spice",
            series_position=6,
        )

        assert identity.title_tokens == ("spice", "5", "wolf")
        assert not self._hit("Other Series Vol. 1 Wolf EPUB", identity)
        assert self._hit("Spice Vol. 5 Wolf (epub)", identity)

    def test_fewer_than_two_title_tokens_never_stop_the_ladder(self):
        assert not self._hit("Wolf (epub)", SearchIdentity(title_tokens=("wolf",)))

    def test_junk_is_never_a_hit(self):
        for title in (None, "", "   ", 7):
            assert not self._hit(title, self.DXD5)
        assert not self._hit("anything", SearchIdentity())

    @pytest.mark.parametrize(
        ("series_key", "position", "title_tokens"),
        [
            ("High School DxD", 5, None),
            (None, 5, ("high", "school")),
            ("High School DxD", True, ("high", "school")),
            ("High School DxD", "5", ("high", "school")),
            ("High School DxD", 5, "high school"),
        ],
    )
    def test_junk_arguments_never_raise(self, series_key, position, title_tokens):
        result = is_identity_hit(
            "High School DxD Vol. 5",
            series_key=series_key,
            position=position,
            title_tokens=title_tokens,
            content_type="ebook",
        )
        assert result in (True, False)
        if title_tokens is None or isinstance(title_tokens, str):
            assert result is False


class TestComicReleases:
    """A manga or comic edition is not the light novel, even with the right volume."""

    OVERLORD5 = SearchIdentity(series_key="Overlord", position=5)
    OVERLORD5_MANGA = SearchIdentity(series_key="Overlord", position=5, book_is_comic=True)
    LIVE_RESULTS = (
        "Yen.Press-Overlord.Vol.05.Manga.2022.Hybrid.Comic.eBook-BitBook",
        "Yen.Press-Overlord.The.Undead.King.Oh.Vol.05.2022.Hybrid.Comic.eBook-BitBook",
    )

    def _hit(self, title: str, identity: SearchIdentity) -> bool:
        return is_identity_hit(
            title,
            series_key=identity.series_key,
            position=identity.position,
            title_tokens=identity.title_tokens,
            content_type="ebook",
            book_is_comic=identity.book_is_comic,
        )

    @pytest.mark.parametrize("title", LIVE_RESULTS)
    def test_the_live_manga_results_do_not_stop_a_light_novel_ladder(self, title):
        assert not self._hit(title, self.OVERLORD5)

    @pytest.mark.parametrize(
        "title",
        [
            "Overlord Vol. 5 (Graphic Novel)",
            "Overlord v05 (Comics)",
            "Overlord.Vol.05.graphic.novel",
        ],
    )
    def test_comics_and_graphic_novels_are_not_the_light_novel(self, title):
        assert not self._hit(title, self.OVERLORD5)

    def test_the_light_novel_release_still_is(self):
        assert self._hit("Overlord.v05.2018.Digital.danke-Empire", self.OVERLORD5)

    @pytest.mark.parametrize("title", LIVE_RESULTS)
    def test_a_manga_book_accepts_manga_releases(self, title):
        assert self._hit(title, self.OVERLORD5_MANGA)

    def test_any_identity_hit_uses_the_identity_s_comic_flag(self):
        assert not any_identity_hit(self.LIVE_RESULTS, self.OVERLORD5, content_type="ebook")
        assert any_identity_hit(self.LIVE_RESULTS, self.OVERLORD5_MANGA, content_type="ebook")


class TestBuildSearchIdentity:
    def test_a_series_volume_identity(self):
        identity = build_search_identity(
            title=f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
            current_query=f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
            series_name=DXD,
            series_position=5,
        )
        assert (identity.series_key, identity.position) == ("High School DxD", 5)

    def test_a_part_in_the_book_name_is_still_a_series_volume(self):
        identity = build_search_identity(
            title=f"{OL}, Vol. 5: The Men of the Kingdom Part I",
            current_query="The Men of the Kingdom Part I",
            series_name=OL,
            series_position=5,
        )
        assert (identity.series_key, identity.position) == ("Overlord", 5)

    def test_a_split_volume_falls_back_to_title_tokens(self):
        identity = build_search_identity(
            title="Spice, Vol. 2 Part 1",
            current_query="Spice Vol. 2 Part 1",
            series_name="Spice",
            series_position=2,
        )
        assert identity == SearchIdentity(title_tokens=("spice", "2", "part", "1"))

    def test_a_manga_or_comic_book_is_flagged(self):
        for title, series in (
            ("Overlord (Manga), Vol. 5", "Overlord (Manga)"),
            ("Spice", "Spice Comics"),
        ):
            identity = build_search_identity(
                title=title, current_query=title, series_name=series, series_position=5
            )
            assert identity.book_is_comic is True

        light_novel = build_search_identity(
            title=f"{OL}, Vol. 5: The Men of the Kingdom Part I",
            current_query="The Men of the Kingdom Part I",
            series_name=OL,
            series_position=5,
        )
        assert light_novel.book_is_comic is False

    def test_a_standalone_identity(self):
        identity = build_search_identity(
            title="The Housemaid",
            current_query="The Housemaid",
            series_name=None,
            series_position=None,
        )
        assert identity == SearchIdentity(title_tokens=("housemaid",), title_names_volume=False)


class TestAnyIdentityHit:
    def test_true_when_one_title_is_the_book(self):
        identity = SearchIdentity(series_key="Overlord", position=2)

        assert any_identity_hit(
            ["Overlord, Vol. 3", None, "Overlord v02 (2016) (Digital)"],
            identity,
            content_type="ebook",
        )

    def test_false_without_an_identity_or_a_hit(self):
        assert not any_identity_hit(["Overlord v02"], None, content_type="ebook")
        assert not any_identity_hit(
            ["Overlord, Vol. 3"],
            SearchIdentity(series_key="Overlord", position=2),
            content_type="ebook",
        )


class TestIdentityReviewFixes:
    @pytest.mark.parametrize("title", ["Spice and Wolf, Vol. 5.5", "Spice and Wolf, Vol. 1-3"])
    def test_a_numeric_volume_marker_title_never_stops_the_ladder(self, title):
        identity = build_search_identity(
            title=title, current_query=title, series_name="Spice and Wolf", series_position=5
        )
        for release in (
            "Spice and Wolf Vol. 5",
            "Spice and Wolf Vol. 5.5",
            "Spice and Wolf Vol. 1-3",
        ):
            assert not is_identity_hit(
                release,
                series_key=identity.series_key,
                position=identity.position,
                title_tokens=identity.title_tokens,
                content_type="ebook",
            )

    @pytest.mark.parametrize(
        "title", ["Overlord Vol 5, 6", "Overlord Vol. 5, Vol. 6", "Overlord Vol. 5, v06"]
    )
    def test_a_comma_separated_volume_list_is_not_a_hit(self, title):
        assert not TestIdentityPredicate()._hit(
            title, SearchIdentity(series_key="Overlord", position=5)
        )

    @pytest.mark.parametrize(
        "title", ["Overlord, Vol. 5", "Overlord Vol. 5, 2018", "Overlord Vol. 5, 1st edition"]
    )
    def test_a_comma_around_the_volume_is_still_a_hit(self, title):
        assert TestIdentityPredicate()._hit(
            title, SearchIdentity(series_key="Overlord", position=5)
        )

    @pytest.mark.parametrize("results", [None, 5, object()])
    def test_any_identity_hit_survives_junk_results(self, results):
        identity = SearchIdentity(series_key="Overlord", position=5)
        assert any_identity_hit(results, identity, content_type="ebook") is False


class TestLiveAcceptanceShapes:
    """Release names from the 2026-10-07 live acceptance run that the predicate missed."""

    @staticmethod
    def _identity(title: str, series: str, position: int) -> SearchIdentity:
        return build_search_identity(
            title=title, current_query=title, series_name=series, series_position=position
        )

    @staticmethod
    def _hit(title: str, identity: SearchIdentity) -> bool:
        return is_identity_hit(
            title,
            series_key=identity.series_key,
            position=identity.position,
            title_tokens=identity.title_tokens,
            content_type="ebook",
            title_names_volume=identity.title_names_volume,
        )

    def test_a_bracketed_series_number_names_the_volume(self):
        overlord2 = self._identity(f"{OL}, Vol. 2: The Dark Warrior", OL, 2)

        assert self._hit("Kugane Maruyama - [Overlord 02] - The Dark Warrior (epub)", overlord2)
        assert not self._hit("Kugane Maruyama - [Overlord 03] - The Bloody Valkyrie", overlord2)

    def test_a_file_version_tag_is_not_a_volume(self):
        shield3 = self._identity(f"{SH}, Vol. 3", SH, 3)

        assert self._hit(
            "Aneko Yusagi - [Shield Hero 03] - The Rising of the Shield Hero Volume 3 (v2.0) (epub)",
            shield3,
        )
        # A fractional volume is still not this volume.
        assert not self._hit("The Rising of the Shield Hero v3.5 (epub)", shield3)

    def test_an_html_escaped_ampersand_still_marks_a_bundle(self):
        leviathan = self._identity("Leviathan Wakes", "The Expanse", 1)

        # Named by title: "&amp;" is the same "&" that marks two books in one release.
        assert not self._hit("Leviathan Wakes &amp; Caliban's War (epub)", leviathan)
        assert self._hit("James S A Corey - The Expanse 01 Leviathan Wakes (epub, mobi)", leviathan)


# --- Release ranking (classify_release) ----------------------------------------------


def _ranking(
    title: str, series: str | None, position: object, authors: tuple[str, ...] = ()
) -> RankingIdentity:
    return build_ranking_identity(
        title=title,
        current_query=title,
        series_name=series,
        series_position=position,
        authors=list(authors),
    )


DXD5_RANK = _ranking(
    f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp", DXD, 5, ("Ichiei Ishibumi",)
)
OVERLORD2_RANK = _ranking(f"{OL}, Vol. 2: The Dark Warrior", OL, 2, ("Kugane Maruyama",))
LEVIATHAN_RANK = _ranking("Leviathan Wakes", "The Expanse", 1, ("James S. A. Corey",))
CALIBAN_RANK = _ranking("Caliban's War", "The Expanse", 2, ("James S. A. Corey",))


def _classify(
    name: object,
    identity: Any,
    *,
    formats: Any = (),
    content_type: object = None,
    author: object = None,
) -> ReleaseMatch:
    """identity and formats are Any so the junk-input tests can pass junk."""
    return classify_release(
        name=name,
        formats=formats,
        content_type=content_type,
        release_author=author,
        identity=identity,
    )


def _volume(name: str, identity: RankingIdentity) -> tuple[str, int | None]:
    match = _classify(name, identity)
    return match.volume, match.other_volume


class TestRankingIdentity:
    def test_it_resolves_series_and_position_like_the_ladder(self):
        assert DXD5_RANK == RankingIdentity(
            series_key="High School DxD",
            position=5,
            title_tokens=(
                "high",
                "school",
                "dxd",
                "5",
                "hellcat",
                "underworld",
                "training",
                "camp",
            ),
            title_names_volume=True,
            book_is_comic=False,
            authors=("Ichiei Ishibumi",),
        )

    def test_a_natural_title_series_book(self):
        assert LEVIATHAN_RANK.series_key == "The Expanse"
        assert LEVIATHAN_RANK.position == 1
        assert LEVIATHAN_RANK.title_names_volume is False

    def test_a_graphic_novel_request_is_a_comic(self):
        assert _ranking("Watchmen (Graphic Novel)", None, None).book_is_comic is True
        assert _ranking("Overlord (Manga), Vol. 2", "Overlord (Manga)", 2).book_is_comic is True

    def test_comical_is_not_a_comic(self):
        assert _ranking("The Comical Adventures", None, None).book_is_comic is False
        assert _ranking("Mangarama", "Comicality", 1).book_is_comic is False

    def test_junk_authors_are_dropped(self):
        identity = build_ranking_identity(
            title="Dune",
            current_query="Dune",
            series_name=None,
            series_position=None,
            authors=["Frank Herbert", None, "  ", 5],
        )
        assert identity.authors == ("Frank Herbert",)
        assert _ranking("Dune", None, None).authors == ()


class TestRankingVolume:
    def test_the_requested_volume_is_a_match(self):
        name = (
            "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp "
            "by Ichiei Ishibumi [ENG / EPUB]"
        )
        assert _volume(name, DXD5_RANK) == ("match", None)

    def test_volume_25_is_another_volume(self):
        name = "High School DxD - Volume 25 by Ichiei Ishibumi [ENG / EPUB]"
        assert _volume(name, DXD5_RANK) == ("other", 25)

    def test_a_bracketed_series_number_is_a_match(self):
        name = "Kugane Maruyama - [Overlord 02] - The Dark Warrior (epub)"
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    def test_a_hash_number_names_another_volume(self):
        assert _volume("Overlord #3 EPUB", OVERLORD2_RANK) == ("other", 3)

    def test_underscore_scene_names_are_explicit_volume_syntax(self):
        assert _volume("Overlord_Vol_02_2018_Retail_EPUB", OVERLORD2_RANK) == ("match", None)
        assert _volume("Overlord_Vol_03_2018_Retail_EPUB", OVERLORD2_RANK) == ("other", 3)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Overlord v02 (2016) (Digital) (danke-Empire)", ("match", None)),
            ("Yen.Press-Overlord.Vol.02.2016.Retail.eBook-BitBook", ("match", None)),
            ("Overlord 02 - The Dark Warrior (epub)", ("match", None)),
            ("Overlord 02 (2016)", ("match", None)),
            ("Overlord 02 2016 epub", ("match", None)),
            ("Overlord 02 epub", ("match", None)),
            ("Overlord 02", ("match", None)),
            ("Overlord Book 3 EPUB", ("other", 3)),
            ("Overlord Volume 10", ("other", 10)),
        ],
    )
    def test_every_explicit_form(self, name, expected):
        assert _volume(name, OVERLORD2_RANK) == expected

    def test_another_numbered_expanse_book_is_another_volume(self):
        name = "The Expanse Book 2 Caliban's War by James S. A. Corey EPUB"
        assert _volume(name, LEVIATHAN_RANK) == ("other", 2)

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD [5] (epub)",
            "High School DxD - 5 (epub)",
            "High School DxD (epub)",
            "Overlord 02 The Dark Warrior",
        ],
    )
    def test_a_bare_number_is_not_volume_syntax(self, name):
        identity = DXD5_RANK if "DxD" in name else OVERLORD2_RANK
        assert _volume(name, identity) == ("unknown", None)

    def test_a_page_count_is_not_another_volume(self):
        for name in (
            "The Expanse Leviathan Wakes [320] EPUB",
            "The Expanse Leviathan Wakes - 451 pages EPUB",
        ):
            assert _volume(name, CALIBAN_RANK) == ("unknown", None)
            # The page count does not veto the natural-title match either.
            assert _volume(name, LEVIATHAN_RANK) == ("match", None)

    def test_a_standalone_book_has_no_volume(self):
        standalone = _ranking("The Housemaid", None, None)
        assert _volume("The Housemaid Vol. 2 (epub)", standalone) == ("unknown", None)


class TestRankingCollections:
    @pytest.mark.parametrize(
        "name",
        [
            "Overlord Vol. 2 Omnibus EPUB",
            "Overlord Box Set Vol. 2",
            "Overlord Boxed Set v02",
            "Overlord The Complete Series Vol. 2",
            "Overlord Collection Vol. 2",
            "Overlord Trilogy Vol. 2",
            "Overlord Duology v02",
            "Overlord Quartet v02",
            "Overlord Books 1-3",
            "Overlord Vol. 1-3",
            "Overlord Vols 2-4",
            "Overlord Vol. 2 & 3",
            "Overlord Vol. 2 and 3",
            "Overlord v02-v03",
            "Overlord Vol. 2, 3",
            "Overlord Vol. 2 Vol. 3",
            "Overlord 1-3 (epub)",
        ],
    )
    def test_collection_evidence_is_unknown(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("unknown", None)

    def test_contributor_separators_are_not_a_bundle(self):
        for name in (
            "Leviathan Wakes 2nd edition EPUB",
            "Leviathan Wakes James S. A. Corey & Daniel Abraham EPUB",
            "Leviathan Wakes James S. A. Corey &amp; Daniel Abraham EPUB",
            "Leviathan Wakes Corey / Abraham + Bonus EPUB",
        ):
            assert _volume(name, LEVIATHAN_RANK) == ("match", None)

    def test_the_same_number_twice_is_one_volume(self):
        assert _volume("Overlord Vol. 2 [Overlord 02]", OVERLORD2_RANK) == ("match", None)


class TestRankingNaturalTitles:
    def test_the_title_words_name_the_book(self):
        assert _volume("Leviathan Wakes (The Expanse #1) epub", LEVIATHAN_RANK) == (
            "match",
            None,
        )
        assert _volume("James S A Corey - Leviathan Wakes (epub)", LEVIATHAN_RANK) == (
            "match",
            None,
        )

    def test_explicit_syntax_naming_another_number_is_not_the_book(self):
        assert _volume("Leviathan Wakes #2 epub", LEVIATHAN_RANK) == ("unknown", None)

    def test_another_title_in_the_series_is_not_the_book(self):
        assert _volume("The Expanse - Calibans War (epub)", LEVIATHAN_RANK) == ("unknown", None)

    def test_a_title_made_of_series_words_never_matches_by_title(self):
        hunger = _ranking("The Hunger Games", "The Hunger Games", 1)
        assert _volume("The Hunger Games (epub)", hunger) == ("unknown", None)
        assert _volume("The Hunger Games #1 (epub)", hunger) == ("match", None)


class TestRankingMedium:
    def test_episodes_in_a_light_novel_title_is_not_video(self):
        match = _classify(
            "Overlord Vol. 2: Episodes of the Kingdom EPUB", OVERLORD2_RANK, formats=["epub"]
        )
        assert (match.medium, match.compatible) == ("ebook", True)

    @pytest.mark.parametrize(
        "name",
        [
            "Yen.Press-Overlord.Vol.02.Manga.2022.Hybrid.Comic.eBook-BitBook",
            "Yen.Press-Overlord.The.Undead.King.Oh.Vol.02.2022.Hybrid.Comic.eBook-BitBook",
            "Overlord Vol. 2 (Graphic Novel) (epub)",
        ],
    )
    def test_manga_names_are_an_incompatible_comic(self, name):
        match = _classify(name, OVERLORD2_RANK)
        assert (match.medium, match.compatible) == ("comic", False)

    def test_a_comic_is_compatible_with_a_comic_request(self):
        manga2 = _ranking("Overlord (Manga), Vol. 2", "Overlord (Manga)", 2)
        match = _classify("Yen.Press-Overlord.Vol.02.Manga.2022.Hybrid.Comic.eBook", manga2)
        assert (match.medium, match.compatible, match.volume) == ("comic", True, "match")

    def test_a_cbz_declared_clean_title_is_a_comic(self):
        match = _classify("Overlord v02", OVERLORD2_RANK, formats=["cbz"])
        assert (match.medium, match.compatible) == ("comic", False)

    def test_an_audiobook_category_clean_title_is_audio(self):
        match = _classify("Overlord v02", OVERLORD2_RANK, content_type="audiobook")
        assert (match.medium, match.compatible) == ("audio", False)

    def test_a_declared_audio_format_is_audio(self):
        for fmt in ("m4b", "MP3", "m4a", "flac", "aac"):
            assert _classify("Overlord v02", OVERLORD2_RANK, formats=[fmt]).medium == "audio"

    def test_a_declared_format_beats_name_words(self):
        match = _classify("Overlord Manga Vol. 2", OVERLORD2_RANK, formats=["m4b"])
        assert match.medium == "audio"

    @pytest.mark.parametrize(
        "name",
        [
            "Overlord S02E05 1080p WEB-DL x264",
            "Overlord.2160p.BDRip.HEVC",
            "Overlord 720p h.264 mkv",
            "Overlord.480p.WEBRip.avi",
            "Overlord x265 mp4",
        ],
    )
    def test_technical_video_markers_are_video(self, name):
        match = _classify(name, OVERLORD2_RANK)
        assert (match.medium, match.compatible) == ("video", False)

    def test_audio_words_in_the_name(self):
        for name in ("Overlord Vol 2 [ENG / M4B]", "Overlord v02 MP3", "Overlord Audiobook v02"):
            assert _classify(name, OVERLORD2_RANK).medium == "audio"

    def test_a_medium_word_that_is_the_books_own_title_word_does_not_count(self):
        manga_guide = _ranking("The Manga Guide to Physics", None, None)
        assert _classify("The Manga Guide to Physics (epub)", manga_guide).medium == "unknown"
        mp3_book = _ranking("MP3 Players For Dummies", None, None)
        assert _classify("MP3 Players For Dummies", mp3_book).medium == "unknown"

    def test_ebook_evidence(self):
        assert _classify("Overlord v02", OVERLORD2_RANK, formats=["epub"]).medium == "ebook"
        assert _classify("Overlord v02", OVERLORD2_RANK, content_type="book").medium == "ebook"
        assert _classify("Overlord v02", OVERLORD2_RANK, content_type="ebook").medium == "ebook"
        assert _classify("Overlord v02", OVERLORD2_RANK).medium == "unknown"
        assert _classify("Overlord v02", OVERLORD2_RANK).compatible is True


class TestRankingAuthorAndFanMarker:
    def test_an_author_conflict_downgrades_a_match(self):
        name = "Overlord v02 (epub)"
        assert _classify(name, OVERLORD2_RANK, author="Kugane Maruyama").volume == "match"
        assert _classify(name, OVERLORD2_RANK, author="Someone Else").volume == "unknown"

    def test_an_author_conflict_leaves_another_volume_alone(self):
        match = _classify("Overlord v03 (epub)", OVERLORD2_RANK, author="Someone Else")
        assert (match.volume, match.other_volume) == ("other", 3)

    def test_a_missing_author_is_neutral(self):
        for author in (None, "", "   ", 42):
            assert _classify("Overlord v02", OVERLORD2_RANK, author=author).volume == "match"

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD Vol 5 Baka-Tsuki",
            "High School DxD Vol 5 (Baka Tsuki)",
            "High School DxD Vol 5 [Fan TL]",
            "High School DxD Vol 5 fan translation",
            "High School DxD Vol 5 fan-translated",
            "High School DxD Vol 5 Scanlation",
        ],
    )
    def test_the_fan_marker(self, name):
        assert _classify(name, DXD5_RANK).fan_marker is True

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp by Ichiei Ishibumi [ENG / EPUB]",
            "High School DxD Vol 5 Retail",
            "High School DxD Vol 5 fantastic translation",
        ],
    )
    def test_no_fan_marker(self, name):
        assert _classify(name, DXD5_RANK).fan_marker is False


class TestRankingJunk:
    @pytest.mark.parametrize("name", [None, "", "   ", 5, object(), ["Overlord v02"]])
    def test_a_junk_name_is_fully_unknown(self, name):
        assert _classify(name, OVERLORD2_RANK, formats=["m4b"]) == ReleaseMatch(
            "unknown", None, "unknown", compatible=True, fan_marker=False
        )

    @pytest.mark.parametrize("formats", [None, 5, "epub", [None, 3, object()]])
    def test_junk_formats_are_ignored(self, formats):
        match = _classify("Overlord v02", OVERLORD2_RANK, formats=formats)
        assert (match.volume, match.medium) == ("match", "unknown")

    @pytest.mark.parametrize("identity", [None, "Overlord", 5])
    def test_no_usable_identity_decides_no_volume(self, identity):
        match = _classify("Overlord v02 (epub)", identity, content_type=7)
        assert match.volume == "unknown"
        assert match.medium == "unknown"

    def test_the_payload_is_versioned(self):
        assert _classify("Overlord v03", OVERLORD2_RANK).to_payload() == {
            "v": 1,
            "volume": "other",
            "other_volume": 3,
            "medium": "unknown",
            "compatible": True,
            "fan_marker": False,
        }

    def test_classify_release_never_raises_on_a_hostile_identity(self):
        hostile = RankingIdentity(series_key="(", position=2, title_tokens=("(",))
        assert _classify("Overlord ( v02", hostile).volume in {"match", "unknown"}


class TestRankingReviewFocus:
    """Inputs the spec implies but its test list does not name (plan Review Focus)."""

    @pytest.mark.parametrize("author", ["Unknown", "unknown", "Various", "Anonymous", "N/A"])
    def test_a_placeholder_author_is_missing_not_a_conflict(self, author):
        # IRC's parser sets "Unknown" when a line has no "Author - Title" split.
        assert _classify("Overlord v02 (epub)", OVERLORD2_RANK, author=author).volume == "match"

    @pytest.mark.parametrize(
        "author", ["Maruyama Kugane", "Kugane Maruyama, so-bin", "MARUYAMA, Kugane", "K. Maruyama"]
    )
    def test_name_order_and_extra_contributors_are_not_a_conflict(self, author):
        assert _classify("Overlord v02 (epub)", OVERLORD2_RANK, author=author).volume == "match"

    def test_initials_alone_do_not_count_as_a_shared_author(self):
        corey = _ranking("Leviathan Wakes", "The Expanse", 1, ("James S. A. Corey",))
        match = _classify("Leviathan Wakes (epub)", corey, author="S. A. Smith")
        assert match.volume == "unknown"

    def test_escaped_names_and_version_tags_make_no_volume_or_bundle(self):
        assert _volume("Overlord Vol. 2 (v1.1) (epub)", OVERLORD2_RANK) == ("match", None)
        assert _volume("Overlord Vol. 2 [v2.0] Kugane &amp; so-bin", OVERLORD2_RANK) == (
            "match",
            None,
        )

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD Vol. 5.5 (epub)",
            "High School DxD Vol. 5a (epub)",
            "High School DxD v05.5 (epub)",
            "High School DxD #5.5",
        ],
    )
    def test_a_fractional_or_lettered_volume_is_unknown(self, name):
        assert _volume(name, DXD5_RANK) == ("unknown", None)

    def test_a_year_after_the_volume_is_not_a_fraction(self):
        name = "Seven.Seas-High.School.DxD.Vol.05.2016.Retail.eBook-BitBook"
        assert _volume(name, DXD5_RANK) == ("match", None)


class TestRankingReviewFindings:
    """Cases from the Codex review of the plan (2026-10-08)."""

    @pytest.mark.parametrize("name", ["Overlord Vol 2.125 (epub)", "Overlord Vol 3.141 (epub)"])
    def test_any_decimal_suffix_is_not_a_whole_volume(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("unknown", None)

    @pytest.mark.parametrize("name", ["Overlord Vol.02.2016 (epub)", "Overlord Vol 2 2016 (epub)"])
    def test_a_year_after_the_volume_keeps_it_whole(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    @pytest.mark.parametrize("name", ["Overlord Vol 2/3 (epub)", "Overlord Vol 2 / 3 (epub)"])
    def test_a_slash_between_volume_numbers_is_a_collection(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("unknown", None)

    def test_a_slash_between_contributors_is_harmless(self):
        name = "Overlord Vol. 2 Kugane Maruyama / so-bin (epub)"
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    @pytest.mark.parametrize(
        "name",
        [
            "Leviathan Wakes & Calibans War EPUB",
            "Leviathan Wakes and Calibans War EPUB",
            "Leviathan Wakes / Calibans War EPUB",
            "Leviathan Wakes + Calibans War EPUB",
            "Leviathan Wakes &amp; Calibans War EPUB",
            "Corey & Abraham - Leviathan Wakes & Calibans War (epub)",
        ],
    )
    def test_a_conjunction_joining_another_title_is_unknown(self, name):
        assert _volume(name, LEVIATHAN_RANK) == ("unknown", None)

    @pytest.mark.parametrize(
        "name",
        [
            "Leviathan Wakes James S. A. Corey & Daniel Abraham EPUB",
            "Leviathan Wakes & James S. A. Corey (epub)",
            "Daniel Abraham & James S. A. Corey - Leviathan Wakes (epub)",
            "Leviathan Wakes & EPUB",
        ],
    )
    def test_a_conjunction_before_a_contributor_or_nothing_is_harmless(self, name):
        assert _volume(name, LEVIATHAN_RANK) == ("match", None)

    def test_volume_zero_is_never_another_volume(self):
        match = _classify("Overlord Vol 0 [MP3]", OVERLORD2_RANK)
        assert (match.volume, match.other_volume, match.medium) == ("unknown", None, "audio")
        assert match.to_payload()["other_volume"] is None

    def test_a_requested_volume_zero_still_matches(self):
        prequel = _ranking("Overlord (Light Novel), Vol. 0: Prologue", OL, 0)
        assert _volume("Overlord Vol. 0 Prologue (epub)", prequel) == ("match", None)

    @pytest.mark.parametrize(
        "name",
        [
            "Overlord Vol. 2 ISBN 978-1-9753-0123-4 (epub)",
            "Overlord v02 (2016-05-24) (epub)",
            "Overlord Vol. 2 [1-2 MB] (epub)",
        ],
    )
    def test_isbns_dates_and_sizes_are_not_volume_ranges(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    def test_a_series_number_range_is_still_a_collection(self):
        assert _volume("The Expanse 1-3 Leviathan Wakes (epub)", LEVIATHAN_RANK) == (
            "unknown",
            None,
        )

    def test_a_shared_given_name_is_not_the_same_author(self):
        match = _classify("Leviathan Wakes (epub)", LEVIATHAN_RANK, author="James Patterson")
        assert match.volume == "unknown"

    @pytest.mark.parametrize(
        "author", ["Corey, James S A", "James S. A. Corey", "J. S. A. Corey & Daniel Abraham"]
    )
    def test_the_same_surname_is_the_same_author(self, author):
        assert _classify("Leviathan Wakes (epub)", LEVIATHAN_RANK, author=author).volume == "match"

    def test_reordered_names_are_the_same_author(self):
        reordered = _classify("Overlord v02 (epub)", OVERLORD2_RANK, author="Maruyama Kugane")
        assert reordered.volume == "match"

    def test_an_author_field_holding_the_series_name_is_not_an_author(self):
        assert _classify("Overlord - Volume 2.epub", OVERLORD2_RANK, author="Overlord").volume == (
            "match"
        )

    @pytest.mark.parametrize("sep", [" ", ".", "-", "_", ""])
    def test_fan_tl_and_baka_tsuki_with_any_separator(self, sep):
        assert _classify(f"DxD Vol 5 [Fan{sep}TL]", DXD5_RANK).fan_marker is True
        assert _classify(f"DxD Vol 5 Baka{sep}Tsuki", DXD5_RANK).fan_marker is True

    @pytest.mark.parametrize("sep", [" ", ".", "-", "_"])
    def test_fan_translation_with_any_separator(self, sep):
        assert _classify(f"DxD Vol 5 fan{sep}translation", DXD5_RANK).fan_marker is True
        assert _classify(f"DxD Vol 5 fan{sep}translated", DXD5_RANK).fan_marker is True

    @pytest.mark.parametrize(
        "identity",
        [
            RankingIdentity(series_key=None, position="2", title_tokens=None, authors=None),  # type: ignore[arg-type]
            RankingIdentity(
                series_key="Overlord",
                position=True,  # type: ignore[arg-type]
                title_tokens=("overlord", None, 3),  # type: ignore[arg-type]
                authors=(None, 5, "Kugane Maruyama"),  # type: ignore[arg-type]
                title_names_volume=None,  # type: ignore[arg-type]
                book_is_comic="yes",  # type: ignore[arg-type]
            ),
            RankingIdentity(series_key="Overlord", position=-1, title_tokens="overlord"),  # type: ignore[arg-type]
        ],
    )
    def test_a_malformed_identity_never_raises(self, identity):
        for author in (None, "Someone Else", "Kugane Maruyama"):
            match = _classify(
                "Yen.Press-Overlord.Vol.02.Manga.2022.Hybrid.Comic.eBook-BitBook",
                identity,
                author=author,
            )
            assert match.volume == "unknown"
            assert (match.medium, match.compatible) == ("comic", False)

    def test_malformed_tokens_and_authors_are_dropped_not_fatal(self):
        identity = RankingIdentity(
            series_key="The Expanse",
            position=1,
            title_tokens=("leviathan", None, "wakes", 7),  # type: ignore[arg-type]
            title_names_volume=False,
            authors=("James S. A. Corey", None),  # type: ignore[arg-type]
        )
        match = _classify("Leviathan Wakes (epub)", identity, author="Corey, James")
        assert match.volume == "match"
