# Hardcover-ID Library Matching — Design (Project 3)

**Date:** 2026-10-06
**Status:** Approved in conversation; revised after Codex review (10 findings: 8 accepted, 2 answered); awaiting written-spec review

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

4. Medium labels are not edition markers: `(Light Novel)`, `(Novel)`, `(LN)` join
   `(Unabridged)`/`(Abridged)` as qualifiers ignored by the other-edition check.
5. Combined-mode safety: a book is locked in combined mode only when **both** formats are
   held.
6. A Hardcover-ID conflict vetoes a weaker match.

Out (rejected): trimming a subtitle after `Vol. N:` and `volume`→`vol` normalization —
widest coverage, but it is the loosening the matcher's docstring warns against (four
*Housemaid* titles differing only by suffix), and tagging already reaches those books
through the Hardcover ID. Out (YAGNI): a lookup carrying only a Hardcover ID — every
producer (Hardcover search, Grimmory extraction) requires a title anyway.

Audiobookshelf items carry no Hardcover ID, so items 1 and 6 do not touch them; items 2
and 4 (author reversal, medium labels) apply to Audiobookshelf titles too, by design.

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
- **Detail validation:** a detail payload whose `id` differs from the requested id, or that
  lacks a `metadata` object, is a `BookloreError` (malformed), never a skipped row. A
  missing or non-numeric `hardcoverBookId` inside valid metadata is normal (untagged book)
  and just yields `hardcover_id = ""`.
