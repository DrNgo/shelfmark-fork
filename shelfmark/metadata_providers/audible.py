"""Audible metadata provider. No API key required.

Every other provider here catalogues *books*; Audible catalogues *recordings*.
That difference is the whole point — narrator, runtime, abridgement, and the
recording's own square cover art simply do not exist in Google Books or Open
Library, and an audiobook search that cannot show them is guessing.

Two upstream services are involved, and only the first is load-bearing:

* ``api.audible.{tld}/1.0/catalog/products`` — search and per-ASIN lookup. This
  is the endpoint Audiobookshelf, Readarr and Plex's agent all use. It is
  undocumented and unauthenticated, so treat every field as optional and every
  response as capable of changing shape.
* ``api.audnex.us`` — an aggregator that enriches a *known* ASIN with genres and
  an ISBN. It has no book search, so it can never replace the call above. It is
  a free community service, so enrichment is best-effort and only runs on the
  detail view, never once per search result.
"""

import re
from http import HTTPStatus
from typing import Any, ClassVar

import requests

from shelfmark.core.cache import cacheable
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    SelectField,
    SettingsField,
    register_settings,
)
from shelfmark.download.network import get_ssl_verify
from shelfmark.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataProvider,
    MetadataSearchOptions,
    SearchField,
    SearchResult,
    SearchType,
    SortOrder,
    TextSearchField,
    register_provider,
    register_provider_kwargs,
)

logger = setup_logger(__name__)

AUDNEXUS_BASE_URL = "https://api.audnex.us"

# Audible runs a separate catalog per storefront, and an ASIN is only valid
# within one of them. The key is the region code audnexus also understands.
REGION_TLDS: dict[str, str] = {
    "us": "com",
    "ca": "ca",
    "uk": "co.uk",
    "au": "com.au",
    "fr": "fr",
    "de": "de",
    "jp": "co.jp",
    "it": "it",
    "in": "in",
    "es": "es",
    "br": "com.br",
}
DEFAULT_REGION = "us"

# Verified against the live API: anything else is rejected outright, and
# "Rating" in particular is NOT a valid value despite ratings being returned.
SORT_MAPPING: dict[str, str] = {
    SortOrder.RELEVANCE: "Relevance",
    SortOrder.POPULARITY: "BestSellers",
    SortOrder.NEWEST: "-ReleaseDate",
    SortOrder.OLDEST: "ReleaseDate",
}

# Audible silently truncates larger requests.
MAX_RESULTS_PER_PAGE = 50

RESPONSE_GROUPS = (
    "contributors,product_desc,product_extended_attrs,product_attrs,media,series,rating"
)

_HTML_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_ASIN_SHAPE = re.compile(r"^[A-Z0-9]{10}$")
MINUTES_PER_HOUR = 60

# Audible reports languages as English words; the rest of Shelfmark passes ISO
# 639-1 codes to release sources. Anything unmapped falls through lowercased.
LANGUAGE_CODES: dict[str, str] = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "japanese": "ja",
    "russian": "ru",
    "polish": "pl",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "chinese": "zh",
    "korean": "ko",
    "hindi": "hi",
    "arabic": "ar",
    "turkish": "tr",
    "czech": "cs",
    "hungarian": "hu",
    "greek": "el",
    "hebrew": "he",
    "catalan": "ca",
    "romanian": "ro",
    "ukrainian": "uk",
}


def _strip_html(value: object) -> str | None:
    """Flatten Audible's HTML summaries into plain text."""
    if not isinstance(value, str) or not value.strip():
        return None

    text = _HTML_TAGS.sub(" ", value)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def _format_runtime(minutes: object) -> str | None:
    """Render a runtime the way a listener thinks about it: "9h 5m"."""
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return None

    hours, remainder = divmod(minutes, MINUTES_PER_HOUR)
    if not hours:
        return f"{remainder}m"
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def _names(entries: object) -> list[str]:
    """Pull the ``name`` out of Audible's contributor lists, skipping junk."""
    if not isinstance(entries, list):
        return []

    names = []
    for entry in entries:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


