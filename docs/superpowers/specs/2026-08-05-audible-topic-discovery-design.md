# Audible Topic Discovery Design

**Date:** 2026-08-05

**Status:** Approved for implementation planning

**Related:** `docs/superpowers/specs/2026-08-02-discover-rows-design.md`

## Summary

Extend Shelfmark's existing homepage Discover rows with Audible-native topic
recommendations. Six broad topics are always eligible as audiobook rows, and each
user may choose one broad topic or nested Audible subgenre as their preferred
first row. Recommendations are real Audible catalog products from the configured
regional storefront, ranked with Audible's `BestSellers` ordering.

The feature reuses the current provider → discover service → `/api/discover` →
`DiscoverSection` flow. It adds regional taxonomy discovery, a cascading settings
control, topic-aware provider dispatch, and per-user row ordering without creating
a second discovery subsystem.

## Goals

- Show Audible recommendations for six permanent topics:
  - Fantasy
  - Romance
  - Mystery, Thriller & Suspense
  - Science Fiction
  - Historical Fiction
  - Horror
- Let a user choose any broad Audible category or nested subgenre as
  `DEFAULT_DISCOVER_TOPIC`.
- Let the user stop at a broad category or drill into a descendant with a
  two-stage cascading selector.
- Put the preferred topic first while retaining the permanent rows.
- Show topic rows in Audiobook and Combined views, never book-only view.
- Resolve category IDs from the configured Audible storefront rather than
  hard-coding one region's IDs.
- Preserve the existing independent row loading, stale fallback, library badges,
  request badges, download status, and details flow.

## Non-goals

- Personalized recommendations based on listening or search history.
- Multiple preferred topics per user.
- Admin-curated book lists.
- Topic rows sourced from Hardcover, Open Library, or Google Books.
- Keyword-search fallback when an Audible category is absent.
- A homepage topic picker. Topic selection belongs in Search Preferences.
- Changing the six permanent topic rows in settings.

## User Experience

### Homepage rows

When Audible is the effective audiobook metadata provider, the eligible rows are:

1. Best Sellers
2. New Releases
3. Fantasy
4. Romance
5. Mystery, Thriller & Suspense
6. Science Fiction
7. Historical Fiction
8. Horror

With no preference, they render in that order. With a preference:

- If the preference exactly matches a permanent topic, that permanent row moves
  to the first position and is removed from its old position.
- If it is another broad category or a subgenre, one preferred row is inserted
  first and the eight standard/permanent rows retain their order below it.
- If it no longer resolves in the configured storefront, the preferred row is
  hidden and Best Sellers naturally becomes first.

A preferred descendant row is labelled with enough context to avoid ambiguous
leaf names: `Broad topic — Selected leaf`, for example
`Science Fiction & Fantasy — Epic`. A selected broad topic uses its own name.

Topic rows render only when all applicable gates pass:

- the user is authenticated;
- Universal search mode is active;
- `SHOW_DISCOVER_ROWS` is enabled;
- the view is Audiobook or Combined; and
- the effective audiobook metadata provider is Audible.

In Combined view, ordinary rows still use the configured combined metadata
provider. Audible topic and preferred rows always use the effective audiobook
provider. This allows Hardcover Trending/New Releases and Audible topic rows to
coexist.

### Cascading topic selector

The Search Mode settings tab and personal Search Preferences reuse one
`AudibleTopicSelector` custom component:

1. **Broad topic** lists top-level categories from the configured storefront's
   Audible `Genres` taxonomy.
2. **Subgenre** lists every descendant under the selected broad topic, flattened
   into readable relative breadcrumbs.

The descendant selector begins with an `All …` option, such as
`All Science Fiction & Fantasy`, which saves only the broad path. Other examples include `Fantasy`, `Fantasy → Epic`, and
`Science Fiction → Space Opera`.