- **Bounded cost:** the per-book detail reads of one sync share one `requests.Session`
  (connection reuse; login and the few listing pages keep their existing sessionless
  calls, since they are a handful of requests — Codex plan review #6);
  the first failure ends the loop (fail fast — no per-book retries). A 401 mid-loop
  triggers exactly one re-login and one retry of that book; a second 401 fails the sync.
  A 404 (book deleted between list and read) fails the sync too — the next scheduled
  sync sees a consistent list. Retrying a failed stale sync keeps the scheduler's existing
  cadence; no new retry loop is added.
- **Paging completeness:** a page response with a missing or non-positive
  `page.totalPages` *and* no top-level `totalPages` is malformed (`BookloreError`) unless
  `content` is empty; reaching the provider's page cap before `totalPages` is a sync
  failure, not a silent partial result.
- `extract_library_items` reads `metadata.hardcoverBookId`; only an all-digit value is
  kept (`hardcover_id`), anything else is `""`.
- `LibraryItem` gains `hardcover_id: str = ""` as its **last** field, so every existing
  constructor still works. It is not stored as a column (the key table carries it as the
  `hardcover:<id>` key). `LibraryMatch` gains `hardcover_id: str = ""` (last field),
  filled by `find_matches` from the matched item's `hardcover:` key; it is internal and
  not added to the API payload.

### 2. Match keys

- `build_match_keys(title, author, subtitle=None, asin=None, isbn=None, hardcover_id=None)`
  adds `hardcover:<digits>` when `hardcover_id` is all digits (namespaced like `asin:` and
  `isbn:`; a title key always contains `|`, so namespaces cannot collide). A Hardcover ID
  alone is enough for a key, exactly as an ASIN or ISBN is.
- `replace_items` passes `hardcover_id=item.hardcover_id`.
- `author_match_keys` additionally emits the reversed order when the **normalized** name
  has exactly two tokens and the raw name contains **no comma**. One comma keeps today's
  inverted-name handling; three or more tokens get no reversal. Author keys only combine
  with the normalized title key (bracketed text and a leading article removed), so a
  reversal matches only when the normalized titles are equal too. Accepted residual risk:
  two different people whose two-word names are each other's reverse, with the same
  normalized title — a deterministic but vanishingly rare collision (Codex finding #4,
  kept by user decision).

### 3. Lookup

- Backend `lookup_books` reads `provider` and `provider_id` from each requested book and
  passes `hardcover_id=provider_id` to `build_match_keys` **only when
  `provider == "hardcover"`**; other providers' IDs are ignored.
- **Conflict veto:** when the requested book has a Hardcover ID and a matched item carries
  a *different* Hardcover ID, that item is dropped from the result (two verified distinct
  works that merely share a normalized title/author key). An item without a Hardcover ID
  is unaffected. The veto applies even when the item also matched on an exact ISBN or
  ASIN (Codex plan review #7, declined). Real Grimmory data held wrong ISBNs, such as
  manga ISBNs on light-novel volumes, which is the error the tagger exists to fix. The
  Hardcover ID was verified by the tagger, so it outranks a stored identifier that may
  be stale. The cost is a missed badge, the cheap failure.
- **Medium labels:** `light novel`, `novel` and `ln` join `abridged`/`unabridged` in
  `_NON_EDITION_QUALIFIERS`, so `Overlord (Light Novel), Vol. 1: …` against Grimmory's
  `Overlord, Vol. 1` stays a same-edition holding (`items`). `(Manga)`, `(Graphic Novel)`
  and the dramatized/full-cast markers still demote — those are different adaptations.
- **Semantics (Codex finding #2, answered):** "in library" has always meant *the work in
  this format*, not one specific edition (`_match_payload` docstring: "'In library' is not
  'same edition'"); the title+author key already matches across editions. A Hardcover-ID
  hit passes through the same checks — format split, ASIN disagreement, edition markers —
  so it adds no new kind of match, only reaches books whose titles/authors were spelled
  differently. Hardcover keeps different adaptations (light novel vs manga) as different
  works, hence different IDs.
- Everything else downstream is unchanged: another format lands in `other_formats` (never
  locks).
- Frontend `LibraryLookupBook` gains optional `provider` and `provider_id`;
  `buildLibraryLookupPayload` includes them only for `provider === 'hardcover'` with a
  non-empty `provider_id`. Eligibility is unchanged (a book still needs title+author, an
  ASIN or an ISBN). `booksLookupSignature` includes `provider_id` so a result set
  refetches when it changes.
- `singleBookLookup` gains optional trailing `provider` and `providerId` parameters. The
  three callers and where each gets the identity:
  - `DetailsModal.tsx` — from the `book` it displays; add both to its `useMemo` deps.
  - `activity/ActivityCard.tsx` (approval panel) — from the request's stored book data;
    add to its `useMemo` deps.
  - `RequestConfirmationModal.tsx` — its preview (`utils/requestConfirmation.ts`) carries
    no provider fields, so read them from `payload.book_data` (requests already store
    `provider`/`provider_id`, `utils/requestPayload.ts`, `core/user_db.py`; no migration).
- **Combined mode:** the results view looks up one format (`effectiveContentType`) but the
  combined acquire action opens both legs. In combined mode the results view and the
  details modal (whose "Find Downloads" runs the same combined flow) look the books up
  once more per format (`ebook`, `audiobook`). The action locks only when **each**
  format's own lookup reports a same-edition holding (`items`). `other_formats` is never
  edition-checked by the backend, so a full-cast audiobook must not stand in for the
  recording being acquired (Codex plan review #2). Holding one format shows the badge but
  never locks the action.

## Error Handling

- Grimmory sync: per-book read failure → `BookloreError` → the sync fails, the previous
  index stays, `last_error` records why (existing scheduler behaviour).
- Lookup: a missing or non-numeric `provider_id` simply adds no Hardcover key.
- Stale index after a failed sync keeps serving the previous holdings, as today; the
  `stale` flag already reaches the frontend.
- **Known limit (Codex plan review #4, deferred):** the settings "Sync Library Now" action
  is synchronous behind the frontend's 30 s request timeout. At ~55 ms per detail read,
  libraries beyond ~500 books will show a timeout in the UI while the sync still finishes
  in the background. The current library (~148 books, ~8 s) is far below that. A
  background job with polling is the fix if the library grows.

## Testing

- `tests/library/test_matching.py`: reversed two-token author keys; no reversal with a
  comma or three tokens; same title + different (non-reversed) author → no shared key;
  `hardcover:` key from digits only; `light novel`/`novel`/`ln` ignored by
  `edition_qualifiers`, `manga` still kept.
- `tests/library/test_providers_grimmory.py`: `hardcover_id` extracted from full payloads
  (digits only); `fetch_items` reads each book and fails the sync on a failed read, an
  empty `{}` detail, a wrong-id detail, a 404, and a second 401; one 401 re-logins once and
  succeeds; reads share one session.
- `tests/grimmory/test_client.py`: `get_book` happy path and errors; `list_books` paging
  with the nested `page.totalPages` shape, the legacy top-level shape, multi-page
  traversal, missing/invalid totals (malformed unless empty), and page-cap exhaustion
  (failure).
- `tests/library/test_index.py`: an item with a Hardcover ID is found by its
  `hardcover:` key.
- `tests/library/test_lookup.py`: the real Overlord pair (`Overlord (Light Novel), Vol. 1:
  The Undead King` / `Kugane Maruyama` vs Grimmory `Overlord, Vol. 1` / `Maruyama Kugane`,
  same Hardcover ID) lands in **`items`**, not just `matches`; a `(Manga)` result with the
  same title key still demotes; a matched item with a different Hardcover ID is dropped;
  a non-Hardcover `provider_id` adds no key; an audiobook lookup against an ebook
  Hardcover-ID holding lands in `other_formats`.
- Frontend: payload includes `provider`/`provider_id` only for Hardcover; eligibility is
  unchanged; the signature changes with `provider_id`; `singleBookLookup` carries them; the
  three surfaces pass identity (request confirmation via `payload.book_data`); combined
  mode locks only when both formats are held (ebook-only and audiobook-only holdings do
  not lock).
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
