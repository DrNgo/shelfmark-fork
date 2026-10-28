"""Which searches get a fallback ladder, and the budget a fallback request checks."""

import time

import pytest

from shelfmark.core import search_deadline
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata, DisplayField
from shelfmark.release_sources.fallback_ladder import plan_fallbacks, remaining_seconds

DXD5_TITLE = "High School DxD (Light Novel), Vol. 5: Hellcat of the Underworld Training Camp"

DXD5_LADDER = [
    "High School DxD Vol. 5",
    "High School DxD v05",
    "Hellcat of the Underworld Training Camp",
    "High School DxD Volume 05",
    "High School DxD Vol. 5 Hellcat of the Underworld Training Camp",
]


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


def _fallbacks(book, *, languages=("en",), content_type="ebook", **plan_kwargs):
    plan = build_release_search_plan(book, languages=list(languages), **plan_kwargs)
    return plan_fallbacks(book, plan, content_type)


class TestPlanFallbacks:
    def test_the_ladder_and_identity_for_a_light_novel_volume(self):
        fallbacks = _fallbacks(
            _dxd5(titles_by_language={"de": "Highschool DxD 5"}), languages=["en", "de"]
        )

        assert fallbacks.queries == DXD5_LADDER
        assert fallbacks.identity is not None
        assert (fallbacks.identity.series_key, fallbacks.identity.position) == (
            "High School DxD",
            5,
        )

    def test_a_query_the_plan_already_sends_is_not_repeated(self):
        book = _dxd5(titles_by_language={"de": "high school dxd  vol. 5"})

        assert _fallbacks(book, languages=["en", "de"]).queries == DXD5_LADDER[1:]

    def test_a_title_that_differs_only_in_punctuation_is_a_different_request(self):
        book = _dxd5(titles_by_language={"de": "High School DxD, Vol. 5"})

        assert _fallbacks(book, languages=["en", "de"]).queries == DXD5_LADDER

    @pytest.mark.parametrize("content_type", ["audiobook", None, "", "comic"])
    def test_only_ebook_searches_get_fallbacks(self, content_type):
        fallbacks = _fallbacks(_dxd5(), content_type=content_type)

        assert fallbacks.queries == []
        assert fallbacks.identity is None

    def test_content_type_is_matched_case_insensitively(self):
        assert _fallbacks(_dxd5(), content_type=" EBook ").queries == DXD5_LADDER

    def test_a_manual_query_gets_no_fallbacks(self):
        fallbacks = _fallbacks(_dxd5(), manual_query="  dxd v05  ")

        assert fallbacks.queries == []
        assert fallbacks.identity is None

    @pytest.mark.parametrize("provider", ["manual", "shelfmark"])
    def test_a_manual_or_retry_refresh_book_gets_no_fallbacks(self, provider):
        book = BookMetadata(
            provider=provider,
            provider_id="abc",
            title="Overlord, Vol. 2",
            search_title="Overlord, Vol. 2",
            authors=["Kugane Maruyama"],
        )

        assert _fallbacks(book).queries == []

    def test_an_isbn_only_search_gets_no_fallbacks(self):
        fallbacks = _fallbacks(_dxd5(title=""))

        assert fallbacks.queries == []
        assert fallbacks.identity is None


class TestPlanFallbacksPerProvider:
    def test_openlibrary_without_series_fields_parses_the_title(self):
        book = BookMetadata(
            provider="openlibrary",
            provider_id="OL1W",
            title="Overlord, Vol. 2: The Dark Warrior",
            authors=["Kugane Maruyama"],
        )

        assert _fallbacks(book).queries == [
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

        assert _fallbacks(book).queries == []

    def test_moly_display_only_series_is_not_used(self):
        book = BookMetadata(
            provider="moly",
            provider_id="m1",
            title="A Sötét Harcos",
            authors=["Kugane Maruyama"],
            display_fields=[DisplayField(label="Series", value="Overlord", icon="editions")],
        )

        assert _fallbacks(book, languages=["hu"]).queries == []


class TestRemainingSeconds:
    def test_remaining_is_the_source_deadline_outside_a_search(self):
        assert 49 < remaining_seconds(time.monotonic() + 50) <= 50

    def test_remaining_is_the_sooner_of_the_source_and_endpoint_budgets(self):
        with search_deadline.search_deadline(30):
            assert remaining_seconds(time.monotonic() + 500) <= 30
            assert remaining_seconds(time.monotonic() + 5) <= 5

    def test_an_expired_endpoint_budget_leaves_nothing(self):
        with search_deadline.search_deadline(3600) as deadline:
            deadline.event.set()
            assert remaining_seconds(time.monotonic() + 500) == 0.0
