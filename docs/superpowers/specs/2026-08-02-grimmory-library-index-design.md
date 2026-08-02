# Grimmory Library Index — Design

**Date:** 2026-08-02
**Status:** Approved, ready for implementation planning

## Primary Goal & Intent

Let Shelfmark query Grimmory for ebooks it already holds and flag those results in
the UI, the way it already does for audiobooks via Audiobookshelf.

Today the in-library badge is audiobook-only. `App.tsx:2626` passes
`showInLibraryBadges={!effectiveCombinedMode && effectiveContentType === 'audiobook'}`,
suppressing the badge on every ebook surface and in combined mode, because the
Audiobookshelf index matches on title+author with no notion of format and would
otherwise badge an ebook on the strength of an audiobook holding.

Adding Grimmory as an ebook source removes that limitation, but only if the index
becomes format-aware. Both halves are in scope.

## Key Decisions

### Badge semantics: format-scoped, cross-format disclosed

An ebook result badges and locks only on an ebook holding; an audiobook result only
on an audiobook holding. A holding in the *other* format is still surfaced in the
tooltip but never blocks acquisition — owning the audiobook must not stop you
grabbing the ebook.

Rejected: any-format matching (blocks legitimate cross-format requests) and strict
format-scoping with no disclosure (discards a genuinely useful signal for one field).

### Source-to-format split

Grimmory is the dedicated ebook library; Audiobookshelf is the dedicated audiobook
library. Source therefore implies format in practice, but each item's real format is
still recorded — a stray audiobook imported into Grimmory must not be able to emit a
false ebook badge.

### Indexing is decoupled from delivery

Every `BOOKLORE_*` field is currently gated behind `show_when: {BOOKS_OUTPUT_MODE:
booklore}`, and `get_booklore_library_options()` hard-returns `[]` unless that mode
is active (`shelfmark/config/booklore_settings.py:122`). Delivery and library-truth
share one switch.

They are separable. The API upload path carries real constraints — no audiobooks, a
nine-extension allowlist, no `verify_tls` setting — so writing ebooks to a
Grimmory-ingested folder is a legitimate setup that should still get badges. Coupling
them would also make the badge vanish silently on a delivery-mode change.

Connection settings move to their own tab; indexing gets its own toggle. This mirrors
Audiobookshelf, where `AUDIOBOOKSHELF_ENABLED` is independent of where audiobooks land.

### Shared index core rather than a parallel module

Roughly 380 lines of the Audiobookshelf integration are source-agnostic: the SQLite
index class, the staleness check, the scheduler loop, the enabled-gate. A parallel
`shelfmark/grimmory/` module would duplicate all of it and still need merge logic,
because `/api/library-matches` has to answer from both sources either way.

Extracting a shared core means one tagged query returns both sources' matches ranked
together, which is exactly the shape the cross-format tooltip needs. Rejected: two
separate index DB files — `replace_items` scoped by `source` already gives the same
blast-radius guarantee, and two files would cost merge code for nothing.

The refactor touches working, tested code. The ~2,250 lines of existing ABS tests are
the safety net and must be adapted rather than rewritten.

### Config keys keep the `BOOKLORE_` prefix

Labels say Grimmory; keys stay `BOOKLORE_*`. They are persisted in existing installs
and documented as env vars, and mixing `BOOKLORE_HOST` with `GRIMMORY_INDEX_INTERVAL_HOURS`
in one section reads worse than the inconsistency it would resolve.

### Upload is unaffected by the new toggle

`build_booklore_config` reads credentials directly and ignores `BOOKLORE_ENABLED`, so
an existing `BOOKS_OUTPUT_MODE=booklore` install delivers exactly as before after
upgrade. Such an install also already has credentials, so the one-time enablement
migration turns badges on for it automatically.

## Grimmory API Findings

Verified against `grimmory-tools/grimmory` (`backend/src/main/java/org/booklore/`).

- `GET /api/v1/books` returns `List<Book>`. `stripForListView` defaults true but strips
  only metadata *lock flags* and `libraryPath` — title, subtitle, authors, ISBNs and
  ASIN all survive (`service/book/BookQueryService.java:172`).
