# Hardcover + Audible API investigation — Discover rows (Trending / New Releases)

Research notes feeding the discover-rows design spec. Sources: the fork's own
`shelfmark/metadata_providers/hardcover.py` and `audible.py` (production-proven
queries), the full GraphQL schema published at
`hardcoverapp/hardcover-docs:schema.graphql`, `docs.hardcover.app` Getting Started,
and live probes of the unauthenticated Audible catalog API. Verified 2026-08-02.

Sections 1–7: Hardcover. Section 8: Audible (for audiobook rows when the audiobook
provider is Audible).

## 1. Endpoint, auth, transport (existing plumbing — reuse as-is)

- Endpoint: `https://api.hardcover.app/v1/graphql` (`HARDCOVER_API_URL`, hardcover.py:45).
- Auth: `Authorization: Bearer <token>`; token from `app_config.get("HARDCOVER_API_KEY")`
  via `_hardcover_kwargs()` (hardcover.py:908–911). Server-side only — Hardcover forbids
  browser-side calls, which our backend-endpoint design already satisfies.
- Execution: `HardcoverProvider._execute_query(query, variables, raise_on_error=False)`
  (hardcover.py:2590) — returns `data` dict or `None` on any error; logs GraphQL errors.
  Discover code should call with `raise_on_error=False` and treat `None` as
  "row unavailable" (feeds serve-stale).

## 2. Operational constraints (per docs.hardcover.app Getting Started — doc claims, not live-verified except where noted)

| Constraint | Value | Impact on discover |
|---|---|---|
| Rate limit | 60 req/min | Trivial at our cache TTLs (≤ ~10 calls/day/row-set) |
| Query timeout | 30 s server-side | Our client timeout is 15 s (`_execute_query`) — fine |
| Max query depth | "3" (2025 rule) | **Docs contradict reality**: existing prod queries nest `lists → list_books → book → contributions → author` and work. Depth counting is evidently looser than the docs' wording. Reuse existing nesting shapes; don't design around depth 3. |
| Token expiry | Auto-expires after 1 year; resets Jan 1 | Landing rows will silently go stale/empty when token dies → 401 "Unable to verify token". Serve-stale masks this; consider surfacing token errors in the admin settings status line (existing pattern shows connected username). |
| Throttle response | HTTP 429 `{error: "Throttled"}` | Handled by `_execute_query` HTTPError path → `None` |

## 3. Row 1 — Trending: `books_trending`

Schema (root query field, schema.graphql:13821):

```graphql
books_trending(from: date!, to: date!, limit: Int!, offset: Int!): TrendingBookType

type TrendingBookType {
  error: String
  ids: [Int]      # book IDs only — NO book data
}
```

**Key fact: trending returns bare IDs.** The row is a two-step fetch (2 API calls):

```graphql
# Step 1 — ranked ids for a popularity window
query DiscoverTrending($from: date!, $to: date!, $limit: Int!) {
  books_trending(from: $from, to: $to, limit: $limit, offset: 0) {
    error
    ids
  }
}

# Step 2 — hydrate, then re-order in Python to match the ids order
query DiscoverTrendingBooks($ids: [Int!]!) {
  books(where: {id: {_in: $ids}}) {
    ...bookFields   # section 5
  }
}
```

- Window: recommend `from = today − 30d`, `to = today` (matches "trending this month"
  feel). Window choice is ours; the API just takes the range.
- `_in` hydration does NOT preserve order — re-sort by `ids` index server-side.
- `error` field inside the payload (not GraphQL `errors`) must be checked too.
- **Assumption to verify live at implementation** (needs the instance token; docs are
  silent): ids are returned ranked most-trending-first. Sanity-check against
  hardcover.app/trending. If unranked, fall back to ordering hydrated books by
  `users_count desc`.

## 4. Row 2 — New Releases: `books` root query

