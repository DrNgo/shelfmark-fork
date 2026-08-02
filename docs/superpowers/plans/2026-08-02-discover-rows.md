# Discover Rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audible-style discover rows (Trending/Best Sellers + New Releases) on the landing page's initial state, sourced from Hardcover or Audible per content type, cached with serve-stale.

**Architecture:** New discover fetcher methods on `HardcoverProvider`/`AudibleProvider` (strict `list[BookMetadata] | None` contract: `None`=failure, `[]`=empty) → new `shelfmark/core/discover.py` service (provider dispatch via `get_configured_provider_name` + enabled/available gates, dual-entry fresh/last-good caching on the existing `CacheService`, per-key single-flight) → one `GET /api/discover` endpoint reusing the metadata-search serialization → frontend `DiscoverSection` rendered in the initial state, reusing the shared badge components and a Book-taking details handler.

**Tech Stack:** Python 3.14/Flask backend (pytest via `uv run pytest`), React+TypeScript frontend (vitest `npm run test:unit`, `npm run typecheck`). No DOM-testing library exists in the frontend — UI logic that needs tests lives in pure helpers.

Spec: `docs/superpowers/specs/2026-08-02-discover-rows-design.md`. API research: `docs/superpowers/specs/2026-08-02-discover-hardcover-api-notes.md`. Revised after a Codex plan review (verified callback signatures, auth flow, provider gating, failure semantics).

## Global Constraints

- Fetcher contract everywhere: `None` = provider failure (triggers stale fallback), `[]` = genuinely empty (cached success). Any failed HTTP page fetch fails the whole fetch (`None`) — no partial rows. Individual malformed *records* inside a successful payload are skipped with a debug log, not failures.
- Row size 20 (`ROW_LIMIT`). TTLs: popularity rows 6 h, new releases 24 h, last-good 7 d.
- Row keys/labels: hardcover → `trending` "Trending", `new_releases` "New Releases"; audible → `best_sellers` "Best Sellers", `new_releases` "New Releases".
- Provider resolution: `get_configured_provider_name(content_type, user_id=...)` (handles `combined`), then gate on `is_provider_enabled(name)` and `provider.is_available()`. NEVER `get_configured_provider()` (no combined support).
- Do not modify `CacheService` semantics. Do not route discover fetches through `_search_cached`/search paths.
- Frontend callbacks (verified against App.tsx): `getUniversalActionButtonState(bookId: string): ButtonStateInfo` (App.tsx:1937), `handleShowDetails(id: string)` looks ids up in search-results state and FAILS for discover books — discover click-through needs the new Book-taking handler defined in Task 6.
- Library-match records are keyed by `book.id` (`buildLibraryLookupPayload`, utils/libraryMatches.ts:39). `Book.preview` is the cover URL; `Book.author` is a joined string (bookTransformers.ts:77).
- Settings key `SHOW_DISCOVER_ROWS` (default True, label "Show Discover Rows" — Title Case per repo convention), config payload key `show_discover_rows`.
- Backend tests: `uv run pytest <path> -x --tb=short`. Frontend: `cd src/frontend && npm run test:unit -- <path>` and `npm run typecheck`.
- Commit after each task with a `feat(discover): ...` message.

---

### Task 1: Hardcover discover fetchers

**Files:**
- Modify: `shelfmark/metadata_providers/hardcover.py` (add query constants near the other `*_QUERY` constants; add 2 methods on `HardcoverProvider` near `get_book` ~line 2441)
- Test: `tests/metadata/test_hardcover_discover.py` (new)

**Interfaces:**
- Consumes: existing `_execute_query(query, variables, raise_on_error=True)`, `_parse_book(book_dict)`, `HardcoverGraphQLError`, `self.api_key`.
- Produces: `HardcoverProvider.discover_trending(limit: int = 20, *, audio_only: bool = False) -> list[BookMetadata] | None` and `HardcoverProvider.discover_new_releases(limit: int = 20, *, audio_only: bool = False) -> list[BookMetadata] | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/metadata/test_hardcover_discover.py`:

```python
"""Tests for HardcoverProvider discover fetchers (trending / new releases)."""

from unittest.mock import patch

import pytest

from shelfmark.metadata_providers.hardcover import HardcoverGraphQLError, HardcoverProvider


def _book(book_id: int, title: str = "Book") -> dict:
    return {
        "id": book_id,
        "title": f"{title} {book_id}",
        "slug": f"book-{book_id}",
        "release_date": "2026-06-01",
        "cached_image": {"url": f"https://img/{book_id}.jpg"},
        "contributions": [{"author": {"name": "Author Name"}}],
    }


@pytest.fixture
def provider() -> HardcoverProvider:
    return HardcoverProvider(api_key="test-key")


class TestDiscoverTrending:
    def test_returns_books_in_trending_id_order(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [7, 3, 9]}}
        # Hydration returns a different order than the trending ids.
        books_payload = {"books": [_book(3), _book(9), _book(7)]}
        with patch.object(
            provider, "_execute_query", side_effect=[ids_payload, books_payload]
        ) as mock_q:
            result = provider.discover_trending(limit=3)
        assert result is not None
        assert [b.provider_id for b in result] == ["7", "3", "9"]
        # Step 1 over-fetches ids at 3x the row limit.
        assert mock_q.call_args_list[0].args[1]["limit"] == 9

    def test_respects_limit_after_reorder(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [1, 2, 3]}}
        books_payload = {"books": [_book(1), _book(2), _book(3)]}
        with patch.object(provider, "_execute_query", side_effect=[ids_payload, books_payload]):
            result = provider.discover_trending(limit=2)
        assert result is not None
        assert len(result) == 2

    def test_trending_error_payload_returns_none(self, provider):
        ids_payload = {"books_trending": {"error": "boom", "ids": [1]}}
        with patch.object(provider, "_execute_query", return_value=ids_payload):
            assert provider.discover_trending() is None

    def test_transport_failure_returns_none(self, provider):
        with patch.object(provider, "_execute_query", side_effect=RuntimeError("down")):
            assert provider.discover_trending() is None

    def test_graphql_rejection_returns_none(self, provider):
        with patch.object(provider, "_execute_query", side_effect=HardcoverGraphQLError("no")):
            assert provider.discover_trending() is None

    def test_empty_ids_returns_empty_list(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": []}}
        with patch.object(provider, "_execute_query", return_value=ids_payload) as mock_q:
            assert provider.discover_trending() == []
        assert mock_q.call_count == 1  # no hydration call

    def test_malformed_record_is_skipped_not_fatal(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [1, 2]}}
        # Book 1's contributions have a shape _parse_book chokes on.
        bad = _book(1)
        bad["contributions"] = [{"author": "not-a-dict"}]
        books_payload = {"books": [bad, _book(2)]}
        with patch.object(provider, "_execute_query", side_effect=[ids_payload, books_payload]):
            result = provider.discover_trending()
        assert result is not None
        assert [b.provider_id for b in result] == ["2"]

    def test_audio_only_uses_audio_hydration_query(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [5]}}
        books_payload = {"books": [_book(5)]}
        with patch.object(
            provider, "_execute_query", side_effect=[ids_payload, books_payload]
        ) as mock_q:
            result = provider.discover_trending(audio_only=True)
        assert result is not None
        hydration_query = mock_q.call_args_list[1].args[0]
        assert "default_audio_edition_id" in hydration_query

    def test_no_api_key_returns_none(self):
        assert HardcoverProvider(api_key="").discover_trending() is None


class TestDiscoverNewReleases:
    def test_returns_parsed_books(self, provider):
        with patch.object(
            provider, "_execute_query", return_value={"books": [_book(1), _book(2)]}
        ) as mock_q:
            result = provider.discover_new_releases(limit=5)
        assert result is not None
        assert [b.provider_id for b in result] == ["1", "2"]
        variables = mock_q.call_args.args[1]
        assert variables["limit"] == 5
        assert variables["from"] < variables["to"]  # ISO date window

    def test_failure_returns_none(self, provider):
        with patch.object(provider, "_execute_query", side_effect=RuntimeError("down")):
            assert provider.discover_new_releases() is None

    def test_non_list_books_payload_returns_none(self, provider):
        with patch.object(provider, "_execute_query", return_value={"books": "garbage"}):
            assert provider.discover_new_releases() is None

    def test_empty_returns_empty_list(self, provider):
        with patch.object(provider, "_execute_query", return_value={"books": []}):
            assert provider.discover_new_releases() == []

    def test_audio_only_uses_audio_query(self, provider):
        with patch.object(
            provider, "_execute_query", return_value={"books": []}
        ) as mock_q:
            provider.discover_new_releases(audio_only=True)
        assert "default_audio_edition_id" in mock_q.call_args.args[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/metadata/test_hardcover_discover.py -x --tb=short`