- `GET /api/v1/books/page?page=N&size=M` gives legacy offset pagination when no
  `sort`/`facet`/`facet_logic`/`query`/`cursor` is supplied.
- `Book` carries `id`, `libraryId`, `libraryName`, `title`, `primaryFile`,
  `alternativeFormats`, `metadata`.
- `BookMetadata` carries `title`, `subtitle`, `authors: List<String>`, `isbn10`,
  `isbn13`, `asin`, plus series and external-provider IDs.
- `BookFileType` is `PDF | EPUB | CBX | FB2 | MOBI | AZW3 | AUDIOBOOK` — Grimmory can
  hold audiobooks, hence the per-item format check.
- Auth is the existing `POST /api/v1/auth/login` → `accessToken` already used by the
  upload path.

**Coverage caveat:** `getBookDTOs` is scoped to the authenticated user — admins see
every library, non-admins only their assigned ones (`service/book/BookService.java:75`).
`BOOKLORE_USERNAME`'s permissions therefore determine index coverage. Test Connection
must report how many books and libraries that account can actually see — read live from
the API at test time, not from the index, which has not synced yet — so a too-narrow
service account is visible at setup rather than as mysteriously absent badges.

## Architecture

### New package `shelfmark/library/`

| File | Responsibility |
|---|---|
| `index.py` | `LibraryIndexDB` — SQLite cache keyed by `(source, item_id)` |
| `matching.py` | Moved from `audiobookshelf/`, extended with ISBN keys |
| `lookup.py` | Format-aware fan-out for `/api/library-matches` |
| `scheduler.py` | One background loop over registered providers |
| `providers/audiobookshelf.py` | `fetch_items()` wrapping the existing ABS client |
| `providers/grimmory.py` | `fetch_items()` over `GET /api/v1/books/page` |

### New `shelfmark/grimmory/client.py`

`BookloreConfig`, `BookloreError`, `booklore_login` and `booklore_list_libraries` move
out of `shelfmark/download/outputs/booklore.py`, which retains only upload-specific
functions and imports the rest. `list_books(config, token, page, size)` is added.

This is a targeted cleanup, not scope creep: `shelfmark/config/booklore_settings.py`
currently imports from `shelfmark/download/outputs/` just to log in, a dependency a
config module should not have.

### Schema

One `library_index.db`, replacing `audiobookshelf_index.db`:

```sql
CREATE TABLE library_items (
    source       TEXT NOT NULL,   -- 'audiobookshelf' | 'grimmory'
    item_id      TEXT NOT NULL,
    library_id   TEXT NOT NULL,
    library_name TEXT NOT NULL,
    media_type   TEXT NOT NULL,   -- 'audiobook' | 'ebook'
    title        TEXT NOT NULL,
    subtitle     TEXT,
    author       TEXT NOT NULL,
    asin         TEXT,
    isbn13       TEXT,
    PRIMARY KEY (source, item_id)
);

CREATE TABLE library_item_keys (
    match_key TEXT NOT NULL,
    source    TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    PRIMARY KEY (match_key, source, item_id)
);
CREATE INDEX idx_library_item_keys_key ON library_item_keys (match_key);

CREATE TABLE index_state (
    source TEXT NOT NULL,
    key    TEXT NOT NULL,
    value  TEXT,
    PRIMARY KEY (source, key)
);
```

The composite primary key matters: ABS item IDs are UUIDs and Grimmory's are numeric,
so they are otherwise free to collide. `replace_items(source, items)` scopes its
`DELETE` to one source, so a Grimmory sync can never wipe ABS rows. Per-source
`index_state` means one server being down does not mark the other stale.

Only `isbn13` is stored — every ISBN-10 canonicalizes losslessly to its ISBN-13 form,
so two columns would be two spellings of one fact.

## Matching

`matching.py` gains ISBN as a third exact key alongside title+author and ASIN,
following the module's existing doctrine:

- `normalize_isbn()` strips hyphens and spaces, validates length **and check digit**,
  and converts ISBN-10 → ISBN-13 (`978` + 9 digits + recomputed check). Bare `979`
  ISBN-13s pass through unchanged.
