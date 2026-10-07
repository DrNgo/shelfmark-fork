import pytest

from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata


def _book_language_config_get(
    global_languages: list[str],
    user_languages: list[str] | None = None,
):
    """Stand in for config.get(), answering BOOK_LANGUAGE per user."""

    def _get(key: str, default: object = None, user_id: int | None = None) -> object:
        if key != "BOOK_LANGUAGE":
            return default
        if user_id is not None and user_languages is not None:
            return user_languages
        return global_languages

    return _get


class TestReleaseSearchPlan:
    def test_uses_default_languages_when_none(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(sp.config, "get", _book_language_config_get(["en", "hu"]))

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
            isbn_13="9780765311788",
        )

        plan = build_release_search_plan(book, languages=None)

        assert plan.languages == ["en", "hu"]
        assert plan.isbn_candidates == ["9780765311788"]
        assert [v.query for v in plan.title_variants] == [
            "The Final Empire Brandon Sanderson",
            "A végső birodalom Brandon Sanderson",
        ]

        # Title-only variants are used by some sources (e.g. Prowlarr).
        assert [v.title for v in plan.title_variants] == [
            "The Final Empire",
            "A végső birodalom",
        ]

        assert [(v.title, v.languages) for v in plan.grouped_title_variants] == [
            ("The Final Empire", ["en"]),
            ("A végső birodalom", ["hu"]),
        ]

    def test_all_language_disables_grouping(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(sp.config, "get", _book_language_config_get(["en"]))

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="The Lightning Thief",
            authors=["Rick Riordan"],
            titles_by_language={"hu": "A villámtolvaj"},
        )

        plan = build_release_search_plan(book, languages=["all"])

        assert plan.languages is None
        assert [v.query for v in plan.title_variants] == [
            "The Lightning Thief Rick Riordan",
        ]
        assert [v.title for v in plan.title_variants] == [
            "The Lightning Thief",
        ]
        assert [(v.title, v.languages) for v in plan.grouped_title_variants] == [
            ("The Lightning Thief", None),
        ]

    def test_user_default_languages_beat_the_global_default(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(
            sp.config,
            "get",
            _book_language_config_get(["en"], user_languages=["de", "en"]),
        )

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="The Final Empire",
            authors=["Brandon Sanderson"],
        )

        assert build_release_search_plan(book).languages == ["en"]
        assert build_release_search_plan(book, user_id=7).languages == ["de", "en"]

    def test_explicit_languages_beat_the_user_default(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(
            sp.config,
            "get",
            _book_language_config_get(["en"], user_languages=["de", "en"]),
        )

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="The Final Empire",
            authors=["Brandon Sanderson"],
        )

        plan = build_release_search_plan(book, languages=["fr"], user_id=7)

        assert plan.languages == ["fr"]


class TestSearchAuthorNormalization:
    """A credit list must not reach the query, whichever field carries it.

    Anna's Archive answers "Blindness Jose Saramago, Giovanni Pontiero, ..." with
    nothing, so a book whose author string lists translators alongside the author
    finds no releases at all.
    """

    MULTI = "Jose Saramago, Giovanni Pontiero, Zohreh Eftekhari"

    def test_search_author_is_trimmed_to_the_first_name(self):
        book = BookMetadata(
            provider="manual",
            provider_id="1",
            title="Blindness",
            authors=[self.MULTI],
            search_author=self.MULTI,
        )

        assert build_release_search_plan(book).primary_query == "Blindness Jose Saramago"

    def test_search_author_matches_the_authors_list(self):
        """Same credit list, two fields, one query."""
        via_authors = BookMetadata(
            provider="manual", provider_id="1", title="Blindness", authors=[self.MULTI]
        )
        via_search_author = BookMetadata(
            provider="manual",
            provider_id="1",
            title="Blindness",
            authors=[self.MULTI],
            search_author=self.MULTI,
        )

        assert (
            build_release_search_plan(via_search_author).primary_query
            == build_release_search_plan(via_authors).primary_query
        )

    def test_single_author_is_untouched(self):
        book = BookMetadata(
            provider="manual",
            provider_id="1",
            title="Elantris",
            authors=["Brandon Sanderson"],
            search_author="Brandon Sanderson",
        )

        assert build_release_search_plan(book).primary_query == "Elantris Brandon Sanderson"