Expected: FAIL with `AttributeError: ... has no attribute 'discover_trending'`

(If `HardcoverProvider(api_key=...)` is not the constructor signature, check `_hardcover_kwargs` (hardcover.py:908) — the provider is constructed with `api_key=` kwarg; adjust the fixture only if the real `__init__` differs. If `test_malformed_record_is_skipped_not_fatal`'s bad shape doesn't actually raise inside `_parse_book`, pick a shape that does — e.g. `bad["featured_book_series"] = "not-a-dict"` — the point is one bad record, not the specific field.)

- [ ] **Step 3: Add the GraphQL constants**

In `hardcover.py`, after `AUTHOR_BOOKS_BY_ID_QUERY` (~line 460), add. The
book-field block copies `LIST_BOOKS_BY_ID_QUERY`'s inner `book` selection — the
same subset the list-browse path feeds `_parse_book` today (`_parse_book`
tolerates the fields this fragment omits, e.g. `cached_tags`, ISBN/edition
fields; they come back `None`/empty exactly as in list browsing):

```python
_DISCOVER_BOOK_FIELDS = """
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
"""

DISCOVER_TRENDING_IDS_QUERY = """
query DiscoverTrendingIds($from: date!, $to: date!, $limit: Int!) {
    books_trending(from: $from, to: $to, limit: $limit, offset: 0) {
        error
        ids
    }
}
"""

DISCOVER_BOOKS_BY_IDS_QUERY = f"""
query DiscoverBooksByIds($ids: [Int!]!) {{
    books(where: {{id: {{_in: $ids}}}}) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""

DISCOVER_BOOKS_BY_IDS_AUDIO_QUERY = f"""
query DiscoverBooksByIdsAudio($ids: [Int!]!) {{
    books(
        where: {{id: {{_in: $ids}}, default_audio_edition_id: {{_is_null: false}}}}
    ) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""

_DISCOVER_NEW_RELEASES_WHERE = """
            release_date: {_gte: $from, _lte: $to},
            canonical_id: {_is_null: true},
            state: {_in: ["normalized", "normalizing"]},
            compilation: {_eq: false},
            users_count: {_gte: 10}
"""

DISCOVER_NEW_RELEASES_QUERY = f"""
query DiscoverNewReleases($from: date!, $to: date!, $limit: Int!) {{
    books(
        where: {{{_DISCOVER_NEW_RELEASES_WHERE}}},
        order_by: [{{users_count: desc_nulls_last}}, {{release_date: desc}}],
        limit: $limit
    ) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""

DISCOVER_NEW_RELEASES_AUDIO_QUERY = f"""
query DiscoverNewReleasesAudio($from: date!, $to: date!, $limit: Int!) {{
    books(
        where: {{{_DISCOVER_NEW_RELEASES_WHERE},
            default_audio_edition_id: {{_is_null: false}}}},
        order_by: [{{users_count: desc_nulls_last}}, {{release_date: desc}}],
        limit: $limit
    ) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""
```

- [ ] **Step 4: Add the fetcher methods**

On `HardcoverProvider`, next to `get_book` (~line 2441):

```python
    def _parse_discover_books(self, books: list) -> list[BookMetadata]:
        """Parse records, skipping (not failing on) malformed ones."""
        parsed: list[BookMetadata] = []
        for book in books:
            if not isinstance(book, dict):
                continue
            try:
                parsed.append(self._parse_book(book))
            except (TypeError, ValueError, AttributeError, KeyError):
                logger.debug("Skipping malformed discover record: %s", book.get("id"))
        return parsed

    def discover_trending(
        self, limit: int = 20, *, audio_only: bool = False
    ) -> list[BookMetadata] | None:
        """Trending row: ranked ids from books_trending, hydrated and re-ordered.

        Returns None on provider failure, [] when the feed is genuinely empty.
        """
        if not self.api_key:
            return None

        from datetime import timedelta

        to_date = datetime.now(UTC).date()
        from_date = to_date - timedelta(days=30)
        try:
            ids_data = self._execute_query(
                DISCOVER_TRENDING_IDS_QUERY,
                {
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    # Over-fetch: the audio filter (and skipped records) shrink
                    # the hydrated set; 3x is the spec's top-up strategy.
                    "limit": limit * 3,
                },
                raise_on_error=True,
            )
        except (RuntimeError, HardcoverGraphQLError):
            return None
        if ids_data is None:
            return None

        trending = ids_data.get("books_trending") or {}
        if trending.get("error"):
            return None
        ids = [i for i in (trending.get("ids") or []) if isinstance(i, int)]
        if not ids:
            return []

        hydration_query = (
            DISCOVER_BOOKS_BY_IDS_AUDIO_QUERY if audio_only else DISCOVER_BOOKS_BY_IDS_QUERY
        )
        try:
            books_data = self._execute_query(
                hydration_query, {"ids": ids}, raise_on_error=True
            )
        except (RuntimeError, HardcoverGraphQLError):
            return None
        if books_data is None:
            return None
        books = books_data.get("books")
        if not isinstance(books, list):
            return None

        by_id = {
            book["id"]: book
            for book in books
            if isinstance(book, dict) and isinstance(book.get("id"), int)
        }
        # books(id: {_in: ...}) does not preserve order; restore trending rank.
        ordered = [by_id[book_id] for book_id in ids if book_id in by_id]
        return self._parse_discover_books(ordered)[:limit]

    def discover_new_releases(
        self, limit: int = 20, *, audio_only: bool = False
    ) -> list[BookMetadata] | None:
        """New-releases row: notable books released in the last 90 days.

        Returns None on provider failure, [] when nothing qualifies.
        """
        if not self.api_key:
            return None

        from datetime import timedelta

        to_date = datetime.now(UTC).date()
        from_date = to_date - timedelta(days=90)
        query = (
            DISCOVER_NEW_RELEASES_AUDIO_QUERY if audio_only else DISCOVER_NEW_RELEASES_QUERY
        )
        try:
            data = self._execute_query(
                query,
                {
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "limit": limit,
                },
                raise_on_error=True,
            )
        except (RuntimeError, HardcoverGraphQLError):
            return None
        if data is None:
            return None

        books = data.get("books")
        if not isinstance(books, list):
            return None
        return self._parse_discover_books(books)
```