- `isbn_match_key()` emits `isbn:<13 digits>`, namespaced away from title keys exactly
  as `asin:` is.
- An ISBN is sufficient on its own, like an ASIN — it is a complete identity where half
  a title+author key is not.

Check-digit validation is deliberate. The module already argues that "an exact match on
junk is still an exact match", and metadata fields routinely carry `N/A` or zero-filled
placeholders. A bad check digit costs a badge; a false one talks a user out of a book
they do not own.

ISBNs are edition-specific — paperback, hardcover and ebook each get their own — so an
ISBN **hit** is a strong yes while a **miss** proves nothing. Title+author keeps
carrying the general case. This is the same asymmetry ASIN already has.

## Grimmory Sync

Log in once per run, then walk `GET /api/v1/books/page?page=N&size=500`, capped at 1000
pages as the ABS client is. Per book:

- `title` ← `metadata.title`, falling back to the top-level `title`
- `author` ← `metadata.authors[0]`, matching how ABS takes the first author rather than
  the comma-joined string, which the matcher would misread as an inverted "Last, First"
- `asin` ← `metadata.asin`; `isbn13` ← canonicalized from `metadata.isbn13` or `metadata.isbn10`
- `media_type` ← `'audiobook'` only if **every** known file type across `primaryFile`
  and `alternativeFormats` is `AUDIOBOOK`; otherwise `'ebook'`

The `media_type` rule is the safeguard on the source-to-format split: a stray audiobook
import cannot produce a false ebook badge, while a book holding both an EPUB and an M4B
still correctly reads as an ebook.

The scheduler generalizes with no behaviour change — the existing 300-second poll with
per-source staleness, each provider on its own interval, failures recorded beside the
data so an outage leaves stale badges standing rather than blanking them.

## Lookup Payload

Request gains `isbn_10`, `isbn_13` and `content_type` per book:

```json
{"books": [{"id": "abc", "title": "...", "author": "...",
            "asin": "", "isbn_13": "9780593135204", "content_type": "ebook"}]}
```

Response splits matches by format:

```json
{
  "enabled": true, "stale": false, "last_sync_at": "...",
  "sources": {
    "grimmory":       {"enabled": true, "stale": false, "last_sync_at": "...", "item_count": 4213},
    "audiobookshelf": {"enabled": true, "stale": true,  "last_sync_at": "...", "item_count": 892}
  },
  "matches": {
    "abc": {
      "libraries": ["Ebooks"],
      "items":         [{"source": "grimmory", "media_type": "ebook", "...": "..."}],
      "other_formats": [{"source": "audiobookshelf", "media_type": "audiobook", "...": "..."}]
    }
  }
}
```

`items` holds same-format holdings and drives the badge and the acquisition lock.
`other_formats` is advisory only. Top-level `enabled`/`stale`/`last_sync_at` remain as
aggregates so `useLibraryMatches` keeps working unchanged; `sources` is added for the
settings UI and diagnostics.

Aggregate rules, so the top-level fields are unambiguous:

- `enabled` — true if **any** source is enabled
- `stale` — true if **any** enabled source is stale, the conservative reading for an
  advisory flag
- `last_sync_at` — the **oldest** `last_sync_at` among enabled sources, so it answers
  "everything is current as of at least this time"; null if any enabled source has never
  synced

Classification is **per book** rather than per surface, via `content_type`, matched
backend-side with the existing `shelfmark.core.utils.is_audiobook`. Per-book
classification is what makes combined mode work — the case `App.tsx` currently switches
badges off for entirely.

## Frontend

- `libraryMatches.ts` — `LibraryMatchItem` gains `source` and `media_type`;
  `LibraryMatch` gains `other_formats`. `buildLibraryLookupPayload` forwards the ISBNs
  and `content_type`, and its eligibility rule widens from `asin || (title && author)`
  to `asin || isbn || (title && author)`. `booksLookupSignature` must fold in ISBN and
  content type, or a format switch will not refetch.
- `applyInLibraryLock` fires only on `items`, never `other_formats`.
- `libraryMatchTooltip` appends cross-format lines: "Also in your library as an audiobook: …"
- `InLibraryBadge` gains a `variant`: the existing solid lock for same-format, and a
  muted non-blocking variant for cross-format-only. Without it the two states would look
  identical while behaving differently, which is worse than no badge.