@register_provider("audible")
class AudibleProvider(MetadataProvider):
    """Audible metadata provider backed by the public catalog API."""

    name = "audible"
    display_name = "Audible"
    requires_auth = False
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (
        SortOrder.RELEVANCE,
        SortOrder.POPULARITY,
        SortOrder.NEWEST,
        SortOrder.OLDEST,
    )
    search_fields: ClassVar[tuple[SearchField, ...]] = (
        TextSearchField(
            key="title",
            label="Title",
            description="Search by audiobook title",
        ),
        TextSearchField(
            key="author",
            label="Author",
            description="Search by author name",
        ),
        TextSearchField(
            key="narrator",
            label="Narrator",
            description="Search by narrator — Audible is the only provider that can",
        ),
    )

    def __init__(self, region: str = DEFAULT_REGION, *, enrich: bool = True) -> None:
        """Initialize the provider for one Audible storefront."""
        self.region = region if region in REGION_TLDS else DEFAULT_REGION
        self.enrich = enrich
        self.session = requests.Session()

    @property
    def tld(self) -> str:
        """The top-level domain for this region's storefront."""
        return REGION_TLDS[self.region]

    @property
    def base_url(self) -> str:
        """Base URL of the regional catalog API."""
        return f"https://api.audible.{self.tld}"

    def is_available(self) -> bool:
        """Audible needs no credentials, so it is always available."""
        return True

    def _build_search_params(self, options: MetadataSearchOptions) -> dict[str, Any]:
        """Translate unified search options into Audible's query dialect."""
        params: dict[str, Any] = {
            "num_results": min(options.limit, MAX_RESULTS_PER_PAGE),
            # Shelfmark pages from 1, Audible from 0 — verified against the
            # live API, where page=0 and an omitted page return the same rows.
            "page": max(options.page - 1, 0),
            "products_sort_by": SORT_MAPPING.get(options.sort, "Relevance"),
            "response_groups": RESPONSE_GROUPS,
            "image_sizes": "500,1024",
        }

        title = str(options.fields.get("title", "") or "").strip()
        author = str(options.fields.get("author", "") or "").strip()
        narrator = str(options.fields.get("narrator", "") or "").strip()
        query = options.query.strip()

        if title:
            params["title"] = title
        if author:
            params["author"] = author
        if narrator:
            params["narrator"] = narrator

        if query:
            # A field-first search treats the free-text box as extra keywords;
            # otherwise the search type decides which field the query lands in.
            if title or author or narrator:
                params["keywords"] = query
            elif options.search_type == SearchType.TITLE:
                params["title"] = query
            elif options.search_type == SearchType.AUTHOR:
                params["author"] = query
            else:
                params["keywords"] = query

        return params

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search the Audible catalog."""
        return self.search_paginated(options).books

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Search with real pagination, which Audible reports as a total."""
        if options.search_type == SearchType.ISBN:
            # Audible indexes recordings by ASIN and has no ISBN lookup at all.
            return SearchResult(books=[], page=options.page)

        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = (
            f"{self.region}:{options.query}:{options.search_type.value}:{options.sort.value}:"
            f"{options.limit}:{options.page}:{fields_key}"
        )
        payload = self._search_cached(cache_key, options)

        books = []
        for product in payload.get("products", []):
            book = self._parse_product(product)
            if book:
                books.append(book)

        total = payload.get("total_results")
        total_found = total if isinstance(total, int) else 0
        # Audible's own page numbering is what decides whether more exist.
        seen = max(options.page, 1) * max(options.limit, 1)

        return SearchResult(
            books=books,
            page=options.page,
            total_found=total_found,
            has_more=total_found > seen,
        )

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="audible:search")
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> dict[str, Any]:
        """Fetch and cache one page of raw catalog results."""
        try:
            response = self.session.get(
                f"{self.base_url}/1.0/catalog/products",
                params=self._build_search_params(options),
                timeout=15,
                verify=get_ssl_verify(self.base_url),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout:
            logger.warning("Audible search timed out")
            return {}
        except requests.RequestException:
            logger.exception("Audible search request failed")
            return {}
        except TypeError, ValueError:
            logger.exception("Audible search parsing error")
            return {}

        if not isinstance(payload, dict):
            return {}

        logger.info(
            "Audible search '%s' returned %s results",
            options.query,
            len(payload.get("products", [])),
        )
        return payload

    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get one recording by ASIN, enriched via audnexus when enabled."""
        asin = str(book_id or "").strip().upper()
        if not _ASIN_SHAPE.match(asin):
            logger.debug("Not a valid Audible ASIN: %s", book_id)
            return None

        # The shared metadata cache drops `self` from its key, so the region
        # and enrichment flag have to travel in the key itself. An ASIN only
        # means something inside the storefront it came from: without this a
        # US lookup would answer a UK one with a different recording.
        return self._get_book_cached(f"{self.region}:{int(self.enrich)}:{asin}", asin)

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="audible:book")
    def _get_book_cached(self, cache_key: str, asin: str) -> BookMetadata | None:
        """Fetch and cache one recording."""
        del cache_key

        try:
            response = self.session.get(
                f"{self.base_url}/1.0/catalog/products/{asin}",
                params={"response_groups": RESPONSE_GROUPS, "image_sizes": "500,1024"},
                timeout=15,
                verify=get_ssl_verify(self.base_url),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout:
            logger.warning("Audible get_book timed out")
            return None
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == HTTPStatus.NOT_FOUND:
                logger.debug("Audible ASIN not found: %s", asin)
            else:
                logger.exception("Audible HTTP error")
            return None
        except requests.RequestException:
            logger.exception("Audible get_book request failed")
            return None
        except TypeError, ValueError:
            logger.exception("Audible get_book parsing error")
            return None

        product = payload.get("product") if isinstance(payload, dict) else None
        book = self._parse_product(product)
        if book and self.enrich:
            book = self._enrich(book, asin)
        return book

    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """Audible has no ISBN index — recordings are catalogued by ASIN."""
        del isbn
        return None

    def _enrich(self, book: BookMetadata, asin: str) -> BookMetadata:
        """Add audnexus genres and ISBN, keeping the book if audnexus is down."""
        from dataclasses import replace

        try:
            response = self.session.get(
                f"{AUDNEXUS_BASE_URL}/books/{asin}",
                params={"region": self.region},
                timeout=10,
                verify=get_ssl_verify(AUDNEXUS_BASE_URL),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException, TypeError, ValueError:
            # audnex.us is a free community service. Losing it costs genres,
            # never the book, so this is deliberately not logged as an error.
            logger.debug("audnexus enrichment unavailable for %s", asin)
            return book

        if not isinstance(payload, dict):
            return book

        updates: dict[str, Any] = {}

        genres = [g.get("name") for g in payload.get("genres", []) if isinstance(g, dict)]
        genres = [g for g in genres if isinstance(g, str) and g.strip()]
        if genres and not book.genres:
            updates["genres"] = genres

        isbn = payload.get("isbn")
        if isinstance(isbn, str) and isbn.strip() and not book.isbn_13:
            updates["isbn_13"] = isbn.strip()

        return replace(book, **updates) if updates else book

    def _parse_product(self, product: object) -> BookMetadata | None:
        """Parse one catalog product into BookMetadata."""
        if not isinstance(product, dict):
            return None

        try:
            asin = str(product.get("asin") or "").strip().upper()
            title = product.get("title")
            if not asin or not isinstance(title, str) or not title.strip():
                return None

            return BookMetadata(
                provider="audible",
                provider_id=asin,
                title=title.strip(),
                provider_display_name="Audible",
                asin=asin,
                authors=_names(product.get("authors")),
                subtitle=_optional_text(product.get("subtitle")),
                cover_url=_cover_url(product.get("product_images")),
                # Audiobook art is square; the portrait default would letterbox
                # every card in the results grid.
                cover_aspect="square",
                description=_strip_html(product.get("publisher_summary"))
                or _strip_html(product.get("merchandising_summary")),
                publisher=_optional_text(product.get("publisher_name")),
                publish_year=_publish_year(product),
                language=_language(product.get("language")),
                source_url=f"https://www.audible.{self.tld}/pd/{asin}",
                display_fields=_display_fields(product),
                **_series_fields(product.get("series")),
            )
        except (TypeError, ValueError, AttributeError, KeyError) as e:
            logger.debug("Failed to parse Audible product: %s", e)
            return None


def _optional_text(value: object) -> str | None:
    """Return a stripped string, or None when there is nothing useful."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _language(value: object) -> str | None:
    """Map Audible's English language name to an ISO 639-1 code."""
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip().casefold()
    return LANGUAGE_CODES.get(normalized, normalized)


def _cover_url(images: object) -> str | None:
    """Pick the largest cover Audible offered."""
    if not isinstance(images, dict) or not images:
        return None

    def size(key: object) -> int:
        try:
            return int(str(key))
        except ValueError:
            return 0

    best = max(images, key=size)
    return _optional_text(images.get(best))


def _publish_year(product: dict[str, Any]) -> int | None:
    """Extract the release year from whichever date field is populated."""
    for key in ("release_date", "issue_date", "publication_datetime"):
        value = product.get(key)
        if isinstance(value, str) and len(value) >= 4:
            try:
                return int(value[:4])
            except ValueError:
                continue
    return None


def _series_fields(series: object) -> dict[str, Any]:
    """Pick this book's own series out of Audible's list.

    A numbered entry wins over an unnumbered one regardless of order: a book
    belongs to the series that gives it a position, while the entries without
    one are broad shelves. Audible returns them in either order — *The Well of
    Ascension* comes back under "The Cosmere" first, ahead of "The Mistborn
    Saga" where it is actually book two.
    """
    if not isinstance(series, list):
        return {}

    entries = [entry for entry in series if isinstance(entry, dict)]
    if not entries:
        return {}

    chosen = entries[0]
    position = None
    for entry in entries:
        sequence = _optional_text(entry.get("sequence"))
        if not sequence:
            continue
        try:
            position = float(sequence)
        except ValueError:
            continue
        chosen = entry
        break

    fields: dict[str, Any] = {
        "series_id": _optional_text(chosen.get("asin")),
        "series_name": _optional_text(chosen.get("title")),
        "series_position": position,
    }

    return {k: v for k, v in fields.items() if v is not None}


def _display_fields(product: dict[str, Any]) -> list[DisplayField]:
    """Build the card facts that only an audiobook catalog can supply."""
    fields: list[DisplayField] = []

    narrators = _names(product.get("narrators"))
    if narrators:
        fields.append(
            DisplayField(label="Narrator", value=", ".join(narrators), icon="users"),
        )

    runtime = _format_runtime(product.get("runtime_length_min"))
    if runtime:
        fields.append(DisplayField(label="Length", value=runtime, icon="book"))

    rating = product.get("rating")
    if isinstance(rating, dict):
        distribution = rating.get("overall_distribution")
        if isinstance(distribution, dict):
            average = _optional_text(distribution.get("display_average_rating"))
            count = distribution.get("num_ratings")
            if average:
                label = f"{average} ({count:,})" if isinstance(count, int) else average
                fields.append(DisplayField(label="Rating", value=label, icon="star"))

    # Abridgement is a property of the recording, not of the book, so it is the
    # one thing worth surfacing before someone downloads the wrong thing.
    if str(product.get("format_type") or "").casefold() == "abridged":
        fields.append(DisplayField(label="Format", value="Abridged", icon="book"))

    return fields


@register_provider_kwargs("audible")
def _audible_kwargs() -> dict[str, Any]:
    """Build provider kwargs from settings."""
    from shelfmark.core.config import config

    return {
        "region": str(config.get("AUDIBLE_REGION", DEFAULT_REGION) or DEFAULT_REGION),
        "enrich": config.get("AUDIBLE_AUDNEXUS_ENRICHMENT", True) is not False,
    }


def _test_audible_connection() -> dict[str, Any]:
    """Test the Audible catalog API connection."""
    try:
        provider = AudibleProvider(**_audible_kwargs())
        response = provider.session.get(
            f"{provider.base_url}/1.0/catalog/products",
            params={"num_results": 1, "keywords": "test", "response_groups": "product_desc"},
            timeout=10,
            verify=get_ssl_verify(provider.base_url),
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except requests.RequestException as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    except (TypeError, ValueError, AttributeError) as e:
        return {"success": False, "message": f"Error: {e!s}"}

    if isinstance(data, dict) and "products" in data:
        return {"success": True, "message": "Successfully connected to the Audible catalog"}
    return {"success": False, "message": "Unexpected response from the Audible catalog"}


_AUDIBLE_REGION_OPTIONS = [
    {"value": "us", "label": "United States (audible.com)"},
    {"value": "uk", "label": "United Kingdom (audible.co.uk)"},
    {"value": "ca", "label": "Canada (audible.ca)"},
    {"value": "au", "label": "Australia (audible.com.au)"},
    {"value": "de", "label": "Germany (audible.de)"},
    {"value": "fr", "label": "France (audible.fr)"},
    {"value": "es", "label": "Spain (audible.es)"},
    {"value": "it", "label": "Italy (audible.it)"},
    {"value": "in", "label": "India (audible.in)"},
    {"value": "jp", "label": "Japan (audible.co.jp)"},
    {"value": "br", "label": "Brazil (audible.com.br)"},
]

_AUDIBLE_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "popularity", "label": "Best sellers"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]


@register_settings("audible", "Audible", icon="book", order=50, group="metadata_providers")
def audible_settings() -> list[SettingsField]:
    """Audible metadata provider settings."""
    return [
        HeadingField(
            key="audible_heading",
            title="Audible",
            description=(
                "Audiobook metadata from Audible's public catalog — narrator, runtime and "
                "series, none of which a print-book provider can supply. No API key required. "
                "Audible only catalogues audiobooks, so leave your book provider set to "
                "something else."
            ),
            link_url="https://www.audible.com",
            link_text="audible.com",
        ),
        CheckboxField(
            key="AUDIBLE_ENABLED",
            label="Enable Audible",
            description="Enable Audible as a metadata provider for audiobook searches",
            default=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify the Audible catalog API is accessible",
            style="primary",
            callback=_test_audible_connection,
        ),
        SelectField(
            key="AUDIBLE_REGION",
            label="Audible Region",
            description=(
                "Which Audible storefront to search. Catalogues and ASINs differ by region, "
                "so pick the one your library was matched against."
            ),
            options=_AUDIBLE_REGION_OPTIONS,
            default=DEFAULT_REGION,
        ),
        SelectField(
            key="AUDIBLE_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Audible search results.",
            options=_AUDIBLE_SORT_OPTIONS,
            default="relevance",
        ),
        CheckboxField(
            key="AUDIBLE_AUDNEXUS_ENRICHMENT",
            label="Enrich details via Audnexus",
            description=(
                "Fetch genres and ISBNs from api.audnex.us when opening a book's details. "
                "Audnexus is a free community service; if it is unreachable the book still "
                "loads without them."
            ),
            default=True,
        ),
    ]