All needed filter/order fields confirmed in schema (`books_bool_exp` schema.graphql:3532,
`books_order_by` :3788): `release_date: date_comparison_exp`, `users_count:
Int_comparison_exp`, `canonical_id`, `state`, `compilation`,
`default_audio_edition_id`, `default_ebook_edition_id`, plus `order_by` on
`users_count`, `release_date`, `rating`, `ratings_count`, `activities_count`.

Proposed query (single call):

```graphql
query DiscoverNewReleases($from: date!, $to: date!, $limit: Int!) {
  books(
    where: {
      release_date: {_gte: $from, _lte: $to},   # e.g. today−90d … today
      canonical_id: {_is_null: true},            # dedupe: skip merged duplicates
      state: {_in: ["normalized", "normalizing"]}, # skip junk/pending records
      compilation: {_eq: false},                 # skip box sets/omnibuses
      users_count: {_gte: 10}                    # quality floor — tune with real data
    },
    order_by: [{users_count: desc_nulls_last}, {release_date: desc}],
    limit: $limit
  ) {
    ...bookFields
  }
}
```

- The `canonical_id` / `state` / `compilation` filter trio is copied from the fork's
  production `AUTHOR_BOOKS_BY_ID_QUERY` (hardcover.py:398) — proven pattern.
- Ordering by `users_count desc` (not `release_date desc`) is deliberate: "notable new
  releases" not "newest 20 rows in their DB". The `users_count` floor value and the
  90-day window are tunables to validate against live data at implementation.

### Content-type awareness

- **Audiobook rows**: add `default_audio_edition_id: {_is_null: false}` — only books
  with a known audio edition. Confirmed filterable in schema.
- **Ebook rows**: recommend NO `default_ebook_edition_id` filter initially —
  coverage of that field is unverified and popular books are near-certain to have
  ebook releases findable by the release sources anyway. Verify coverage live before
  deciding to add it.
- `books_trending` takes no media-type argument, so for trending the audiobook variant
  applies the audio-edition filter in the hydration step (step 2 `where`), which may
  shrink the row below `limit` — over-fetch ids (e.g. limit 40 for a 20-tile row)
  to compensate.

## 5. Book fields fragment + `BookMetadata` mapping

Use the exact field set the fork's list/series/author queries already select and that
`_parse_book` (hardcover.py:2740) already consumes — reuse `_parse_book` verbatim:

```graphql
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
compilation
editions_count
cached_image
cached_contributors
contributions(where: {contribution: {_eq: "Author"}}) {
  author { name }
}
featured_book_series {
  position
  series { id name primary_books_count }
}
```

Mapping (all handled by `_parse_book` today):

| API field | BookMetadata | Notes |
|---|---|---|
| `id` | `provider_id` | int → str |
| `title` / `subtitle` | `title` / `subtitle` | |
| `contributions[].author.name` | `authors` | fallback: `cached_contributors` (both shapes handled) |
| `cached_image` | `cover_url` | via `_extract_cover_url` (hardcover.py:564); frontend proxies through `/api/covers` |
| `release_date` | `publish_year` | via `_extract_publish_year` |
| `headline` + `description` | `description` | combined |
| `slug` | `source_url` | `_build_source_url` |
| `featured_book_series` | `series_id/name/position/count` | |
| `rating`, `ratings_count`, `pages`, `users_count` | `display_fields` | provider display-field builder |
| `cached_tags` | `genres` | NOT in fragment above; optional — add if tiles want genre chips |
| ISBN fields | `isbn_10/13` | not fetched; not needed for tiles (resolved later in release flow) |

`BookMetadata` (metadata_providers/__init__.py:187) → frontend `Book` conversion is the
existing metadata-search path; discover endpoint returns the same shape, so tiles get
status overlays (library match / requested / download state) for free.

## 6. Interplay with existing caching

`hardcover.py` methods use `@cacheable` (core/cache.py) with TTLs of 120 s–600 s.
Discover adds its own longer-lived keys (trending 6 h, new releases 24 h) at the
discover-module level — do NOT reuse `@cacheable` defaults, and don't double-wrap the
hydration call in a short-TTL cacheable or serve-stale semantics get confused.
Serve-stale requires a small extension: `CacheService.get` currently deletes expired
entries; discover needs get-with-stale-fallback (keep entry, flag freshness).

