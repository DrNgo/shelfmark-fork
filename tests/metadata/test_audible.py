"""Tests for the Audible metadata provider.

Audible is the only provider that describes an audiobook as an audiobook —
narrator, runtime, and the actual recording's cover — and the only one that
yields an ASIN, which the "already in library" badge can match exactly.
"""

import pytest
import requests

from shelfmark.metadata_providers import MetadataSearchOptions, SearchType, SortOrder
from shelfmark.metadata_providers.audible import AudibleProvider

PRODUCT = {
    "asin": "B0CTMZBM36",
    "title": "The Housemaid Is Watching",
    "subtitle": "A Novel",
    "authors": [{"asin": "B00ELQLN2I", "name": "Freida McFadden"}],
    "narrators": [{"name": "Lauryn Allman"}, {"name": "Ina Marie Smith"}],
    "language": "english",
    "publisher_name": "Hachette UK - Bookouture",
    "release_date": "2024-06-11",
    "runtime_length_min": 545,
    "format_type": "unabridged",
    "merchandising_summary": "<p>Short blurb.</p>",
    "publisher_summary": "<p>The <b>full</b> summary.</p>",
    "product_images": {
        "500": "https://example.com/cover-500.jpg",
        "1024": "https://example.com/cover-1024.jpg",
    },
    "series": [
        {"asin": "B006K1P698", "sequence": "3", "title": "The Housemaid"},
        {"asin": "B0DMXTJ8WH", "sequence": "", "title": "Thrillers"},
    ],
    "rating": {"overall_distribution": {"display_average_rating": "4.8", "num_ratings": 75452}},
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    """Records every GET and replays canned payloads keyed by URL fragment."""

    def __init__(self, payloads=None, error=None):
        self.payloads = payloads or {}
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **_kwargs):
        self.calls.append((url, params or {}))
        if self.error:
            raise self.error
        for fragment, payload in self.payloads.items():
            if fragment in url:
                return FakeResponse(payload)
        return FakeResponse({})

    @property
    def last_params(self) -> dict:
        return self.calls[-1][1]


@pytest.fixture(autouse=True)
def _clear_metadata_cache():
    """Every test here reuses one ASIN, and get_book results are cached."""
    from shelfmark.core.cache import get_metadata_cache

    get_metadata_cache().clear()


@pytest.fixture
def provider():
    prov = AudibleProvider()
    prov.session = FakeSession({"catalog/products": {"products": [PRODUCT], "total_results": 19}})
    return prov


class TestParseProduct:
    """Audible's payload is the only one that knows this is a recording."""

    def test_maps_the_core_fields(self, provider):
        book = provider._parse_product(PRODUCT)

        assert book is not None
        assert book.provider == "audible"
        assert book.provider_id == "B0CTMZBM36"
        assert book.asin == "B0CTMZBM36"
        assert book.title == "The Housemaid Is Watching"
        assert book.subtitle == "A Novel"
        assert book.authors == ["Freida McFadden"]
        assert book.publisher == "Hachette UK - Bookouture"
        assert book.publish_year == 2024
        assert book.source_url == "https://www.audible.com/pd/B0CTMZBM36"

    def test_prefers_the_largest_cover(self, provider):
        assert provider._parse_product(PRODUCT).cover_url == "https://example.com/cover-1024.jpg"

    def test_marks_the_cover_square(self, provider):
        """Audiobook art is square; a portrait frame would letterbox every card."""
        assert provider._parse_product(PRODUCT).cover_aspect == "square"

    def test_normalizes_the_language_to_an_iso_code(self, provider):
        assert provider._parse_product(PRODUCT).language == "en"

    def test_keeps_an_unrecognized_language_verbatim(self, provider):
        book = provider._parse_product({**PRODUCT, "language": "Klingon"})

        assert book.language == "klingon"

    def test_strips_html_from_the_description(self, provider):
        assert provider._parse_product(PRODUCT).description == "The full summary."

    def test_falls_back_to_the_short_blurb(self, provider):
        book = provider._parse_product({**PRODUCT, "publisher_summary": ""})

        assert book.description == "Short blurb."

    def test_reads_the_numbered_series(self, provider):
        """The unnumbered entry is a shelf, not this book's series."""
        book = provider._parse_product(PRODUCT)

        assert book.series_id == "B006K1P698"
        assert book.series_name == "The Housemaid"
        assert book.series_position == 3.0

    def test_prefers_a_numbered_series_over_an_earlier_shelf(self, provider):
        """Live payloads list the broad collection first as often as not.

        Audible returned *The Well of Ascension* under "The Cosmere" with no
        position, ahead of "The Mistborn Saga" where it is book two.
        """
        book = provider._parse_product(
            {
                **PRODUCT,
                "series": [
                    {"asin": "B0DMXTJ8WH", "sequence": "", "title": "The Cosmere"},
                    {"asin": "B006K1P698", "sequence": "2", "title": "The Mistborn Saga"},
                ],
            }
        )

        assert book.series_name == "The Mistborn Saga"
        assert book.series_position == 2.0

    def test_falls_back_to_the_first_entry_when_none_are_numbered(self, provider):
        book = provider._parse_product(
            {
                **PRODUCT,
                "series": [
                    {"asin": "a", "sequence": "", "title": "First Shelf"},
                    {"asin": "b", "sequence": "", "title": "Second Shelf"},
                ],
            }
        )

        assert book.series_name == "First Shelf"
        assert book.series_position is None

    def test_tolerates_a_series_without_a_position(self, provider):
        book = provider._parse_product({**PRODUCT, "series": [{"asin": "x", "title": "Loose"}]})

        assert book.series_name == "Loose"
        assert book.series_position is None

    def test_surfaces_the_narrator_and_runtime(self, provider):
        """The two facts a print-book provider can never give you."""
        fields = {f.label: f.value for f in provider._parse_product(PRODUCT).display_fields}

        assert fields["Narrator"] == "Lauryn Allman, Ina Marie Smith"
        assert fields["Length"] == "9h 5m"

    def test_formats_a_runtime_under_an_hour(self, provider):
        book = provider._parse_product({**PRODUCT, "runtime_length_min": 47})
        fields = {f.label: f.value for f in book.display_fields}

        assert fields["Length"] == "47m"

    def test_omits_the_runtime_when_absent(self, provider):
        book = provider._parse_product({**PRODUCT, "runtime_length_min": 0})

        assert "Length" not in {f.label for f in book.display_fields}

    def test_surfaces_the_rating(self, provider):
        fields = {f.label: f.value for f in provider._parse_product(PRODUCT).display_fields}

        assert fields["Rating"] == "4.8 (75,452)"

    def test_flags_an_abridged_recording(self, provider):
        """Abridged is a different listening experience, not a different edition."""
        book = provider._parse_product({**PRODUCT, "format_type": "abridged"})
        fields = {f.label: f.value for f in book.display_fields}

        assert fields["Format"] == "Abridged"

    def test_does_not_flag_the_unabridged_default(self, provider):
        assert "Format" not in {f.label for f in provider._parse_product(PRODUCT).display_fields}

    def test_rejects_a_product_without_an_asin_or_title(self, provider):
        assert provider._parse_product({**PRODUCT, "asin": ""}) is None
        assert provider._parse_product({**PRODUCT, "title": ""}) is None

    def test_tolerates_a_malformed_payload(self, provider):
        assert provider._parse_product({"asin": "B0CTMZBM36", "title": 7}) is None
        assert provider._parse_product("nonsense") is None


