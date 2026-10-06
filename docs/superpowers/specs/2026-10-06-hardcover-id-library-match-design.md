# Hardcover-ID Library Matching — Design (Project 3)

**Date:** 2026-10-06
**Status:** Approved in conversation; awaiting written-spec review

## Primary Goal & Intent

Books the user owns in Grimmory must show "In library" in Shelfmark search. The trigger
was the Overlord light novels, which never badged.

The strict title+author key misses them three ways (diagnosed 2026-10-06 on the live
index): surname-first authors with no comma (`Maruyama Kugane` vs Hardcover's
`Kugane Maruyama`), a subtitle on one side only (`Overlord, Vol. 1` vs
`Overlord (Light Novel), Vol. 1: The Undead King`), and `Volume` vs `Vol.`.

Since Project 1 (the fleet-infra Grimmory metadata tagger, deployed 2026-10-06), Grimmory
books carry a verified **Hardcover book ID** (62 of 148 so far, growing as the user tags
more). A Hardcover ID identifies a *work* — every edition of it shares the ID — and every
Shelfmark Hardcover search result already carries that ID as `provider_id`. Matching on it
sidesteps the title/author fragility entirely.

The governing rule of `shelfmark/library/matching.py` stands: **a false "already in
library" is worse than a missed badge** — it talks a user out of a request they were
entitled to make.

## Scope (user decision: option 2 of 3)

In:

1. A `hardcover:<id>` match key: indexed from Grimmory's `hardcoverBookId`, looked up from
   a Hardcover search result's `provider_id`.
2. Reversed author order for a comma-less two-token name (`maruyama kugane` ↔
   `kugane maruyama`), on both sides of the title+author key.
3. Fix `shelfmark/grimmory/client.py:list_books` reading `totalPages` from the top level
   when Grimmory nests it under `page` (silently indexes only the first 500 books).

Out (rejected): trimming a subtitle after `Vol. N:` and `volume`→`vol` normalization —
widest coverage, but it is the loosening the matcher's docstring warns against (four
*Housemaid* titles differing only by suffix), and tagging already reaches those books
through the Hardcover ID. Audiobookshelf items carry no Hardcover ID; they are unaffected.

## Current State

- `GET /api/v1/books/page` and `GET /api/v1/books` both **omit provider IDs**; only
  `GET /api/v1/books/{id}` returns `hardcoverBookId` (stored as a numeric string, e.g.
  `"886465"`). A single-book GET takes ~55 ms; 148 books ≈ 8 s.
- `library_item_keys (match_key, source, item_id)` is a generic key table; the index is a
  cache rebuilt by every sync (`replace_items`), so a new key kind needs no migration.
- `LibraryItem` is a frozen dataclass constructed in both providers and four test modules.
- Frontend `Book` has `provider` and `provider_id`; `buildLibraryLookupPayload`,
  `singleBookLookup` and `booksLookupSignature` (`src/frontend/src/utils/libraryMatches.ts`)
  do not send them. `singleBookLookup` callers: `DetailsModal.tsx`,
  `activity/ActivityCard.tsx`, `RequestConfirmationModal.tsx`.

## Design

### 1. Index: Hardcover IDs from Grimmory

- `shelfmark/grimmory/client.py` gains `get_book(config, token, book_id) -> dict`, with the
  same error handling as `list_books` (`BookloreError` on connection/timeout/HTTP/JSON
  failure; a non-dict payload is an error).
- `list_books` reads the page count from `payload["page"]["totalPages"]`, falling back to
  a top-level `totalPages` for older builds.
- `GrimmoryProvider.fetch_items` lists books as today, then reads each in full with
  `get_book` and passes the full payload to `extract_library_items`. **Any per-book read
  failure fails the whole sync** — the scheduler then records the error and keeps the
  previous index (the existing "malformed is a failure, not empty" rule): a partial index
  would silently drop badges.
- `extract_library_items` reads `metadata.hardcoverBookId`; only an all-digit value is
  kept (`hardcover_id`), anything else is `""`.