- `ResultsSection` swaps `showInLibraryBadges` for `defaultContentType`, used when a book
  carries no `content_type` of its own. `App.tsx:2626` passes `effectiveContentType`
  instead of a boolean — one call site, now enabling the feature rather than suppressing it.

## Settings

New Grimmory tab (`order=61`, beside Audiobookshelf), mirroring the ABS tab structure:

- `BOOKLORE_ENABLED` — master toggle, default `False`, but flipped to `True` by a one-time
  migration on installs that already have Grimmory credentials (see Rollout)
- `BOOKLORE_HOST` / `BOOKLORE_USERNAME` / `BOOKLORE_PASSWORD` **move here** from Downloads
- Test Connection — existing `check_booklore_connection`, extended to report the number
  of books visible to the account alongside the library count it already returns
- *Already In Library*: `BOOKLORE_LIBRARY_INDEX_ENABLED` (default `True`),
  `BOOKLORE_INDEX_INTERVAL_HOURS` (default 1, min 1, max 168), and a "Sync Library Now" button

The Downloads tab keeps the upload-only fields — destination, library, path, test button
— with a note pointing at the Grimmory tab for connection settings. Fields *move* rather
than duplicate: two tabs rendering the same key would fight.

The index is active when `BOOKLORE_ENABLED` and `BOOKLORE_LIBRARY_INDEX_ENABLED` are set
and host, username and password are all present.

`get_booklore_library_options()` and `get_booklore_path_options()` keep their existing
`BOOKS_OUTPUT_MODE == "booklore"` early-return: those pickers configure upload
destinations and are meaningful only in that mode.

## Testing

New `tests/library/`:

- **matching** — ISBN canonicalization, check-digit rejection, `979` passthrough, junk
  (`N/A`, zero-filled, truncated) rejection, ISBN-10/ISBN-13 cross-matching
- **index** — source-scoped replacement, ID collision across sources, per-source state
- **lookup** — format split, `other_formats` population, lock behaviour
- **grimmory provider** — `media_type` derivation including the mixed EPUB+M4B case,
  pagination, field extraction, missing-metadata handling
- **enablement migration** — flips on with credentials present, stays off with partial or
  absent credentials, and does not re-enable after a user unticks the box

Existing `tests/audiobookshelf/test_library_*.py` port to the shared core with their ABS
behaviour assertions intact. They are the regression net for the refactor and must be
adapted, not rewritten.

Frontend: extend `libraryMatches.test.ts` for the ISBN payload, the lookup signature, and
the lock-vs-tooltip split.

## Rollout

- **One-time enablement migration.** On first start after upgrade, if `BOOKLORE_ENABLED`
  has never been persisted and `BOOKLORE_HOST`, `BOOKLORE_USERNAME` and
  `BOOKLORE_PASSWORD` are all populated, set it to `True` and persist it. Anyone who has
  already configured Grimmory gets ebook badges without hunting for a checkbox.

  The migration keys off *never persisted*, not off the value, so a user who later
  unticks the box stays unticked — a migration that re-enabled on every boot would be a
  setting that refuses to stay off.

  The checkbox still defaults to `False` for fresh installs, matching the Audiobookshelf
  tab and keeping the Grimmory tab collapsed for people who do not use it.
- No other config migration — every key is unchanged.
- `library_index.db` rebuilds itself on first start, since a missing timestamp already
  counts as stale.
- The orphaned `audiobookshelf_index.db` gets a best-effort unlink on first init so
  `/config` does not accumulate a dead file.
- `docs/environment-variables.md` picks up the new keys.

## Out of Scope

- Renaming `BOOKLORE_*` config keys or adding `GRIMMORY_*` aliases.
- Exposing `verify_tls` for the Grimmory connection (a pre-existing gap in the upload
  path, unchanged here).
- Using Grimmory as an audiobook source, or Audiobookshelf as an ebook source.
- Series- or edition-level matching beyond the existing title+author, ASIN and new ISBN keys.