`datetime`/`UTC` are already imported at the top of hardcover.py (line 6).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/metadata/test_hardcover_discover.py -x --tb=short`
Expected: PASS (all)

- [ ] **Step 6: Run the full non-e2e suite for regressions**

Run: `uv run pytest tests/ -x --tb=short -m "not integration and not e2e"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add shelfmark/metadata_providers/hardcover.py tests/metadata/test_hardcover_discover.py
git commit -m "feat(discover): hardcover trending and new-release fetchers"
```

---

### Task 2: Audible discover fetchers

**Files:**
- Modify: `shelfmark/metadata_providers/audible.py` (constants near `RESPONSE_GROUPS` line 84; methods on `AudibleProvider` near `search_paginated` line 254)
- Test: `tests/metadata/test_audible_discover.py` (new)

**Interfaces:**
- Consumes: `self.session` (requests.Session), `self.base_url`, `RESPONSE_GROUPS`, `MAX_RESULTS_PER_PAGE`, `_parse_product`, `get_ssl_verify`.
- Produces: `AudibleProvider.discover_best_sellers(limit: int = 20) -> list[BookMetadata] | None` and `AudibleProvider.discover_new_releases(limit: int = 20) -> list[BookMetadata] | None`. Both top up from page 1 when short; any failed page fails the whole fetch (`None`) — no partial rows.

- [ ] **Step 1: Write the failing tests**

Create `tests/metadata/test_audible_discover.py`:

```python
"""Tests for AudibleProvider discover fetchers (best sellers / new releases)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.metadata_providers.audible import AudibleProvider

TODAY = datetime.now(UTC).date()
PAST = (TODAY - timedelta(days=10)).isoformat()
FUTURE = (TODAY + timedelta(days=10)).isoformat()


def _product(asin: str, issue_date: str = PAST, **overrides) -> dict:
    product = {
        "asin": asin,
        "title": f"Title {asin}",
        "issue_date": issue_date,
        "is_listenable": True,
        "content_delivery_type": "SinglePartBook",
    }
    product.update(overrides)
    return product


def _response(products: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"products": products}
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def provider() -> AudibleProvider:
    return AudibleProvider()


class TestDiscoverBestSellers:
    def test_returns_parsed_books_with_browse_params(self, provider):
        products = [_product(f"B00000000{i}") for i in range(3)]
        with patch.object(provider.session, "get", return_value=_response(products)) as mock_get:
            result = provider.discover_best_sellers(limit=3)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000000", "B000000001", "B000000002"]
        params = mock_get.call_args.kwargs["params"]
        assert params["products_sort_by"] == "BestSellers"
        assert "keywords" not in params
        assert "title" not in params

    def test_filters_non_listenable_and_podcasts(self, provider):
        products = [
            _product("B000000000"),
            _product("B000000001", is_listenable=False),
            _product("B000000002", content_delivery_type="PodcastParent"),
        ]
        with patch.object(provider.session, "get", return_value=_response(products)):
            result = provider.discover_best_sellers(limit=10)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000000"]

    def test_tops_up_from_page_1_when_filtered_short(self, provider):
        page0 = [_product(f"B0000000{i:02d}", is_listenable=False) for i in range(50)]
        page1 = [_product("B000000090"), _product("B000000091")]
        with patch.object(
            provider.session, "get", side_effect=[_response(page0), _response(page1)]
        ) as mock_get:
            result = provider.discover_best_sellers(limit=2)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000090", "B000000091"]
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[1].kwargs["params"]["page"] == 1

    def test_full_first_page_makes_single_request(self, provider):
        products = [_product(f"B0000000{i:02d}") for i in range(50)]
        with patch.object(
            provider.session, "get", return_value=_response(products)
        ) as mock_get:
            result = provider.discover_best_sellers(limit=20)
        assert result is not None
        assert len(result) == 20
        assert mock_get.call_count == 1

    def test_request_error_returns_none(self, provider):
        with patch.object(
            provider.session, "get", side_effect=requests.ConnectionError("down")
        ):
            assert provider.discover_best_sellers() is None

    def test_malformed_payload_returns_none(self, provider):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"unexpected": True}
        with patch.object(provider.session, "get", return_value=response):
            assert provider.discover_best_sellers() is None

    def test_no_qualifying_products_returns_empty(self, provider):
        with patch.object(provider.session, "get", return_value=_response([])):
            assert provider.discover_best_sellers() == []


class TestDiscoverNewReleases:
    def test_drops_preorders(self, provider):
        products = [
            _product("B000000000", issue_date=FUTURE),
            _product("B000000001", issue_date=PAST),
        ]
        with patch.object(provider.session, "get", return_value=_response(products)) as mock_get:
            result = provider.discover_new_releases(limit=5)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000001"]
        assert mock_get.call_args_list[0].kwargs["params"]["products_sort_by"] == "-ReleaseDate"

    def test_tops_up_from_page_1_when_short(self, provider):
        page0 = [_product(f"B0000000{i:02d}", issue_date=FUTURE) for i in range(50)]
        page1 = [_product("B000000090"), _product("B000000091")]
        with patch.object(
            provider.session, "get", side_effect=[_response(page0), _response(page1)]
        ) as mock_get:
            result = provider.discover_new_releases(limit=2)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000090", "B000000091"]
        assert mock_get.call_count == 2

    def test_stops_at_two_pages(self, provider):
        page = [_product("B000000000", issue_date=FUTURE)] * 50
        with patch.object(
            provider.session, "get", side_effect=[_response(page), _response(page)]
        ) as mock_get:
            result = provider.discover_new_releases(limit=20)
        assert result == []
        assert mock_get.call_count == 2

    def test_drops_missing_issue_date(self, provider):
        products = [_product("B000000000", issue_date=""), _product("B000000001")]
        with patch.object(provider.session, "get", return_value=_response(products)):
            result = provider.discover_new_releases(limit=5)
        assert result is not None
        assert [b.provider_id for b in result] == ["B000000001"]

    def test_any_page_failure_returns_none(self, provider):
        # Page 0 succeeds but is short; page-1 failure fails the whole fetch —
        # partial rows must never be cached as fresh/last-good (spec contract).
        page0 = [_product("B000000000")]
        with patch.object(
            provider.session,
            "get",
            side_effect=[_response(page0), requests.ConnectionError("down")],
        ):
            assert provider.discover_new_releases(limit=5) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/metadata/test_audible_discover.py -x --tb=short`
Expected: FAIL with `AttributeError: ... 'discover_best_sellers'`

(If `AudibleProvider()` needs kwargs, check `_audible_kwargs` (audible.py:557) — it passes `region=DEFAULT_REGION`; mirror that in the fixture if the bare constructor fails.)

- [ ] **Step 3: Implement**

In `audible.py`, near `RESPONSE_GROUPS` (line 84) add:

```python
# Browse-feed content kinds that are actual audiobooks. The catalog browse feed
# also carries podcasts/periodicals, and _parse_product does not filter kinds.
DISCOVER_DELIVERY_TYPES = frozenset({"SinglePartBook", "MultiPartBook"})
DISCOVER_MAX_PAGES = 2
```

On `AudibleProvider` (after `search_paginated`, ~line 254):

```python
    def _discover_fetch_page(self, sort: str, page: int) -> list[dict] | None:
        """Fetch one no-keyword browse page. None on failure."""
        try:
            response = self.session.get(
                f"{self.base_url}/1.0/catalog/products",
                params={
                    "num_results": MAX_RESULTS_PER_PAGE,
                    "page": page,
                    "products_sort_by": sort,
                    "response_groups": RESPONSE_GROUPS,
                    "image_sizes": "500,1024",
                },
                timeout=15,
                verify=get_ssl_verify(self.base_url),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Audible discover browse failed (sort=%s page=%s)", sort, page)
            return None

        products = payload.get("products") if isinstance(payload, dict) else None
        return products if isinstance(products, list) else None

    @staticmethod
    def _is_discover_book(product: object) -> bool:
        """Keep only listenable, actual-book products from the browse feed."""
        if not isinstance(product, dict):
            return False
        if product.get("is_listenable") is not True:
            return False
        return product.get("content_delivery_type") in DISCOVER_DELIVERY_TYPES

    def _discover_browse(
        self,
        sort: str,
        limit: int,
        *,
        released_only: bool = False,
    ) -> list[BookMetadata] | None:
        """Shared browse loop: up to DISCOVER_MAX_PAGES, filter, parse.

        Any failed page fails the whole fetch (None) so partial rows are never
        cached as fresh/last-good. [] means the feed genuinely had nothing.
        """
        today = datetime.now(UTC).date().isoformat()
        books: list[BookMetadata] = []
        for page in range(DISCOVER_MAX_PAGES):
            products = self._discover_fetch_page(sort, page)
            if products is None:
                return None
            for product in products:
                if not self._is_discover_book(product):
                    continue
                if released_only:
                    issue_date = str(product.get("issue_date") or "")
                    # ISO dates compare correctly as strings; a missing date is
                    # unknown and cannot be shown as a "new release".
                    if not issue_date or issue_date > today:
                        continue
                parsed = self._parse_product(product)
                if parsed is not None:
                    books.append(parsed)
                    if len(books) >= limit:
                        return books
        return books

    def discover_best_sellers(self, limit: int = 20) -> list[BookMetadata] | None:
        """Best-sellers row. None on failure, [] when nothing qualifies."""
        return self._discover_browse("BestSellers", limit)

    def discover_new_releases(self, limit: int = 20) -> list[BookMetadata] | None:
        """New-releases row: -ReleaseDate browse minus preorders.

        The sort leads with future issue_dates, so the shared browse loop
        over-fetches and drops anything not yet released.
        """
        return self._discover_browse("-ReleaseDate", limit, released_only=True)
```

Check imports at the top of audible.py: `datetime`/`UTC` — if missing, add
`from datetime import UTC, datetime`. `requests` and `get_ssl_verify` are already
imported (used by `_search_cached`). `BookMetadata` is already imported.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/metadata/test_audible_discover.py -x --tb=short`
Expected: PASS

- [ ] **Step 5: Regression suite**

Run: `uv run pytest tests/ -x --tb=short -m "not integration and not e2e"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add shelfmark/metadata_providers/audible.py tests/metadata/test_audible_discover.py
git commit -m "feat(discover): audible best-sellers and new-release fetchers"
```

---

### Task 3: Discover service (dispatch + gates + dual-entry cache + single-flight)

**Files:**
- Create: `shelfmark/core/discover.py`
- Test: `tests/core/test_discover_service.py` (new)

**Interfaces:**
- Consumes: Task 1/2 fetchers; `get_configured_provider_name`, `get_provider`, `get_provider_kwargs`, `is_provider_enabled` from `shelfmark.metadata_providers`; `get_metadata_cache` from `shelfmark.core.cache`.
- Produces: `DiscoverRow` dataclass (`key: str, label: str, provider: str, books: list[BookMetadata], stale: bool`), `ROWS_BY_PROVIDER: dict[str, list[tuple[str, str]]]`, `ROW_LIMIT: int`, `ROW_TTLS`, `LAST_GOOD_TTL`, `get_discover_row(content_type: str, row_key: str, user_id: int | None = None) -> DiscoverRow | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_discover_service.py`:

```python
"""Tests for the discover service: dispatch, gating, dual-entry caching, serve-stale."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core import discover
from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import BookMetadata