- `LibraryItem` gains `hardcover_id: str = ""` as its **last** field, so every existing
  constructor still works. It is not stored as a column (the key table carries it) and is
  not added to `LibraryMatch` or the API payload.

### 2. Match keys

- `build_match_keys(title, author, subtitle=None, asin=None, isbn=None, hardcover_id=None)`
  adds `hardcover:<digits>` when `hardcover_id` is all digits (namespaced like `asin:` and
  `isbn:`; a title key always contains `|`, so namespaces cannot collide). A Hardcover ID
  alone is enough for a key, exactly as an ASIN or ISBN is.
- `replace_items` passes `hardcover_id=item.hardcover_id`.
- `author_match_keys` additionally emits the reversed order when the **normalized** name
  has exactly two tokens and the raw name contains **no comma**. One comma keeps today's
  inverted-name handling; three or more tokens get no reversal. Because author keys only
  ever combine with the full exact title key, a reversal can only match the same title.

### 3. Lookup

- Backend `lookup_books` reads `provider` and `provider_id` from each requested book and
  passes `hardcover_id=provider_id` to `build_match_keys` **only when
  `provider == "hardcover"`**; other providers' IDs are ignored.
- Everything downstream is unchanged: a Hardcover-ID hit is a work-level match, so the
  existing split still applies — another format lands in `other_formats` (never locks),
  and ASIN disagreement or differing edition markers still demote to `other_editions`.
- Frontend `LibraryLookupBook` gains optional `provider` and `provider_id`;
  `buildLibraryLookupPayload` includes them only for `provider === 'hardcover'` with a
  non-empty `provider_id`; a book with only a Hardcover ID (no title/author/ASIN/ISBN) is
  now a valid lookup. `booksLookupSignature` includes `provider_id` so a result set
  refetches when it changes.
- `singleBookLookup` gains optional trailing `provider` and `providerId` parameters; the
  three callers pass them where the book they hold has them.

## Error Handling

- Grimmory sync: per-book read failure → `BookloreError` → the sync fails, the previous
  index stays, `last_error` records why (existing scheduler behaviour).
- Lookup: a missing or non-numeric `provider_id` simply adds no Hardcover key.

## Testing

- `tests/library/test_matching.py`: reversed two-token author keys; no reversal with a
  comma or three tokens; `hardcover:` key from digits only; Hardcover-ID-only books get a
  key.
- `tests/library/test_providers_grimmory.py`: `hardcover_id` extracted from full payloads
  (digits only); `fetch_items` reads each book and fails the sync when one read fails.
- `tests/grimmory/test_client.py`: `get_book` happy path and errors; `list_books` paging
  with the nested `page.totalPages` shape and the legacy top-level shape.
- `tests/library/test_index.py`: an item with a Hardcover ID is found by its
  `hardcover:` key.
- `tests/library/test_lookup.py`: a Hardcover result matches by ID even when title and
  author differ (the real Overlord pair); a non-Hardcover `provider_id` adds no key; an
  audiobook lookup against an ebook Hardcover-ID holding lands in `other_formats`.
- Frontend (`src/frontend/src/tests/libraryMatches.test.ts`): payload includes
  `provider`/`provider_id` only for Hardcover; a Hardcover-ID-only book is kept; the
  signature changes with `provider_id`; `singleBookLookup` carries them.
- Gates: `make python-checks python-test` and the frontend checks/tests
  (`make frontend-checks frontend-test`).

## Acceptance

After a release (`scripts/release-local.sh`, then the fleet-infra image bump — every push
user-gated): run a Grimmory index sync, then search Shelfmark for "Overlord" and
"Mushoku Tensei". Tagged volumes (e.g. Overlord 2, 3, 7, 8, 9; Mushoku 3–15) badge as owned;
the manga and untagged volumes do not.

## Implementation Order

1. `matching.py` (author reversal, Hardcover key) — pure, test-first.
2. `grimmory/client.py` (`get_book`, paging fix) and the Grimmory provider.
3. Index and lookup wiring.
4. Frontend payload, signature, `singleBookLookup` and its callers.
5. Checks, release, acceptance.