The control provides loading, unavailable, stale-taxonomy, and saved-value-no-
longer-available states. A user can reset the setting to inherit the deployment
default through the existing reset behavior.

The selector is shown only when the effective search mode is Universal and the
effective audiobook provider is Audible. A blank deployment default means no
preferred row; users can independently override it.

## Data Model

`DEFAULT_DISCOVER_TOPIC` is a user-overridable, environment-disabled setting in
the `search_mode` tab. Its value is a list of exact taxonomy path segments:

```json
["Science Fiction & Fantasy", "Fantasy", "Epic"]
```

An empty list means no preferred topic. A list avoids ambiguous delimiter
escaping and disambiguates repeated category names by their complete ancestry.
The value is not an Audible category ID: IDs are regional implementation details
and may change independently of labels.

The setting is backed by a hidden value field inside a `CustomComponentField`
registered as `audible_topic_selector`. The settings registry continues to own
the value schema, global default, and `user_overridable` metadata. The shared
user-preference helper must enumerate nested custom-component value fields via
`iter_value_fields()` so the key appears in admin and self-service preference
payloads.

Frontend user settings type:

```ts
DEFAULT_DISCOVER_TOPIC?: string[];
```

`/api/config` exposes the effective path and, when the path exactly matches a
permanent topic for the current region, its permanent row key. The frontend uses
the latter only to prevent a duplicate core row; the server remains authoritative
for category resolution.

## Audible Taxonomy

### Fetching and normalization

`AudibleProvider` fetches:

```text
GET /1.0/catalog/categories
  ?root=Genres
  &categories_num_levels=8
  &response_groups=category_metadata
```

The provider treats the payload as untrusted and optional:

- accept only category objects with a non-empty string `name` and numeric-string
  `id`;
- recursively normalize `children` lists;
- cap traversal at 8 levels and 5,000 accepted nodes;
- skip malformed nodes without failing valid siblings;
- preserve exact display labels and full ancestry;
- deduplicate by complete path, not leaf name.

Path normalization trims leading/trailing whitespace from every segment, rejects
empty segments, and otherwise preserves Audible's case, punctuation, accents, and
segment order. Matching compares the resulting path tuples exactly.

The normalized internal node contains `name`, `path`, `category_id`, and
`children`. The browser-facing response omits `category_id`; discovery resolves
paths server-side.

### Permanent topic resolution

The six permanent rows have stable internal keys:

| Key | Label |
|---|---|
| `topic_fantasy` | Fantasy |
| `topic_romance` | Romance |
| `topic_mystery_thriller` | Mystery, Thriller & Suspense |
| `topic_science_fiction` | Science Fiction |
| `topic_historical_fiction` | Historical Fiction |
| `topic_horror` | Horror |

Each key owns ordered exact-path candidates by `REGION_TLDS` region. This is
necessary because Audible localizes both labels and category IDs. Resolution
matches a complete normalized path, never a leaf name alone. Every currently
supported Audible region receives verified candidates; if none match a
successfully fetched tree, that permanent row is unsupported and hidden.

There is no keyword or broader-category fallback. In particular, Romance maps to
Audible's broad Romance category; it is not labelled Urban Romance.

### Taxonomy endpoint

Add an authenticated read-only endpoint:

```text
GET /api/metadata/audible/topics
```

Successful response:

```json
{
  "region": "us",
  "stale": false,
  "topics": [
    {
      "name": "Science Fiction & Fantasy",
      "path": ["Science Fiction & Fantasy"],
      "children": []
    }
  ]
}
```

The real `children` array recursively contains the same public shape. The route
does not expose IDs and does not accept a requested region. It always represents
the deployment's configured Audible storefront. A taxonomy failure with no
last-known-good tree returns HTTP 503; the selector shows a
retryable unavailable state.

## Provider Topic Browse

Generalize the existing Audible discover page fetcher with an optional internal
`category_id`. Topic requests use:

```text
GET /1.0/catalog/products
  ?category_id={resolved_category_id}
  &products_sort_by=BestSellers
  &num_results=50
  &page={page_index}
  &response_groups={RESPONSE_GROUPS}
```

The existing discover rules continue to apply:

- only `is_listenable == true`;
- only `SinglePartBook` and `MultiPartBook` delivery types;
- at most two upstream pages;
- deduplicate by ASIN;
- return at most 20 parsed books;
- skip malformed individual products;
- fail the whole fetch when any required page fails so partial rows are never
  cached as fresh.

Provider contracts remain explicit:

- `None`: upstream/taxonomy failure; eligible for stale-row fallback.
- `[]`: successfully resolved but unsupported/empty; cache as an empty success.
- non-empty list: successful recommendation row.

## Discover Service and API

Extend the existing row registry with the six permanent topic keys and one
synthetic `preferred_topic` row key.

Provider dispatch rules:

- Best Sellers/New Releases/Hardcover rows resolve the provider exactly as they
  do today.
- Permanent and preferred topic rows reject `ebook` content.
- For `audiobook` and `combined`, topic rows resolve
  `get_configured_provider_name("audiobook", user_id=...)` and continue only when
  it is `audible`, enabled, and available.
- `preferred_topic` reads `DEFAULT_DISCOVER_TOPIC` for the authenticated
  `user_id`; the request never supplies a path or category ID.

`GET /api/discover` retains its current response shape. Preferred-row labels are
computed from the resolved path. Unsupported paths return a valid empty row, so
the frontend hides them without presenting an error.

Cache keys include everything that can change the result:

- taxonomy: Audible storefront;
- permanent row: storefront, permanent key, and resolved category ID;
- preferred row: storefront, normalized full path, and resolved category ID.

Preferred paths are serialized deterministically and hashed for bounded cache-key
length; the category ID remains present in the key so a taxonomy remap cannot
reuse books fetched for an obsolete ID.

Different users selecting the same path share book-row cache entries. Region
changes cannot serve another storefront's fresh or stale data.

## Cache and Concurrency

Taxonomy cache:

- fresh TTL: 24 hours;
- last-known-good TTL: 7 days;
- one fresh and one last-good entry per storefront;
- per-storefront single-flight lock.

Topic rows use the existing popularity policy:

- fresh TTL: 6 hours;
- last-known-good TTL: 7 days;
- existing per-row single-flight behavior.

Reads follow the existing dual-entry semantics:

1. return fresh when present;
2. on a miss, fetch under the key lock;
3. on upstream failure, return last-good with `stale: true`;
4. on a successful empty response, cache `[]` and do not resurrect stale books.

All rows retain independent loading and failure boundaries. One unavailable topic
does not suppress Best Sellers, New Releases, or other topics.

## Frontend Changes

### Settings

Add an `audible_topic_selector` entry to the existing custom settings component
registry. The renderer fetches the taxonomy endpoint only when its effective
search/provider gates make the field applicable. The same component is reused by
the global Search Mode settings and the explicit personal Search Preferences
section.

Pure helpers handle:

- flattening descendants into relative breadcrumb options;
- converting a selected broad/descendant option to a path list;
- restoring both selectors from a saved path;
- recognizing an unavailable saved path;
- reset/inheritance comparisons for list values.

### Homepage

Replace the single-provider-only row-definition calculation with a helper that
accepts:

- active content type (`ebook`, `audiobook`, or `combined`);
- standard discover provider;
- effective audiobook provider;
- whether a preferred path exists; and
- the optional matching permanent-row key.

The helper returns deterministic ordered row definitions. `DiscoverSection`
continues to fetch each definition independently through `getDiscoverRow`, reuse
the metadata transformer, batch library lookup, and render existing overlays.

No new card or carousel visual language is introduced. Audible covers and
skeletons remain square.

## Validation and Failure Handling

Preference writes accept only:

- `[]`; or
- a bounded list of non-empty strings that exactly traverses the current cached
  regional taxonomy from a top-level node to a descendant.

Validation applies to global settings, admin-edited user overrides, and
self-service updates. The browser cannot submit a raw category ID. If taxonomy
verification is temporarily impossible, a new non-empty selection is rejected
with a retryable message instead of being stored unchecked. Existing saved
values remain readable and can always be reset.

Runtime behavior:

| Condition | Behavior |
|---|---|
| Taxonomy request fails, last-good exists | Use stale taxonomy; mark topic endpoint stale |
| Taxonomy request fails, no last-good | Hide topic/preferred rows; selector shows retryable unavailable state |
| Permanent path absent in successful taxonomy | Cache empty success; hide that row |
| Saved preferred path absent | Hide preferred row; show saved value as unavailable in settings |
| Product request fails, row last-good exists | Serve stale books |
| Product request fails, no row last-good | Hide only that row |
| One product is malformed | Skip it and continue |
| Invalid stored preference shape | Treat as no preferred row and allow reset |
| Audible is not effective audiobook provider | Do not request or render Audible topic rows |
| Book-only view | Do not request or render Audible topic rows |

## Testing

### Backend provider tests

- Parse nested category trees and skip malformed siblings.
- Enforce path uniqueness, depth cap, and node-count cap.
- Resolve exact full paths when leaf names repeat.
- Cover at least one English and one localized storefront fixture.
- Verify permanent candidate paths for every supported `REGION_TLDS` entry.
- Distinguish taxonomy failure from successful missing category.
- Verify taxonomy fresh/last-good TTLs and region isolation.
- Verify category browse parameters include `category_id` and `BestSellers`.
- Reuse coverage for filtering, two-page top-up, deduplication, malformed products,
  and page failure.

### Discover service/API tests

- Permanent topic dispatch in audiobook mode.
- Combined mode routes standard and topic rows to their separate configured
  providers.
- Book-only requests return empty topic rows.
- Preferred path comes from the authenticated user's effective setting.
- No request parameter can override the path/category.
- Exact core preference avoids a duplicate row.
- Cache keys isolate storefront, category, and preferred path.
- Fresh, empty, failure, and stale-fallback matrices.
- Authentication, row validation, serialization, and square Audible cover data.

### User settings tests

- Registry exposes `DEFAULT_DISCOVER_TOPIC` as user-overridable.
- Nested custom value fields appear in global/admin/self preference payloads.
- Global save, admin override, self-service override, reset, and inheritance.
- Valid broad and descendant paths are accepted.
- Empty, malformed, nonexistent, and temporarily unverifiable paths behave as
  specified.
- `/api/config` returns the effective preference and matching permanent key.

### Frontend tests

- Cascading options for broad, nested, and `All…` selections.
- Restore a saved path and flag an unavailable saved path.
- Deterministic rows for ebook, audiobook, and combined modes.
- No preference, permanent-topic preference, non-core broad preference, nested
  preference, and unavailable preference ordering.
- Provider gates prevent topic requests when Audible is not the audiobook
  provider.
- Existing discover row-state, content-type, library-match, and details tests
  remain green.

### Verification commands

- Focused Python tests with
  `uv run pytest tests/metadata/test_audible_topics.py tests/core/test_discover_service.py -x --tb=short`.
- Full Python suite with the repository's established test command.
- Frontend unit tests via `npm run test:unit`.
- Frontend type check, lint, and formatting checks through existing scripts.

## External API Note

Audible's catalog API is undocumented by Audible and can change. The current
community reference documents both the category endpoints and the catalog
`category_id`/`products_sort_by` parameters:

- https://audible.readthedocs.io/en/latest/misc/external_api.html

The implementation therefore keeps all upstream parsing defensive, caches the
last known good taxonomy and rows, and never treats missing optional fields as
fatal to unrelated rows.
