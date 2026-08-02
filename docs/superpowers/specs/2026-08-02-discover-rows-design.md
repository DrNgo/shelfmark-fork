# Discover Rows on Landing Page — Design

Companion doc: `2026-08-02-discover-hardcover-api-notes.md` (API research, verified
queries, open items). Decisions were settled in a grilling interview on 2026-08-02
(see SESSION_STATE.md); this revision incorporates a Codex review of the first draft
(fetch-result contract, combined-mode resolution, stale-cache design, cache keys,
Audible browse filtering, overlay wiring).

## Goal

Give the landing page an Audible-home-screen feel: horizontally scrollable rows of
book-cover tiles ("Trending"/"Best Sellers", "New Releases") shown in the initial
pre-search state, respecting each user's allowed content types and dropping into the
existing book details → release/request flow on click.

## Settled decisions

1. Content sources: **Hardcover** (server-side token) for ebook rows and for
   audiobook rows when Hardcover is the audiobook provider; **Audible** (public
   catalog API) for audiobook rows when Audible is the audiobook provider. Generic
   rows, no personalization. Providers other than these two ⇒ no rows for that
   content type.
2. Exactly two rows per content type: popularity ("Trending" on Hardcover, "Best
   Sellers" on Audible) and "New Releases". Labels are per-source — no pretending
   they're the same metric.
3. Cache: `core/cache.py` `CacheService` used as-is (no semantic changes), popularity
   row TTL 6 h, new releases TTL 24 h, in-memory, serve-stale-on-error via a
   dual-entry scheme (§ Component 2), lazily populated. No scheduler.
4. Rows follow the existing content-type toggle (`effectiveContentType`); blocked
   content types are already hidden by `allowedContentTypes` (App.tsx). Combined
   mode shows one row set driven by the combined provider (§ Component 2).
5. Tiles are cover-first (cover with a compact title/author caption beneath, per the
   interview decision); click behaves exactly like clicking a search result.
   Per-user overlays (library match, requested, download state) are wired
   explicitly (§ Component 6) — they are per-view wiring in this codebase, not
   automatic.
6. Admin settings toggle, default ON. Fork-only, cheap two-branch dispatch — no
   provider abstraction.
7. "New" is **work-level** recency on Hardcover (`books.release_date`) and
   product-level on Audible (`issue_date`). A work first published years ago whose
   audiobook edition is new will NOT appear in Hardcover-backed new releases —
   accepted v1 tradeoff; edition-level windows are a v2 refinement.

## Approach

Backend: a discover service dispatches on the configured provider name for the
requested content type, fetches one row per request from Hardcover GraphQL or the
Audible catalog API, caches per (provider, region/variant, row), and serves through
one new authenticated endpoint using the search endpoint's serialization
(`asdict(BookMetadata)` + `transform_cover_url`, as in `api_metadata_search`).
Frontend: a `DiscoverSection` rendered in the initial state fires one request per
row, renders each row independently, wires the standard overlays itself, and feeds
clicks into the existing book-selection path.

### Fetch-result contract (drives serve-stale)

Every provider fetcher returns `list[BookMetadata] | None`:

- **`None` = failure** (transport error, timeout, HTTP error, GraphQL `errors`,
  `TrendingBookType.error` set, payload-shape failure such as a non-list `books`).
  Any failed page in a multi-page fetch fails the whole fetch — partial rows are
  never cached. Discover service falls back to the last-good cache entry.
- **`[]` = genuinely empty** (provider answered, zero qualifying books). Cached
  like any success; renders as a hidden row. Never triggers stale fallback.
- **Malformed individual records** inside a successful payload are skipped with a
  debug log — one bad book does not fail or empty the row.

This distinction is the reason fetchers do NOT reuse the providers' search methods,
which collapse errors into empty results (`_execute_query` → `None` → `[]` in
Hardcover search; `_search_cached` → `{}` in Audible).

## Components

### 1. Provider fetchers — `shelfmark/metadata_providers/hardcover.py`, `audible.py`

New public methods on each provider, honoring the fetch-result contract:

- `HardcoverProvider.discover_trending(limit, *, audio_only=False)` — two-step:
  `books_trending(from=today−30d, to=today, limit=3×limit)` → hydrate ids via
  `books(where: {id: {_in: ...}})` with the existing book-field fragment → re-order
  to match ids order → `_parse_book`. `audio_only` adds
  `default_audio_edition_id: {_is_null: false}` to the hydration `where`; the 3×
  id over-fetch is the top-up strategy — if the filtered row is still short of
  `limit`, render it short (no extra round-trip). Uses
  `_execute_query(..., raise_on_error=True)` and maps any raise to `None`;
  `TrendingBookType.error` set ⇒ `None`; empty `ids` ⇒ `[]`.
- `HardcoverProvider.discover_new_releases(limit, *, audio_only=False)` — single
  `books` query: `release_date` window (today−90d…today), `canonical_id` null,
  `state` in normalized/normalizing, `compilation` false, `users_count >= 10`,
  ordered `users_count desc_nulls_last, release_date desc` (API notes §4).
- `AudibleProvider.discover_best_sellers(limit)` /
  `AudibleProvider.discover_new_releases(limit)` — Audible has no shared transport
  helper (search does `session.get` inline inside `@cacheable _search_cached`), so
  these methods make their own `session.get(f"{self.base_url}/1.0/catalog/products",
  ...)` with the browse params (no keywords; `products_sort_by=BestSellers` /
  `-ReleaseDate`; existing `RESPONSE_GROUPS`/`image_sizes`). They must NOT go
  through `_search_cached` (double caching + error collapsing).
  **Browse filtering is explicit** — `_parse_product` only drops items missing
  asin/title, so before parsing, keep only products with `is_listenable is True`
  and `content_delivery_type in {"SinglePartBook", "MultiPartBook"}` (excludes
  podcasts/periodicals), and for new releases drop `issue_date > today`
  (preorders lead the `-ReleaseDate` sort). Fetch `MAX_RESULTS_PER_PAGE` (50) and
  top up from page 1 if the filtered row is short of `limit`; two pages max.
  Request exception/timeout/bad JSON ⇒ `None`.

### 2. Discover service — `shelfmark/core/discover.py` (new)

- `ROWS_BY_PROVIDER = {"hardcover": [("trending", "Trending"), ("new_releases", "New Releases")], "audible": [("best_sellers", "Best Sellers"), ("new_releases", "New Releases")]}`
- `get_discover_row(content_type, row_key, user_id) -> DiscoverRow | None`.
  **Provider resolution goes through `get_configured_provider_name(content_type,
  user_id)`** — the only resolver that understands `"combined"`
  (`get_configured_provider()` does not; passing it "combined" silently returns
  the ebook provider) — then instantiates via
  `get_provider(name, **get_provider_kwargs(name))`, mirroring
  `api_metadata_providers`. Gates before fetching: `is_provider_enabled(name)`
  and `provider.is_available()` must both hold (resolution-by-name alone skips
  the enabled check that `get_configured_provider()` performs). Unknown/disabled/
  unavailable provider or unknown row ⇒ `None` (endpoint renders empty row).
  `content_type == "combined"` uses the ebook-shaped row variants
  (`audio_only=False`).
- **Cache keys include result-affecting provider config**:
  - Hardcover: `discover:hardcover:{row_key}:{variant}` where variant is
    `audio`/`all` (the `audio_only` flag — the only config dimension).
  - Audible: `discover:audible:{tld}:{row_key}` — region change orphans old keys
    instead of serving the wrong storefront (stale entries included).
- **Serve-stale via dual entries, `CacheService` untouched:** each successful fetch
  (including `[]`) writes two entries — `discover:fresh:<key>` with the row TTL and
  `discover:last_good:<key>` with a 7-day TTL. Reads: fresh hit ⇒ return; miss ⇒
  fetch; fetch returns `None` ⇒ return `last_good` if present (flagged
  `stale: true` in the response) else `None`. This survives `cleanup_expired()`
  and capacity eviction with existing semantics; the settings "clear metadata
  cache" action (`_clear_metadata_cache` → `cache.clear()`) clears stale entries
  too, which is acceptable — that button means "start over".
- **Single-flight:** a module-level `dict[key, threading.Lock]` guards each cache
  key; concurrent cold-cache requests for the same row serialize, losers re-read
  the cache after acquiring. Different rows still fetch in parallel.
- TTLs: `DISCOVER_POPULAR_TTL = 6*3600`, `DISCOVER_NEW_RELEASES_TTL = 24*3600`,
  `DISCOVER_LAST_GOOD_TTL = 7*24*3600`. Row size: 20.

### 3. Endpoint — `shelfmark/main.py`

`GET /api/discover?content_type=<ebook|audiobook|combined>&row=<row_key>` with
`@login_required`.

- `404` if the `SHOW_DISCOVER_ROWS` setting is off.
- `400` if `content_type` is not one of the three values, or `row` is missing/not a
  known row key for any provider.
- Valid request but provider unavailable/not discover-capable, or row genuinely
  empty ⇒ `200` with `{"row": key, "books": []}` — an empty row is a rendering
  no-op, not an error.
- Success: `{"row": key, "label": ..., "provider": name, "stale": bool,
  "books": [asdict(BookMetadata) + cover proxy]}` — book serialization copied from
  `api_metadata_search` (`asdict` + `transform_cover_url` with
  `{provider}_{provider_id}` cache ids so tiles hit `/api/covers`).

### 4. Settings toggle

CheckboxField `SHOW_DISCOVER_ROWS`, default `True`, label "Show Discover Rows"
(Title Case per the tab's existing labels), registered in the existing **`search_mode` tab**
(`shelfmark/config/settings.py`, `search_mode_settings()`) next to
`SHOW_COMBINED_SELECTOR` — the tab that owns search-page presentation. Exposed in
`/api/config` as `show_discover_rows` (AppConfig payload in `main.py` +
`AppConfig` interface in `types/index.ts`).

### 5. Frontend gating + data — `src/frontend/src/components/DiscoverSection.tsx` (new)

- Rendered by `App.tsx` inside the initial state (alongside the
  `search-initial-state` block) when: authenticated, config loaded,
  `config.show_discover_rows`, and the active provider name is in a frontend
  `ROWS_BY_PROVIDER` map. The active provider name comes from state App already
  holds: `configuredCombinedMetadataProvider` when combined mode is on, else
  `configuredAudiobookMetadataProvider` / `configuredMetadataProvider` per
  `effectiveContentType`. No new discovery endpoint needed.
- One `GET /api/discover` request per row, fired concurrently; each row renders a
  skeleton, then tiles, independently. Empty row ⇒ row not rendered; all rows
  empty ⇒ section not rendered.
- Content-type toggle flips ⇒ refetch; in-flight responses for a stale content
  type are discarded (same cancellation pattern as `MetadataConfigSession`).

### 6. Tile overlays + click-through — explicit wiring

Overlays in this codebase are per-view wiring (ResultsSection/CardView receive
them as props), so `DiscoverSection` wires its own, reusing the same primitives:

- **Library badge:** `useLibraryMatches(rowBooks)` (hooks/useLibraryMatches.ts) —
  self-contained batch lookup, already used the same way by `DetailsModal` and
  `ActivityCard`; failed lookups degrade to no badge by design.
- **Requested badge:** `isBookRequested(book, openRequestKeys)`
  (utils/requestedBooks.ts) with `openRequestKeys` passed down from App (same
  memo that feeds ResultsSection).
- **Download state:** `getUniversalButtonState` passed down from App
  (useDownloadTracking), rendered as a compact badge (queued/downloading/done),
  not the full button.
- **Click:** row books are converted with the existing metadata→`Book`
  transformer (`bookTransformers.ts`) at fetch time, so tiles hold real `Book`
  objects and clicking calls the same selection handler search results use — the
  details modal, release resolution, request policy, and on-behalf-of flows are
  inherited from that point on.

## Error handling summary

| Failure | Behavior |
|---|---|
| Provider API down/timeout/GraphQL error | Fetcher returns `None` ⇒ serve `last_good` (flagged stale) if present, else empty row |
| Row genuinely empty | `[]` cached as success; row hidden; NO stale fallback |
| Hardcover token expired (Jan 1) | Same as API-down; admin sees connection status in existing Hardcover settings panel |
| Toggle off | Endpoint 404s; frontend never calls it (config flag) |
| Invalid content_type/row param | 400 |
| Provider is OpenLibrary/GoogleBooks | 200 empty row; section hidden |
| Audible region changed | New cache key (tld in key); old entries orphaned, never served |
| Podcasts/non-listenable in Audible browse | Filtered explicitly in fetcher (`is_listenable`, `content_delivery_type`) — `_parse_product` does NOT filter these |
| Concurrent cold-cache requests | Per-key single-flight lock; one upstream fetch |

## Testing

- `tests/metadata/test_hardcover_discover.py`: trending two-step (id order
  preserved; `TrendingBookType.error` ⇒ `None`; empty ids ⇒ `[]`; transport error
  ⇒ `None`), new-release filter construction, audio-only variant + short-row
  behavior.
- `tests/metadata/test_audible_discover.py`: browse param construction,
  content-type/listenable filtering, preorder filtering + page-1 top-up, region in
  URL, error ⇒ `None` vs no-results ⇒ `[]`.
- `tests/core/test_discover_service.py`: dispatch via
  `get_configured_provider_name` including `combined`; unknown provider/row ⇒
  `None`; fresh-hit/miss/stale-fallback matrix (`None` vs `[]` fetch results);
  dual-entry TTLs; region/variant in cache keys; single-flight under concurrent
  calls.
- Endpoint tests per `tests/e2e/test_api.py` patterns: auth required, toggle-off
  404, param validation 400s, combined resolution, serialization shape, `stale`
  flag.
- Frontend: provider→rows map gating and row-state transitions as pure-helper
  unit tests (no DOM-test infra exists). Request-cancellation on content-type
  flip and carousel rendering are manual-verify: the cancellation code is the
  same untested `cancelled`-flag effect idiom `useLibraryMatches` and
  `MetadataConfigSession` already use, and adding a generation-counter helper
  solely to unit-test it would complicate the shipped code.

## Out of scope (v2 candidates)

- Genre rows, admin-curated featured lists, personalized rows (Hardcover "Want to
  Read"), OpenLibrary/GoogleBooks sources, disk-persisted or scheduler-warmed
  cache, per-user visibility preference, edition-level new-release windows,
  automatic cache invalidation on arbitrary settings changes (region is handled
  via keys; anything else waits for TTL).