class TestSearchParams:
    """Audible's catalog API has its own dialect; get it exactly right."""

    def test_sends_the_title_and_author_fields(self, provider):
        provider.search(
            MetadataSearchOptions(query="", fields={"title": "Housemaid", "author": "McFadden"})
        )

        assert provider.session.last_params["title"] == "Housemaid"
        assert provider.session.last_params["author"] == "McFadden"

    def test_sends_a_narrator_field(self, provider):
        """The search Audible can do that no other provider can."""
        provider.search(MetadataSearchOptions(query="", fields={"narrator": "Julia Whelan"}))

        assert provider.session.last_params["narrator"] == "Julia Whelan"

    def test_sends_a_general_query_as_keywords(self, provider):
        provider.search(MetadataSearchOptions(query="the housemaid"))

        assert provider.session.last_params["keywords"] == "the housemaid"

    def test_maps_a_title_search(self, provider):
        provider.search(MetadataSearchOptions(query="Housemaid", search_type=SearchType.TITLE))

        assert provider.session.last_params["title"] == "Housemaid"
        assert "keywords" not in provider.session.last_params

    def test_maps_an_author_search(self, provider):
        provider.search(MetadataSearchOptions(query="McFadden", search_type=SearchType.AUTHOR))

        assert provider.session.last_params["author"] == "McFadden"

    def test_converts_the_page_to_audibles_zero_based_index(self, provider):
        """Shelfmark counts pages from 1; Audible counts from 0."""
        provider.search(MetadataSearchOptions(query="x", page=3))

        assert provider.session.last_params["page"] == 2

    def test_never_sends_a_negative_page(self, provider):
        provider.search(MetadataSearchOptions(query="x", page=0))

        assert provider.session.last_params["page"] == 0

    def test_maps_every_supported_sort(self, provider):
        expected = {
            SortOrder.RELEVANCE: "Relevance",
            SortOrder.POPULARITY: "BestSellers",
            SortOrder.NEWEST: "-ReleaseDate",
            SortOrder.OLDEST: "ReleaseDate",
        }

        for sort, audible_value in expected.items():
            provider.search(MetadataSearchOptions(query="x", sort=sort))
            assert provider.session.last_params["products_sort_by"] == audible_value

    def test_caps_the_result_count_at_audibles_limit(self, provider):
        provider.search(MetadataSearchOptions(query="x", limit=500))

        assert provider.session.last_params["num_results"] == 50