DXD5_TITLE = "High School DxD (Light Novel), Vol. 5: Hellcat of the Underworld Training Camp"


def _dxd5(**overrides) -> BookMetadata:
    """DxD vol 5 as Hardcover's get_book returns it: no subtitle, so no search_title."""
    fields: dict[str, object] = {
        "provider": "hardcover",
        "provider_id": "2575261",
        "title": DXD5_TITLE,
        "authors": ["Ichiei Ishibumi"],
        "search_author": "Ichiei Ishibumi",
        "series_name": "High School DxD (Light Novel)",
        "series_position": 5,
        "isbn_13": "9780316559294",
    }
    fields.update(overrides)
    return BookMetadata(**fields)


DXD5_LADDER = [
    "High School DxD Vol. 5",
    "High School DxD v05",
    "Hellcat of the Underworld Training Camp",
    "High School DxD Volume 05",
    "High School DxD Vol. 5 Hellcat of the Underworld Training Camp",
]


class TestFallbackVariants:
    def test_order_is_mandatory_then_localized_then_fallbacks(self):
        book = _dxd5(titles_by_language={"de": "Highschool DxD 5"})

        plan = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        assert [(v.title, v.fallback) for v in plan.title_variants] == [
            (DXD5_TITLE, False),
            ("Highschool DxD 5", False),
            *[(query, True) for query in DXD5_LADDER],
        ]
        assert plan.identity is not None
        assert (plan.identity.series_key, plan.identity.position) == ("High School DxD", 5)

    def test_fallbacks_carry_the_search_author_like_every_variant(self):
        plan = build_release_search_plan(_dxd5(), languages=["en"], content_type="ebook")

        assert {v.author for v in plan.title_variants} == {"Ichiei Ishibumi"}
        assert all(v.languages is None for v in plan.title_variants if v.fallback)

    def test_a_duplicate_title_keeps_its_mandatory_status(self):
        book = _dxd5(titles_by_language={"de": "high school dxd  vol. 5"})

        plan = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        titles = [(v.title, v.fallback) for v in plan.title_variants]
        assert ("high school dxd  vol. 5", False) in titles
        assert ("High School DxD Vol. 5", True) not in titles
        assert [t for t, fallback in titles if fallback] == DXD5_LADDER[1:]

    def test_a_title_that_differs_only_in_punctuation_is_a_different_request(self):
        book = _dxd5(titles_by_language={"de": "High School DxD, Vol. 5"})

        plan = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        assert [v.title for v in plan.title_variants if v.fallback] == DXD5_LADDER

    @pytest.mark.parametrize("content_type", ["audiobook", None, "", "comic"])
    def test_only_ebook_searches_get_fallbacks(self, content_type):
        plan = build_release_search_plan(_dxd5(), languages=["en"], content_type=content_type)

        assert [v.title for v in plan.title_variants] == [DXD5_TITLE]
        assert plan.identity is None

    def test_content_type_is_matched_case_insensitively(self):
        plan = build_release_search_plan(_dxd5(), languages=["en"], content_type=" EBook ")

        assert [v.title for v in plan.title_variants if v.fallback] == DXD5_LADDER

    def test_primary_query_and_grouped_variants_are_unchanged(self):
        book = _dxd5(titles_by_language={"de": "Highschool DxD 5"})

        before = build_release_search_plan(book, languages=["en", "de"])
        after = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        assert after.primary_query == before.primary_query == f"{DXD5_TITLE} Ichiei Ishibumi"
        assert after.grouped_title_variants == before.grouped_title_variants
        assert after.title_variants[: len(before.title_variants)] == before.title_variants
        assert after.isbn_candidates == before.isbn_candidates

    def test_a_manual_query_keeps_its_trimming_and_gets_no_fallbacks(self):
        plan = build_release_search_plan(
            _dxd5(), languages=["en"], manual_query="  dxd v05  ", content_type="ebook"
        )

        assert [(v.title, v.fallback) for v in plan.title_variants] == [("dxd v05", False)]
        assert plan.identity is None

        long_plan = build_release_search_plan(
            _dxd5(), languages=["en"], manual_query="x" * 300, content_type="ebook"
        )
        assert [v.title for v in long_plan.title_variants] == ["x" * 256]

    def test_a_manual_provider_book_gets_no_fallbacks(self):
        book = BookMetadata(
            provider="manual",
            provider_id="abc",
            title="Overlord, Vol. 2",
            search_title="Overlord, Vol. 2",
            authors=["Kugane Maruyama"],
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="ebook")

        assert [v.title for v in plan.title_variants] == ["Overlord, Vol. 2"]
        assert plan.identity is None

    def test_an_empty_title_still_falls_back_to_isbn_only(self):
        plan = build_release_search_plan(_dxd5(title=""), languages=["en"], content_type="ebook")

        assert [(v.title, v.fallback) for v in plan.title_variants] == [("9780316559294", False)]
        assert plan.identity is None