def _books(n: int) -> list[BookMetadata]:
    return [
        BookMetadata(provider="hardcover", provider_id=str(i), title=f"Book {i}")
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def clean_cache():
    get_metadata_cache().clear()
    yield
    get_metadata_cache().clear()


def _patch_provider(name: str, provider: MagicMock, *, enabled: bool = True):
    return (
        patch.object(discover, "get_configured_provider_name", return_value=name),
        patch.object(discover, "get_provider", return_value=provider),
        patch.object(discover, "get_provider_kwargs", return_value={}),
        patch.object(discover, "is_provider_enabled", return_value=enabled),
    )


def _hardcover_mock(trending=None, new_releases=None) -> MagicMock:
    provider = MagicMock()
    provider.is_available.return_value = True
    provider.discover_trending.return_value = trending
    provider.discover_new_releases.return_value = new_releases
    return provider


class TestDispatch:
    def test_unknown_provider_returns_none(self):
        p1, p2, p3, p4 = _patch_provider("openlibrary", MagicMock())
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "trending") is None

    def test_unknown_row_returns_none(self):
        p1, p2, p3, p4 = _patch_provider("hardcover", _hardcover_mock())
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "best_sellers") is None

    def test_disabled_provider_returns_none(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider, enabled=False)
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "trending") is None
        provider.discover_trending.assert_not_called()

    def test_unavailable_provider_returns_none(self):
        provider = _hardcover_mock(trending=_books(1))
        provider.is_available.return_value = False
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "trending") is None
        provider.discover_trending.assert_not_called()

    def test_hardcover_audiobook_uses_audio_only(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("audiobook", "trending")
        assert row is not None
        provider.discover_trending.assert_called_once_with(
            discover.ROW_LIMIT, audio_only=True
        )

    def test_combined_resolves_via_provider_name_helper(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1 as name_mock, p2, p3, p4:
            row = discover.get_discover_row("combined", "trending", user_id=7)
        assert row is not None
        name_mock.assert_called_once_with("combined", user_id=7)
        # combined uses ebook-shaped rows
        provider.discover_trending.assert_called_once_with(
            discover.ROW_LIMIT, audio_only=False
        )

    def test_audible_best_sellers_dispatch(self):
        provider = MagicMock()
        provider.tld = "com"
        provider.is_available.return_value = True
        provider.discover_best_sellers.return_value = _books(2)
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("audiobook", "best_sellers")
        assert row is not None
        assert row.provider == "audible"
        assert len(row.books) == 2


class TestCaching:
    def test_second_call_hits_cache(self):
        provider = _hardcover_mock(trending=_books(3))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            first = discover.get_discover_row("ebook", "trending")
            second = discover.get_discover_row("ebook", "trending")
        assert first is not None and second is not None
        assert provider.discover_trending.call_count == 1
        assert second.stale is False

    def test_success_writes_fresh_and_last_good_with_ttls(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4, patch.object(get_metadata_cache(), "set") as mock_set:
            discover.get_discover_row("ebook", "trending")
        calls = {c.args[0]: c.args[2] for c in mock_set.call_args_list}
        assert calls["discover:hardcover:trending:all:fresh"] == discover.ROW_TTLS["trending"]
        assert calls["discover:hardcover:trending:all:last_good"] == discover.LAST_GOOD_TTL

    def test_new_releases_uses_24h_ttl(self):
        provider = _hardcover_mock(new_releases=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4, patch.object(get_metadata_cache(), "set") as mock_set:
            discover.get_discover_row("ebook", "new_releases")
        ttls = [c.args[2] for c in mock_set.call_args_list]
        assert discover.ROW_TTLS["new_releases"] in ttls
        assert discover.ROW_TTLS["new_releases"] == 24 * 3600

    def test_empty_result_is_cached_success(self):
        provider = _hardcover_mock(trending=[])
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("ebook", "trending")
            again = discover.get_discover_row("ebook", "trending")
        assert row is not None and row.books == [] and row.stale is False
        assert again is not None and again.books == []
        assert provider.discover_trending.call_count == 1

    def test_failure_serves_last_good_as_stale(self):
        books = _books(2)
        provider = _hardcover_mock(trending=books)
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            discover.get_discover_row("ebook", "trending")
        # Simulate fresh expiry while last_good survives.
        get_metadata_cache().invalidate("discover:hardcover:trending:all:fresh")
        provider.discover_trending.return_value = None
        with p1, p2, p3, p4:
            row = discover.get_discover_row("ebook", "trending")
        assert row is not None
        assert row.stale is True
        assert [b.provider_id for b in row.books] == ["0", "1"]

    def test_failure_without_stale_returns_empty_uncached(self):
        provider = _hardcover_mock(trending=None)
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("ebook", "trending")
            row2 = discover.get_discover_row("ebook", "trending")
        assert row is not None and row.books == []
        # Failures are never cached: a retry re-fetches.
        assert provider.discover_trending.call_count == 2
        assert row2 is not None and row2.books == []

    def test_audible_cache_key_includes_region(self):
        provider = MagicMock()
        provider.tld = "de"
        provider.is_available.return_value = True
        provider.discover_best_sellers.return_value = _books(1)
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with p1, p2, p3, p4:
            discover.get_discover_row("audiobook", "best_sellers")
        assert (
            get_metadata_cache().get("discover:audible:de:best_sellers:fresh") is not None
        )

    def test_hardcover_audio_variant_cached_separately(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            discover.get_discover_row("ebook", "trending")
            discover.get_discover_row("audiobook", "trending")
        assert provider.discover_trending.call_count == 2


class _SignallingLock:
    """Context-manager lock that reports when a SECOND acquirer is waiting.

    Lets the test prove the race actually happened: caller 2 must be blocked
    at the lock while caller 1's fetch is still in flight — otherwise the test
    could pass on an ordinary sequential cache hit.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._attempts = 0
        self.second_waiter = threading.Event()

    def __enter__(self):
        with self._guard:
            self._attempts += 1
            if self._attempts >= 2:
                self.second_waiter.set()
        self._lock.acquire()
        return self

    def __exit__(self, *exc: object) -> bool:
        self._lock.release()
        return False


class TestSingleFlight:
    def test_concurrent_cold_requests_fetch_once(self):
        release = threading.Event()
        fetch_started = threading.Event()

        def slow_fetch(*args, **kwargs):
            fetch_started.set()
            release.wait(timeout=5)
            return _books(1)

        provider = MagicMock()
        provider.is_available.return_value = True
        provider.discover_trending.side_effect = slow_fetch

        lock = _SignallingLock()
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        results: list = []
        with p1, p2, p3, p4, patch.object(discover, "_lock_for", return_value=lock):
            t1 = threading.Thread(
                target=lambda: results.append(discover.get_discover_row("ebook", "trending"))
            )
            t2 = threading.Thread(
                target=lambda: results.append(discover.get_discover_row("ebook", "trending"))
            )
            t1.start()
            assert fetch_started.wait(timeout=5)  # t1 holds the lock, fetch in flight
            t2.start()
            assert lock.second_waiter.wait(timeout=5)  # t2 is blocked at the lock
            release.set()  # only now may t1 finish and populate the cache
            t1.join(timeout=5)
            t2.join(timeout=5)

        assert len(results) == 2
        assert all(r is not None and len(r.books) == 1 for r in results)
        assert provider.discover_trending.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_discover_service.py -x --tb=short`
Expected: FAIL with `ImportError` / `ModuleNotFoundError: shelfmark.core.discover`

- [ ] **Step 3: Implement `shelfmark/core/discover.py`**

```python
"""Discover rows: provider dispatch, dual-entry caching, serve-stale.

Fetch contract (shared with the provider fetchers): a fetcher returns
None on provider failure and [] when the feed is genuinely empty. Failures
fall back to the 7-day "last_good" cache entry; empties are cached successes.
CacheService is used as-is — the stale window lives in a second entry rather
than in modified cache semantics.
"""

import threading
from dataclasses import dataclass, field
from typing import cast

from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import (
    BookMetadata,
    get_configured_provider_name,
    get_provider,
    get_provider_kwargs,
    is_provider_enabled,
)

logger = setup_logger(__name__)

ROWS_BY_PROVIDER: dict[str, list[tuple[str, str]]] = {
    "hardcover": [("trending", "Trending"), ("new_releases", "New Releases")],
    "audible": [("best_sellers", "Best Sellers"), ("new_releases", "New Releases")],
}

ROW_LIMIT = 20
ROW_TTLS: dict[str, int] = {
    "trending": 6 * 3600,
    "best_sellers": 6 * 3600,
    "new_releases": 24 * 3600,
}
LAST_GOOD_TTL = 7 * 24 * 3600


@dataclass(frozen=True)
class DiscoverRow:
    """One rendered discover row."""

    key: str
    label: str
    provider: str
    books: list[BookMetadata] = field(default_factory=list)
    stale: bool = False


# Bounded in practice: one lock per (provider, row, variant/region) actually
# requested — a handful of keys per deployment, so no eviction is needed.
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _fetch(provider: object, provider_name: str, row_key: str, *, audio_only: bool):
    if provider_name == "hardcover":
        if row_key == "trending":
            return provider.discover_trending(ROW_LIMIT, audio_only=audio_only)
        return provider.discover_new_releases(ROW_LIMIT, audio_only=audio_only)
    if row_key == "best_sellers":
        return provider.discover_best_sellers(ROW_LIMIT)
    return provider.discover_new_releases(ROW_LIMIT)


def get_discover_row(
    content_type: str, row_key: str, user_id: int | None = None
) -> DiscoverRow | None:
    """Return the discover row, or None if no provider/row applies.

    A returned row with empty books means "nothing to show" (hidden row);
    stale=True marks last-good data served through a provider outage.
    """
    provider_name = get_configured_provider_name(content_type, user_id=user_id)
    rows = dict(ROWS_BY_PROVIDER.get(provider_name, []))
    label = rows.get(row_key)
    if label is None:
        return None
    if not is_provider_enabled(provider_name):
        return None

    provider = get_provider(provider_name, **get_provider_kwargs(provider_name))
    if not provider.is_available():
        return None
    audio_only = provider_name == "hardcover" and content_type == "audiobook"

    if provider_name == "hardcover":
        variant = "audio" if audio_only else "all"
        base_key = f"discover:hardcover:{row_key}:{variant}"
    else:
        # Region changes must not serve the old storefront (stale included).
        base_key = f"discover:audible:{provider.tld}:{row_key}"
    fresh_key = f"{base_key}:fresh"
    last_good_key = f"{base_key}:last_good"

    def _row(books: list[BookMetadata], *, stale: bool) -> DiscoverRow:
        return DiscoverRow(
            key=row_key, label=label, provider=provider_name, books=books, stale=stale
        )

    cache = get_metadata_cache()
    cached = cache.get(fresh_key)
    if cached is not None:
        return _row(cast("list[BookMetadata]", cached), stale=False)

    with _lock_for(base_key):
        cached = cache.get(fresh_key)
        if cached is not None:
            return _row(cast("list[BookMetadata]", cached), stale=False)

        books = _fetch(provider, provider_name, row_key, audio_only=audio_only)
        if books is None:
            stale_books = cache.get(last_good_key)
            if stale_books is not None:
                logger.warning("Discover %s: provider failed, serving stale", base_key)
                return _row(cast("list[BookMetadata]", stale_books), stale=True)
            logger.warning("Discover %s: provider failed, no stale data", base_key)
            return _row([], stale=False)

        cache.set(fresh_key, books, ROW_TTLS[row_key])
        cache.set(last_good_key, books, LAST_GOOD_TTL)
        return _row(books, stale=False)
```

(`is_provider_enabled` lives at `shelfmark/metadata_providers/__init__.py:512`;
if it is not re-exported in `__all__`, import it anyway — the module has no
`__all__` gate — or add it to the import list shown.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_discover_service.py -x --tb=short`
Expected: PASS

- [ ] **Step 5: Regression suite**

Run: `uv run pytest tests/ -x --tb=short -m "not integration and not e2e"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add shelfmark/core/discover.py tests/core/test_discover_service.py
git commit -m "feat(discover): discover service with gating and serve-stale cache"
```

---

### Task 4: Endpoint + settings toggle + config exposure

**Files:**
- Modify: `shelfmark/main.py` (new route near `api_metadata_search`; config payload dict ~line 1183)
- Modify: `shelfmark/config/settings.py` (`search_mode_settings()`, after the `SHOW_COMBINED_SELECTOR` field ~line 482)
- Test: `tests/core/test_discover_endpoint.py` (new; Flask test client with `get_auth_mode` patched — no e2e file changes in this task)

**Interfaces:**
- Consumes: `get_discover_row`, `ROWS_BY_PROVIDER` (Task 3); existing `login_required` (main.py:810 — checks `get_auth_mode()` FIRST, then session `user_id`), `get_session_db_user_id`, `app_config`, `transform_cover_url`, `asdict`.
- Produces: `GET /api/discover?content_type=&row=` (JSON: `{row, label, provider, stale, books}` | `{row, books: []}` | 400 | 401 | 404), settings key `SHOW_DISCOVER_ROWS`, config payload key `show_discover_rows`. main.py imports the service as `get_discover_row_service` (patch target for tests).

- [ ] **Step 1: Add the settings field**

In `shelfmark/config/settings.py`, inside `search_mode_settings()` directly after the
`SHOW_COMBINED_SELECTOR` CheckboxField (~line 489), add:

```python
        CheckboxField(
            key="SHOW_DISCOVER_ROWS",
            label="Show Discover Rows",
            description=(
                "Show trending and new-release rows on the home page before a "
                "search. Sourced from Hardcover or Audible depending on the "
                "configured metadata provider."
            ),
            default=True,
            show_when={"field": "SEARCH_MODE", "value": "universal"},
        ),
```

- [ ] **Step 2: Expose in /api/config**

In `shelfmark/main.py` config payload (directly after `"show_combined_selector"`,
~line 1185), add:

```python
            "show_discover_rows": app_config.get("SHOW_DISCOVER_ROWS", True),
```

- [ ] **Step 3: Write the failing endpoint tests**

Create `tests/core/test_discover_endpoint.py`. `login_required` (main.py:810)
checks `get_auth_mode()` first — `"none"` bypasses everything; any other mode
requires `session["user_id"]`. Patch `get_auth_mode` on the `shelfmark.main`
module:

```python
"""Tests for GET /api/discover: auth, parameter validation, toggle gating."""

from unittest.mock import patch

import pytest

import shelfmark.main as main_module
from shelfmark.core.discover import DiscoverRow
from shelfmark.metadata_providers import BookMetadata


@pytest.fixture
def client():
    main_module.app.config["TESTING"] = True
    with main_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def no_auth():
    with patch.object(main_module, "get_auth_mode", return_value="none"):
        yield


def _row() -> DiscoverRow:
    return DiscoverRow(
        key="trending",
        label="Trending",
        provider="hardcover",
        books=[BookMetadata(provider="hardcover", provider_id="1", title="Book")],
        stale=False,
    )


class TestDiscoverEndpointAuth:
    def test_unauthenticated_returns_401(self, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        assert resp.status_code == 401


class TestDiscoverEndpoint:
    def test_toggle_off_returns_404(self, client, no_auth):
        with patch.object(main_module, "app_config") as cfg:
            cfg.get.side_effect = lambda key, default=None, **kw: (
                False if key == "SHOW_DISCOVER_ROWS" else default
            )
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        assert resp.status_code == 404

    def test_invalid_content_type_returns_400(self, client, no_auth):
        resp = client.get("/api/discover?content_type=magazine&row=trending")
        assert resp.status_code == 400

    def test_unknown_row_returns_400(self, client, no_auth):
        resp = client.get("/api/discover?content_type=ebook&row=bogus")
        assert resp.status_code == 400

    def test_unavailable_provider_returns_empty_row(self, client, no_auth):
        with patch.object(main_module, "get_discover_row_service", return_value=None):
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        assert resp.status_code == 200
        assert resp.get_json() == {"row": "trending", "books": []}

    def test_success_serializes_books(self, client, no_auth):
        with patch.object(main_module, "get_discover_row_service", return_value=_row()):
            resp = client.get("/api/discover?content_type=ebook&row=trending")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["row"] == "trending"
        assert data["label"] == "Trending"
        assert data["provider"] == "hardcover"
        assert data["stale"] is False
        assert data["books"][0]["title"] == "Book"
```

Two patch-target caveats for the implementer: (a) if `app_config` in main.py is a
module-level import used as `app_config.get(...)`, `patch.object(main_module,
"app_config")` works as shown; (b) the toggle-off test's `cfg.get` side-effect must
not break other config reads inside the request — if the route reads more config
keys than `SHOW_DISCOVER_ROWS`, return `default` for them (the lambda shown already
does). If importing `shelfmark.main` at module scope has side effects that break
test collection, mirror how the closest existing test imports it (search for an
existing `from shelfmark.main import` in tests/).

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_discover_endpoint.py -x --tb=short`
Expected: FAIL (404 route-not-found on the 200-path tests; the 401 test may
already pass — that's fine)

- [ ] **Step 5: Implement the route**

In `shelfmark/main.py`, near `api_metadata_search`:

```python
from shelfmark.core.discover import ROWS_BY_PROVIDER
from shelfmark.core.discover import get_discover_row as get_discover_row_service

_DISCOVER_CONTENT_TYPES = {"ebook", "audiobook", "combined"}
_DISCOVER_ROW_KEYS = {key for rows in ROWS_BY_PROVIDER.values() for key, _ in rows}


@app.route("/api/discover", methods=["GET"])
@login_required
def api_discover() -> Response | tuple[Response, int]:
    """Return one discover row for the landing page."""
    if not app_config.get("SHOW_DISCOVER_ROWS", True):
        return jsonify({"error": "Discover rows are disabled"}), 404

    content_type = request.args.get("content_type", "ebook").strip().lower()
    row_key = request.args.get("row", "").strip().lower()
    if content_type not in _DISCOVER_CONTENT_TYPES:
        return jsonify({"error": f"Invalid content_type: {content_type}"}), 400
    if row_key not in _DISCOVER_ROW_KEYS:
        return jsonify({"error": f"Invalid row: {row_key}"}), 400

    db_user_id = get_session_db_user_id(session)
    row = get_discover_row_service(content_type, row_key, user_id=db_user_id)
    if row is None:
        return jsonify({"row": row_key, "books": []})

    books_data = [asdict(book) for book in row.books]
    for book_dict in books_data:
        if book_dict.get("cover_url"):
            cache_id = f"{book_dict['provider']}_{book_dict['provider_id']}"
            book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)

    return jsonify(
        {
            "row": row.key,
            "label": row.label,
            "provider": row.provider,
            "stale": row.stale,
            "books": books_data,
        }
    )
```

Check how `api_metadata_search` (main.py ~2621) imports `asdict` and
`transform_cover_url` — inline-in-function or module-level — and match that
convention. Keep the `get_discover_row_service` alias exactly as shown (the
tests patch it on `shelfmark.main`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_discover_endpoint.py -x --tb=short`
Expected: PASS

- [ ] **Step 7: Regression suite**

Run: `uv run pytest tests/ -x --tb=short -m "not integration and not e2e"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add shelfmark/main.py shelfmark/config/settings.py tests/core/test_discover_endpoint.py
git commit -m "feat(discover): /api/discover endpoint and SHOW_DISCOVER_ROWS toggle"
```

---

### Task 5: Frontend data layer (API client, rows map, row-state helpers, config type)

**Files:**
- Modify: `src/frontend/src/services/api.ts` (API const ~line 43; new function near `getMetadataProviders` ~line 337)
- Create: `src/frontend/src/utils/discoverRows.ts`
- Modify: `src/frontend/src/types/index.ts` (`AppConfig` interface ~line 266)
- Test: `src/frontend/src/tests/discoverRows.test.ts` (new)

**Interfaces:**
- Consumes: existing `fetchJSON`, `MetadataBookData` (utils/bookTransformers.ts:8), `Book`.
- Produces: `getDiscoverRow(contentType: string, row: string): Promise<DiscoverRowResponse>`; from `discoverRows.ts`: `DiscoverRowDef {key,label}`, `getDiscoverRowsForProvider(provider: string | null): DiscoverRowDef[]`, `DiscoverRowState {key,label,books: Book[] | null}`, `initialRowStates(defs)`, `applyRowResponse(rows, key, label, books)`, `applyRowError(rows, key)`, `visibleRows(rows)`; `AppConfig.show_discover_rows: boolean`.

There is no DOM-testing library in this frontend (vitest only, plain `.test.ts`),
so the row-state logic the spec wants tested lives here as pure functions; Task 6's
component is a thin shell over them.

- [ ] **Step 1: Write the failing test**

Create `src/frontend/src/tests/discoverRows.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';

import type { Book } from '../types';
import {
  applyRowError,
  applyRowResponse,
  getDiscoverRowsForProvider,
  initialRowStates,
  visibleRows,
} from '../utils/discoverRows';

const book = (id: string): Book => ({ id, title: `Book ${id}`, author: 'A' }) as Book;

describe('getDiscoverRowsForProvider', () => {
  it('returns trending + new releases for hardcover', () => {
    expect(getDiscoverRowsForProvider('hardcover').map((r) => r.key)).toEqual([
      'trending',
      'new_releases',
    ]);
  });

  it('returns best sellers + new releases for audible', () => {
    expect(getDiscoverRowsForProvider('audible').map((r) => r.key)).toEqual([
      'best_sellers',
      'new_releases',
    ]);
  });

  it('returns empty for other providers and null', () => {
    expect(getDiscoverRowsForProvider('openlibrary')).toEqual([]);
    expect(getDiscoverRowsForProvider('googlebooks')).toEqual([]);
    expect(getDiscoverRowsForProvider(null)).toEqual([]);
  });
});

describe('row state transitions', () => {
  const defs = getDiscoverRowsForProvider('hardcover');

  it('starts all rows loading (books null)', () => {
    expect(initialRowStates(defs).every((r) => r.books === null)).toBe(true);
  });

  it('applies a response to only the matching row, keeping label fallback', () => {
    const rows = applyRowResponse(initialRowStates(defs), 'trending', undefined, [book('1')]);
    expect(rows[0].books).toHaveLength(1);
    expect(rows[0].label).toBe('Trending'); // fallback kept when response has no label
    expect(rows[1].books).toBeNull(); // other row untouched — rows load independently
  });

  it('marks an errored row as empty', () => {
    const rows = applyRowError(initialRowStates(defs), 'trending');
    expect(rows[0].books).toEqual([]);
    expect(rows[1].books).toBeNull();
  });

  it('hides loaded-empty rows, keeps loading and non-empty rows', () => {
    let rows = initialRowStates(defs);
    rows = applyRowResponse(rows, 'trending', 'Trending', []);
    expect(visibleRows(rows).map((r) => r.key)).toEqual(['new_releases']); // still loading
    rows = applyRowResponse(rows, 'new_releases', 'New Releases', [book('1')]);
    expect(visibleRows(rows).map((r) => r.key)).toEqual(['new_releases']);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/frontend && npm run test:unit -- src/tests/discoverRows.test.ts`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement**

Create `src/frontend/src/utils/discoverRows.ts`:

```typescript
import type { Book } from '../types';

export interface DiscoverRowDef {
  key: string;
  label: string;
}

export interface DiscoverRowState {
  key: string;
  label: string;
  books: Book[] | null; // null = still loading
}

// Which discover rows each metadata provider supports. Mirrors
// ROWS_BY_PROVIDER in shelfmark/core/discover.py — keep in sync.
export const DISCOVER_ROWS_BY_PROVIDER: Record<string, DiscoverRowDef[]> = {
  hardcover: [
    { key: 'trending', label: 'Trending' },
    { key: 'new_releases', label: 'New Releases' },
  ],
  audible: [
    { key: 'best_sellers', label: 'Best Sellers' },
    { key: 'new_releases', label: 'New Releases' },
  ],
};

export const getDiscoverRowsForProvider = (provider: string | null): DiscoverRowDef[] =>
  provider ? (DISCOVER_ROWS_BY_PROVIDER[provider] ?? []) : [];

export const initialRowStates = (defs: DiscoverRowDef[]): DiscoverRowState[] =>
  defs.map((def) => ({ key: def.key, label: def.label, books: null }));

export const applyRowResponse = (
  rows: DiscoverRowState[],
  key: string,
  label: string | undefined,
  books: Book[],
): DiscoverRowState[] =>
  rows.map((row) => (row.key === key ? { ...row, label: label ?? row.label, books } : row));

export const applyRowError = (rows: DiscoverRowState[], key: string): DiscoverRowState[] =>
  rows.map((row) => (row.key === key ? { ...row, books: [] } : row));

/** Rows worth rendering: still loading (skeleton) or loaded with books. */
export const visibleRows = (rows: DiscoverRowState[]): DiscoverRowState[] =>
  rows.filter((row) => row.books === null || row.books.length > 0);
```

In `src/frontend/src/services/api.ts`: add to the `API` const:

```typescript
  discover: `${API_BASE}/discover`,
```

and near `getMetadataProviders`:

```typescript
export interface DiscoverRowResponse {
  row: string;
  label?: string;
  provider?: string;
  stale?: boolean;
  books: MetadataBookData[];
}

export const getDiscoverRow = async (
  contentType: string,
  row: string,
): Promise<DiscoverRowResponse> => {
  const params = new URLSearchParams({ content_type: contentType, row });
  return fetchJSON<DiscoverRowResponse>(`${API.discover}?${params.toString()}`);
};
```

`MetadataBookData` comes from `utils/bookTransformers.ts` — add
`import type { MetadataBookData } from '../utils/bookTransformers';` with api.ts's
existing type imports (export it from bookTransformers.ts if it isn't already).

In `src/frontend/src/types/index.ts`, add to `AppConfig` after
`show_combined_selector`:

```typescript
  show_discover_rows: boolean;
```

- [ ] **Step 4: Run test + typecheck**

Run: `cd src/frontend && npm run test:unit -- src/tests/discoverRows.test.ts && npm run typecheck`
Expected: test PASS; typecheck may fail where `AppConfig` objects are constructed in
tests/mocks without the new field — add `show_discover_rows: true` to those fixtures.
Re-run until clean.

- [ ] **Step 5: Commit**

```bash
git add src/frontend/src/utils/discoverRows.ts src/frontend/src/services/api.ts src/frontend/src/types/index.ts src/frontend/src/tests/discoverRows.test.ts
git commit -m "feat(discover): frontend data layer and row-state helpers"
```

---

### Task 6: DiscoverSection component + App wiring

**Files:**
- Create: `src/frontend/src/components/DiscoverSection.tsx`
- Modify: `src/frontend/src/App.tsx` (details handler for discover books; render below `SearchSection` in initial state)
- Test: manual verification + `npm run typecheck` + `npm run lint` + full `npm run test:unit` (row-state logic already covered in Task 5)

**Interfaces:**
- Consumes: `getDiscoverRow` + helpers (Task 5); `transformMetadataToBook` (bookTransformers.ts:77); `useLibraryMatches` (hooks/useLibraryMatches.ts — returns a record keyed by `book.id`); `isBookRequested` (utils/requestedBooks.ts:67); shared badges `InLibraryBadge` (components/shared/InLibraryBadge.tsx, props `{match, className?, variant?}`) and `RequestedBadge` (components/shared/RequestedBadge.tsx, props `{className?, variant?}`); App: `getUniversalActionButtonState(bookId: string): ButtonStateInfo` (App.tsx:1937), `openRequestKeys` (App.tsx:2376), config/provider state.
- Produces: `<DiscoverSection contentType providerName openRequestKeys getButtonState onDetails />` where `getButtonState: (bookId: string) => ButtonStateInfo` and `onDetails: (book: Book) => void` (takes the full Book — see App wiring; `handleShowDetails(id)` only resolves ids present in search-results state and MUST NOT be passed here).

- [ ] **Step 1: Create the component**

`src/frontend/src/components/DiscoverSection.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';

import { useLibraryMatches } from '../hooks/useLibraryMatches';
import { getDiscoverRow } from '../services/api';
import type { Book, ButtonStateInfo, ContentType } from '../types';
import { transformMetadataToBook } from '../utils/bookTransformers';
import type { DiscoverRowState } from '../utils/discoverRows';
import {
  applyRowError,
  applyRowResponse,
  getDiscoverRowsForProvider,
  initialRowStates,
  visibleRows,
} from '../utils/discoverRows';
import { isBookRequested } from '../utils/requestedBooks';
import { InLibraryBadge, RequestedBadge } from './shared';

interface DiscoverSectionProps {
  contentType: ContentType | 'combined';
  providerName: string | null;
  openRequestKeys: Set<string>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  onDetails: (book: Book) => void;
}

const ACTIVE_STATES = new Set(['queued', 'resolving', 'locating', 'downloading', 'complete']);

interface DiscoverTileProps {
  book: Book;
  buttonState: ButtonStateInfo;
  requested: boolean;
  libraryMatch: ReturnType<typeof useLibraryMatches>[string] | undefined;
  onDetails: (book: Book) => void;
}

const DiscoverTile = ({
  book,
  buttonState,
  requested,
  libraryMatch,
  onDetails,
}: DiscoverTileProps) => {
  const [imageError, setImageError] = useState(false);

  return (
    <button
      type="button"
      onClick={() => onDetails(book)}
      className="w-32 flex-none snap-start text-left"
      title={book.title}
    >
      <div className="relative">
        {book.preview && !imageError ? (
          <img
            src={book.preview}
            alt={book.title}
            loading="lazy"
            onError={() => setImageError(true)}
            className="h-48 w-32 rounded-lg object-cover shadow-sm"
          />
        ) : (
          <div className="flex h-48 w-32 items-center justify-center rounded-lg bg-(--bg-soft) p-2 text-center text-xs opacity-70">
            {book.title}
          </div>
        )}
        <div className="absolute top-1 right-1 flex flex-col items-end gap-1">
          {libraryMatch && <InLibraryBadge match={libraryMatch} variant="overlay" />}
          {requested && <RequestedBadge variant="overlay" />}
          {ACTIVE_STATES.has(buttonState.state) && (
            <span className="rounded-sm bg-black/70 px-1.5 py-0.5 text-xs text-white">
              {buttonState.text}
            </span>
          )}
        </div>
      </div>
      <div className="mt-1 truncate text-sm">{book.title}</div>
      <div className="truncate text-xs opacity-70">{book.author || ''}</div>
    </button>
  );
};

export const DiscoverSection = ({
  contentType,
  providerName,
  openRequestKeys,
  getButtonState,
  onDetails,
}: DiscoverSectionProps) => {
  const [rows, setRows] = useState<DiscoverRowState[]>([]);

  useEffect(() => {
    const rowDefs = getDiscoverRowsForProvider(providerName);
    if (rowDefs.length === 0) {
      setRows([]);
      return undefined;
    }
    let cancelled = false;
    setRows(initialRowStates(rowDefs));

    rowDefs.forEach((def) => {
      void getDiscoverRow(contentType, def.key)
        .then((response) => {
          if (cancelled) return;
          const books = response.books.map(transformMetadataToBook);
          setRows((current) => applyRowResponse(current, def.key, response.label, books));
        })
        .catch(() => {
          if (cancelled) return;
          setRows((current) => applyRowError(current, def.key));
        });
    });

    return () => {
      cancelled = true;
    };
  }, [contentType, providerName]);

  const allBooks = useMemo(() => rows.flatMap((row) => row.books ?? []), [rows]);
  // Record keyed by book.id (buildLibraryLookupPayload uses book.id).
  const libraryMatches = useLibraryMatches(allBooks);

  const rendered = visibleRows(rows);
  if (rendered.length === 0) {
    return null;
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-4">
      {rendered.map((row) => (
        <section key={row.key} className="mb-8" aria-label={row.label}>
          <h2 className="mb-3 text-lg font-semibold">{row.label}</h2>
          {row.books === null ? (
            <div className="flex gap-4 overflow-hidden">
              {Array.from({ length: 6 }, (_, i) => (
                <div
                  key={i}
                  className="h-48 w-32 flex-none animate-pulse rounded-lg bg-(--bg-soft)"
                />
              ))}
            </div>
          ) : (
            <div className="flex snap-x gap-4 overflow-x-auto pb-2">
              {row.books.map((book) => (
                <DiscoverTile
                  key={book.id}
                  book={book}
                  buttonState={getButtonState(book.id)}
                  requested={isBookRequested(book, openRequestKeys)}
                  libraryMatch={libraryMatches[book.id]}
                  onDetails={onDetails}
                />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
};
```

Verify before finishing (conventions, not guesses): badge prop shapes at their
definitions (`InLibraryBadge.tsx:4`, `RequestedBadge.tsx`), the Tailwind
custom-property class syntax used in `CardView.tsx` (`bg-(--bg-soft)` etc.), and
the `LibraryMatch` type import if the inline `ReturnType` indexing displeases
the linter (`import type { LibraryMatch } from '../utils/libraryMatches'`).

- [ ] **Step 2: Add a discover details handler and wire into App.tsx**

`handleShowDetails(id)` resolves ids against the `books` search-results array and
error-toasts on a miss (App.tsx:949–983), so discover books need a handler that
takes the `Book` directly. Add next to `handleShowDetails`, reusing its metadata
branch verbatim:

```tsx
  // Discover tiles hold Books that are not in search-results state, so details
  // enrichment takes the Book itself instead of an id looked up in `books`.
  const handleShowDiscoverDetails = async (book: Book): Promise<void> => {
    // Book.provider/provider_id are optional; isMetadataBook (types/index.ts:355)
    // narrows them to string. Discover books always satisfy it in practice.
    if (!isMetadataBook(book)) {
      showToast('Failed to load book details', 'error');
      return;
    }
    try {
      const fullBook = await getMetadataBookInfo(book.provider, book.provider_id);
      setSelectedBook({
        ...book,
        description: fullBook.description || book.description,
        series_id: fullBook.series_id || book.series_id,
        series_name: fullBook.series_name,
        series_position: fullBook.series_position,
        series_count: fullBook.series_count,
      });
    } catch (error) {
      console.error('Failed to load book description, using discover data:', error);
      setSelectedBook(book);
    }
  };
```

(Only the metadata branch of `handleShowDetails` is mirrored — discover books
always come from a metadata provider. `isMetadataBook` is already imported in
App.tsx for `handleShowDetails`; `showToast` is in scope.)

Then render inside the main content area, directly after the `SearchSection`
block (~App.tsx:2536):

```tsx
          {isInitialState &&
            isAuthenticated &&
            config?.show_discover_rows &&
            effectiveSearchMode === 'universal' && (
              <DiscoverSection
                contentType={effectiveCombinedMode ? 'combined' : effectiveContentType}
                providerName={
                  effectiveCombinedMode
                    ? configuredCombinedMetadataProvider
                    : effectiveContentType === 'audiobook'
                      ? (configuredAudiobookMetadataProvider ?? configuredMetadataProvider)
                      : configuredMetadataProvider
                }
                openRequestKeys={openRequestKeys}
                getButtonState={getUniversalActionButtonState}
                onDetails={(book) => void handleShowDiscoverDetails(book)}
              />
            )}
```

Add the import: `import { DiscoverSection } from './components/DiscoverSection';`

Identifier names were verified against the `ResultsSection` call site
(App.tsx:2571): `getUniversalActionButtonState`, `openRequestKeys`. Confirm
`effectiveSearchMode` and `effectiveCombinedMode` are the in-scope names near the
render site (they appear in the `loadConfig` area); the audiobook fallback to
`configuredMetadataProvider` mirrors `get_configured_provider_name`'s
fallback-to-main behavior.

- [ ] **Step 3: Typecheck, lint, unit tests**

Run: `cd src/frontend && npm run typecheck && npm run lint && npm run test:unit`
Expected: all clean. Fix any `AppConfig` mock objects missing `show_discover_rows`.

- [ ] **Step 4: Build + manual verify**

Run: `cd src/frontend && npm run build`
Expected: build succeeds.

Manual verification (dev instance): landing page shows skeletons then two rows for
the configured provider; toggling content type swaps rows; **flip the content type
rapidly while rows are loading — no row from the previous type may appear
(cancellation is manual-verify by spec decision: same untested `cancelled`-flag
idiom as `useLibraryMatches`/`MetadataConfigSession`)**; clicking a tile opens
the details modal (book NOT in any search results — this exercises
`handleShowDiscoverDetails`); search hides the section; reset brings it back;
disabling "Show Discover Rows" in settings hides it (and `/api/discover` 404s);
a book already in the ABS library shows the in-library badge (overlay variant,
legible on cover art).

- [ ] **Step 5: Commit**

```bash
git add src/frontend/src/components/DiscoverSection.tsx src/frontend/src/App.tsx
git commit -m "feat(discover): landing-page discover section"
```

---

## Self-review notes

- Spec coverage: Component 1 → Tasks 1–2 (both Audible rows top up, per spec);
  Component 2 → Task 3 (incl. enabled/available gates, single-flight + TTL tests);
  Components 3–4 → Task 4 (incl. 401); Components 5–6 → Tasks 5–6 (row-state
  logic tested as pure helpers — no DOM test infra exists; shared badges reused).
- Codex-review deltas incorporated: callback signatures fixed against verified
  App.tsx code, plus the deeper find that `handleShowDetails` cannot serve
  discover books at all (new `handleShowDiscoverDetails`, guarded by
  `isMetadataBook` — its provider fields are optional on `Book`); endpoint tests
  patch `get_auth_mode`; partial Audible pages now fail the fetch; per-record
  parse errors skip records (documented in spec); `_key_locks` bounded-by-design
  comment; Task 4 file list made consistent; cover fallback mirrors CardView's
  `imageError` pattern.
- Codex re-review deltas: `isMetadataBook` narrowing made part of the prescribed
  handler; badges render `variant="overlay"` (CardView precedent, `./shared`
  barrel import); single-flight test uses `_SignallingLock` so it fails unless
  the second caller is provably blocked at the lock mid-fetch; cancellation
  declared manual-verify in the spec (matches the repo's untested
  `cancelled`-flag idiom) rather than adding a helper only tests would use.
- Type consistency: `DiscoverRow` fields match endpoint serialization (Task 4)
  and `DiscoverRowResponse` (Task 5); `get_discover_row_service` alias defined in
  Task 4 Step 5 and patched in Step 3 tests; `DiscoverRowState` helpers named
  identically in Tasks 5 and 6.