class TestSearch:
    """Search must degrade to empty, never to an exception."""

    def test_returns_parsed_books(self, provider):
        books = provider.search(MetadataSearchOptions(query="housemaid"))

        assert [b.asin for b in books] == ["B0CTMZBM36"]

    def test_reports_pagination_from_total_results(self, provider):
        result = provider.search_paginated(MetadataSearchOptions(query="housemaid", limit=10))

        assert result.total_found == 19
        assert result.has_more is True

    def test_reports_the_last_page_as_final(self, provider):
        result = provider.search_paginated(
            MetadataSearchOptions(query="housemaid", limit=10, page=2)
        )

        assert result.has_more is False

    def test_a_network_failure_yields_no_results(self):
        prov = AudibleProvider()
        prov.session = FakeSession(error=requests.Timeout())

        assert prov.search(MetadataSearchOptions(query="housemaid")) == []

    def test_isbn_search_is_not_supported(self, provider):
        """Audible catalogs recordings by ASIN; it has no ISBN index."""
        assert provider.search_by_isbn("9781234567897") is None


class TestGetBook:
    """The detail view is where the extra audnexus round trip is affordable."""

    def test_fetches_a_single_product_by_asin(self):
        prov = AudibleProvider(enrich=False)
        prov.session = FakeSession({"catalog/products/": {"product": PRODUCT}})

        book = prov.get_book("B0CTMZBM36")

        assert book is not None
        assert book.asin == "B0CTMZBM36"
        assert "catalog/products/B0CTMZBM36" in prov.session.calls[0][0]

    def test_enriches_with_audnexus_genres_and_isbn(self):
        prov = AudibleProvider(enrich=True)
        prov.session = FakeSession(
            {
                "catalog/products/": {"product": PRODUCT},
                "audnex.us": {
                    "isbn": "9781234567897",
                    "genres": [
                        {"name": "Mystery", "type": "genre"},
                        {"name": "Domestic Thrillers", "type": "tag"},
                    ],
                },
            }
        )

        book = prov.get_book("B0CTMZBM36")

        assert book.isbn_13 == "9781234567897"
        assert book.genres == ["Mystery", "Domestic Thrillers"]

    def test_a_failing_audnexus_does_not_lose_the_book(self):
        """audnex.us is a free community service; it must never be load-bearing."""
        prov = AudibleProvider(enrich=True)
        prov.session = FakeSession({"catalog/products/": {"product": PRODUCT}})
        original_get = prov.session.get

        def get(url, **kwargs):
            if "audnex.us" in url:
                raise requests.ConnectionError
            return original_get(url, **kwargs)

        prov.session.get = get

        assert prov.get_book("B0CTMZBM36").title == "The Housemaid Is Watching"

    def test_does_not_serve_one_regions_answer_to_another(self):
        """An ASIN is only valid in the storefront it came from.

        The shared cache strips `self`, so a region-blind key would let a
        US lookup answer a UK one — for a *different* recording.
        """
        us = AudibleProvider(region="us", enrich=False)
        us.session = FakeSession({"catalog/products/": {"product": PRODUCT}})
        uk = AudibleProvider(region="uk", enrich=False)
        uk.session = FakeSession(
            {"catalog/products/": {"product": {**PRODUCT, "title": "The UK Recording"}}}
        )

        us.get_book("B0CTMZBM36")

        assert uk.get_book("B0CTMZBM36").title == "The UK Recording"

    def test_a_missing_product_yields_none(self):
        prov = AudibleProvider(enrich=False)
        prov.session = FakeSession({"catalog/products/": {}})

        assert prov.get_book("B0CTMZBM36") is None

    def test_rejects_a_malformed_asin_without_a_request(self):
        prov = AudibleProvider(enrich=False)
        prov.session = FakeSession({})

        assert prov.get_book("not-an-asin") is None
        assert prov.session.calls == []


class TestRegions:
    """Audible's catalogs are per-country, and so are the ASINs."""

    def test_defaults_to_the_us_catalog(self):
        assert AudibleProvider().base_url == "https://api.audible.com"

    def test_uses_the_regional_top_level_domain(self):
        assert AudibleProvider(region="uk").base_url == "https://api.audible.co.uk"
        assert AudibleProvider(region="de").base_url == "https://api.audible.de"

    def test_falls_back_to_the_us_catalog_for_an_unknown_region(self):
        assert AudibleProvider(region="atlantis").base_url == "https://api.audible.com"

    def test_links_to_the_matching_regional_storefront(self):
        prov = AudibleProvider(region="uk")

        assert prov._parse_product(PRODUCT).source_url == "https://www.audible.co.uk/pd/B0CTMZBM36"

    def test_passes_the_region_to_audnexus(self):
        prov = AudibleProvider(region="uk", enrich=True)
        prov.session = FakeSession({"catalog/products/": {"product": PRODUCT}, "audnex.us": {}})

        prov.get_book("B0CTMZBM36")

        audnexus_call = next(call for call in prov.session.calls if "audnex.us" in call[0])
        assert audnexus_call[1]["region"] == "uk"


class TestAvailability:
    def test_is_always_available(self):
        """No API key, so nothing can be misconfigured."""
        assert AudibleProvider().is_available() is True