class TestFallbacksPerProvider:
    def test_openlibrary_without_series_fields_parses_the_title(self):
        book = BookMetadata(
            provider="openlibrary",
            provider_id="OL1W",
            title="Overlord, Vol. 2: The Dark Warrior",
            authors=["Kugane Maruyama"],
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="ebook")

        assert [v.title for v in plan.title_variants if v.fallback] == [
            "Overlord Vol. 2",
            "Overlord v02",
            "The Dark Warrior",
            "Overlord Volume 02",
            # Today's query keeps its comma and colon, so this is a different request.
            "Overlord Vol. 2 The Dark Warrior",
        ]

    def test_google_books_standalone_gets_none(self):
        book = BookMetadata(
            provider="googlebooks", provider_id="g1", title="Dune", authors=["Frank Herbert"]
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="ebook")

        assert [v.title for v in plan.title_variants] == ["Dune"]
        assert plan.identity is None

    def test_moly_display_only_series_is_not_used(self):
        from shelfmark.metadata_providers import DisplayField

        book = BookMetadata(
            provider="moly",
            provider_id="m1",
            title="A Sötét Harcos",
            authors=["Kugane Maruyama"],
            display_fields=[DisplayField(label="Series", value="Overlord", icon="editions")],
        )

        plan = build_release_search_plan(book, languages=["hu"], content_type="ebook")

        assert [v.title for v in plan.title_variants] == ["A Sötét Harcos"]

    def test_audible_audiobook_gets_none(self):
        book = BookMetadata(
            provider="audible",
            provider_id="B0X",
            title="Overlord, Vol. 2",
            authors=["Kugane Maruyama"],
            series_name="Overlord",
            series_position=2,
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="audiobook")

        assert [v.title for v in plan.title_variants] == ["Overlord, Vol. 2"]


class TestFallbacksStayWithTheirSources:
    """Only Prowlarr and Newznab read ``fallback``; no other source may see a ladder title.

    AudiobookBay takes ``title_variants[0]``, IRC takes ``primary_query`` and direct
    download takes ``grouped_title_variants``: each must still be a mandatory title.
    """

    def _plan(self):
        book = _dxd5(titles_by_language={"de": "Highschool DxD 5"})
        plan = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")
        fallbacks = {v.title for v in plan.title_variants if v.fallback}
        assert fallbacks == set(DXD5_LADDER)
        return plan, fallbacks

    def test_every_fallback_comes_after_every_mandatory_variant(self):
        plan, _ = self._plan()

        flags = [v.fallback for v in plan.title_variants]
        first_fallback = flags.index(True)
        assert not any(flags[:first_fallback])
        assert all(flags[first_fallback:])

    def test_other_sources_never_see_a_fallback_title(self):
        plan, fallbacks = self._plan()

        # AudiobookBay
        assert not plan.title_variants[0].fallback
        assert plan.title_variants[0].title not in fallbacks
        # IRC
        assert plan.primary_query == f"{DXD5_TITLE} Ichiei Ishibumi"
        assert plan.primary_query not in {v.query for v in plan.title_variants if v.fallback}
        # Direct download
        assert not any(v.fallback for v in plan.grouped_title_variants)
        assert not {v.title for v in plan.grouped_title_variants} & fallbacks