## 7. Open items to verify live at implementation (needs instance token)

1. `books_trending` id ordering (ranked vs unranked) — §3.
2. `users_count` floor (10?) and 90-day window produce a full, non-junk row — §4.
3. `default_audio_edition_id` coverage: does the audiobook filter leave enough books? — §4.
4. Whether `default_ebook_edition_id` is populated widely enough to ever use — §4.
5. Practical depth limit unaffected for our shapes (expected fine; existing prod queries are deeper than docs' stated limit) — §2.

## 8. Audible catalog API — audiobook rows when provider is Audible

Verified LIVE 2026-08-02 (unauthenticated, no token needed) against
`api.audible.com/1.0/catalog/products` — the same endpoint `audible.py` already
wraps (`_build_search_params`, `_parse_product`, `RESPONSE_GROUPS`,
`MAX_RESULTS_PER_PAGE = 50`).

**Transport caveat:** Audible has NO shared request helper — search does
`session.get` inline inside `@cacheable _search_cached`. Discover fetchers must
make their own `session.get` with the browse params; routing through
`_search_cached` would double-cache and collapse errors into empty results
(breaking the empty-vs-failed contract in the design spec).

**Key fact: the catalog endpoint browses with NO search terms.** Omitting
title/author/keywords and passing only `products_sort_by` returns a global browse
feed. Both discover rows map directly onto sorts the provider already uses in
`SORT_MAPPING` (audible.py:74):

### Row 1 — Best Sellers (Audible's analogue of "Trending")

```
GET /1.0/catalog/products
    ?num_results=50&page=0
    &products_sort_by=BestSellers
    &response_groups=<RESPONSE_GROUPS>&image_sizes=500,1024
```

Verified live: returns ranked best-seller list with full product data in ONE call.
Parse with `_parse_product` verbatim.

### Row 2 — New Releases

```
... &products_sort_by=-ReleaseDate ...
```

**Gotcha, verified live: `-ReleaseDate` leads with PREORDERS** (future
`issue_date`, up to a year out). Measured on 2026-08-02: page 0 of 50 = 33 future
+ 17 published, with yesterday's releases appearing on page 0 — the preorder wall
is thin in the no-keyword browse feed. No server-side date filter exists on the
public endpoint, so: over-fetch (page 0 + page 1 if needed), drop
`issue_date > today` server-side, keep first 20. Two calls worst case.

### Operational notes

- **No auth**: no token, no expiry failure mode (unlike Hardcover §2). Same cache
  TTLs apply anyway (politeness; no documented rate limit).
- **Region-aware**: endpoint is `api.audible.{tld}` per the configured region
  setting (`_AUDIBLE_REGION_OPTIONS`, audible.py:591) — discover inherits region
  automatically by reusing the provider's base-URL logic.
- Deep pagination is clamped/repeats (pages 20/100/400 returned identical date
  ranges) — irrelevant at our depth, but don't paginate past the first few pages.
- **`_parse_product` does NOT filter content kinds** — it drops only items missing
  asin/title. The browse feed can contain podcasts/periodicals/non-listenable
  items, so discover fetchers must filter explicitly before parsing:
  `is_listenable is True` and `content_delivery_type in {"SinglePartBook",
  "MultiPartBook"}` (both fields observed live in the browse response;
  `product_attrs` response group).
- `BookMetadata` carries only `publish_year` (no full date), so tiles cannot show
  exact release dates; the `issue_date <= today` filtering happens server-side on
  the raw payload before parsing. Fine for v1 (tiles don't show dates).

### Open items (Audible)

6. Confirm the thin-preorder-wall observation holds across regions (tested .com only)
   and over time; if a region's wall is deep, raise the over-fetch page count.
7. Measure the explicit content filter's drop-rate on live browse pages
   (podcasts/non-listenable share) to validate that 50/page over-fetch fills a
   20-tile row.
