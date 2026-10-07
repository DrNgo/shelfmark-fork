# Ebook Library Picker and Tagged Ingest (Project 2)

**Date:** 2026-10-06
**Status:** Approved design, pending implementation plan
**Repos:** `shelfmark-fork` (picker, upload, payload) and `fleet-infra` (dispatcher, tagger hook)
**Builds on:** Project 1, the Grimmory metadata tagger
(`fleet-infra/docs/superpowers/specs/2026-10-06-grimmory-metadata-tagger-design.md`), and
Project 3, Hardcover-ID library matching (`2026-10-06-hardcover-id-library-match-design.md`)

## Goal

When an admin downloads an ebook, they pick the Grimmory library it goes into (Fiction by
default). Shelfmark uploads it straight into that library, and the P1 tagger tags it
right away. The result is a verified ISBN and Hardcover ID, so the P3 badge works after
the next index sync. Today every ebook waits in BookDrop for a manual import, and is
untagged until someone runs `backfill`.

## Decisions (user-approved)

1. **Who picks:** admins only, as with audiobooks. Requesters cannot choose; their
   requests land in the default unless the admin changes it at approval.
2. **Where the choices come from:** live from Grimmory, meaning every library/path the
   `shelfmark` account can see, read through the cache Shelfmark already keeps for its
   settings dropdowns. No curated table.
3. **Default:** the existing `BOOKLORE_LIBRARY_ID` / `BOOKLORE_PATH_ID` settings, pinned to
   Fiction (library 3, path 3) in the manifest. The same manifest commit sets
   `BOOKLORE_DESTINATION=library`.
4. **Transport:** reuse `destination_key`. Ebook keys are `grimmory:<libraryId>:<pathId>`.
5. **One endpoint for both formats:** `GET /api/download-destinations?content_type=…`
   replaces `/api/audiobook-destinations`.
6. **After upload:** tag immediately, with the queue as the fallback (details in §4).
7. **Hook wiring:** a dispatcher in fleet-infra owns `CUSTOM_SCRIPT` and routes by content
   type to the ABS enrich hook or the tagger hook.

## Context (current behaviour)

- **Audiobook picker:**
  - `AUDIOBOOK_DESTINATIONS` table (`shelfmark/audiobookshelf/destinations.py`).
  - `GET /api/audiobook-destinations` returns `{destinations:[{key,name}]}`. It is
    admin-only, except in auth mode `none`.
  - The `destination_key` travels as follows:
    - `/api/releases/download` (via `withDestinationKey`)
    - the fulfil body
    - the `download_requests.destination_key` column
    - `queued_release_data`
    - `DownloadTask.destination_key`
    - the retry payload
  - `authorize_destination_key()` strips it from non-admin payloads (`main.py:1101`).
  - Picker UI:
    - `ReleaseModal.tsx`, gated by `canChooseDestination`
    - `ActivityCard.tsx`, the approve panel
    - combined mode stores a single `CombinedSelectionState.destinationKey`, applied to
      the audiobook leg only
- **Grimmory upload:**
  - `shelfmark/download/outputs/booklore.py`. Library mode does
    `POST /api/v1/files/upload?libraryId=&pathId=` and then
    `PUT /api/v1/libraries/{id}/refresh`; bookdrop mode posts to `/files/upload/bookdrop`.
  - Library and path IDs come from `BOOKLORE_LIBRARY_ID` / `BOOKLORE_PATH_ID`, which can
    be overridden per user.
  - The upload response is discarded.
  - Libraries and paths are read from `GET /api/v1/libraries`
    (`config/booklore_settings.py`, cached by credentials). That helper returns `[]`
    unless `BOOKS_OUTPUT_MODE == "booklore"`.
- **Custom script:**
  - `postprocess/custom_script.py` runs once per task, with phase `post_upload` for
    Grimmory.
  - It is called as `[script, target]` with a 300 s timeout. A non-zero exit, a timeout
    or a missing script fails the task, even after a successful upload.
  - The JSON payload is version 1 and carries `task` (title, author, series, …), `output`
    and `paths`. It does **not** carry `provider`, `provider_id`, `isbn_13`, `asin`, or any
    Grimmory book id.
- **Tagger (P1):**
  - `grimmory_tagger.py backfill|apply`, stdlib-only, Python 3.13-compatible.
  - Queue entries (`/config/grimmory-tag-queue.jsonl`, `QueueEntry` in `tag_queue.py`)
    already carry `library_id, path_id, filename, size, provider, provider_id, isbn_13,
    asin`.
  - `backfill._identify` already uses a queue entry's Hardcover `provider_id` as the first
    lookup. It is still gated by the same evidence checks, so a hint never bypasses
    verification. `resolve_entry` finds the book by `book_id`, or by library + exact
    filename.
- **Grimmory book fields:** `primaryFile` carries `fileName`, `fileSizeKb` and `addedOn`
  (P1 probe findings). Whether a library upload keeps the original filename is
  **unverified**; Grimmory may apply its own upload naming pattern.
- **Constraint:** Grimmory runs `DISK_TYPE: NETWORK`, so books cannot be moved between
  libraries in the UI (`POST /api/v1/files/move` returns 409). A wrong pick needs a manual
  `mv` on the NFS host.

## Design

### 1. Destinations endpoint (Shelfmark)

`GET /api/download-destinations?content_type=ebook|audiobook` returns
`{"destinations": [{"key": str, "name": str}], "default_name": str}`.

- **Auth:** same rule as today's audiobook endpoint (admin-only, open in auth mode
  `none`). A missing or unknown `content_type` returns 400.
- **Audiobook:** exactly today's list from `list_destination_options()`. `default_name`
  is `""`, so the frontend keeps its existing "Default audiobook destination" label.
- **Ebook:**
  - When `BOOKS_OUTPUT_MODE == "booklore"`, one entry per (library, path) pair from the
    cached Grimmory library list. The key is `grimmory:<libraryId>:<pathId>`.
  - The name is the library name, or `"<library> — <path>"` when the library has more
    than one path.
  - The list is ordered by library name, then path.
  - Otherwise, or when Grimmory is unreachable, the list is `[]`.
  - `default_name` is the name of the entry matching `BOOKLORE_LIBRARY_ID` /
    `BOOKLORE_PATH_ID`, or `""`.
- **Old endpoint:** `/api/audiobook-destinations` is removed. Its only caller is
  Shelfmark's own frontend, which ships in the same image.

### 2. Picker (Shelfmark frontend)

- `useAudiobookDestinations` becomes `useDownloadDestinations(contentType)`. It keeps the
  same module-level cache, now per content type, and still yields `[]` on error.
- The helpers in `utils/audiobookDestinations.ts` become generic, in
  `utils/downloadDestinations.ts`:
  - `shouldShowDestinationPicker(contentType, destinations)`: show when there is more than
    one destination, for either format.
  - `resolveDefaultDestinationKey`: unchanged behaviour.
  - Default-option label: `Default (<default_name>)` when the name is known, else
    `Default <format> destination`.
  - `withDestinationKey`: unchanged.
- **Release window:** `ReleaseModal.tsx` shows the picker for ebook releases too, under the
  same `canChooseDestination` gate (admin, not browse-fulfil mode).
- **Approval panel:** `ActivityCard.tsx` shows it for ebook requests.
- **Combined mode:** each leg keeps its own key. `CombinedSelectionState` gains
  `ebookDestinationKey` next to the existing `destinationKey`, which stays the
  audiobook's key. Each leg's payload carries its own key.

### 3. Upload target and book identity (Shelfmark backend)

**Key parsing.** A pure function `parse_grimmory_destination_key(key) -> (library_id,
path_id) | None` accepts only `grimmory:<digits>:<digits>`.

**Resolving the target.** In `build_booklore_config()`, library mode only:

- If the task's `destination_key` parses, and that (library, path) pair is in the live
  Grimmory library list, use it.
- Otherwise use the configured default, and log a warning when a key was given but was
  malformed or stale.
- Grimmory unreachable while validating means "stale": use the default. The upload call
  itself will then fail or succeed on its own terms.
- Bookdrop mode ignores the key.

**Protecting the key.** `authorize_destination_key()` is unchanged and strips the key from
any non-admin payload. Audiobook resolution never sees a `grimmory:` key, because an
Audiobookshelf library id never has that prefix: `resolve_destination_path` treats it as
stale and uses the default.

**Upload failure.** As today, a failed upload fails the task. There is no fallback to
another library: a misfiled book cannot be moved later.

**Book identity on the task.** `DownloadTask` gains `provider`, `provider_id`, `isbn_13`
and `asin`, all `str | None`.

| Path | Where the identity comes from |
|---|---|
| Direct download | `buildReleaseDownloadPayload` adds `provider`, `provider_id`, `isbn_13` (`book.isbn_13 ?? book.isbn_10`, canonicalized server-side) and `asin` from the metadata `Book`. Manual-provider books send none. |
| Approved request | `fulfil_request` copies those four from the request's stored `book_data` into `queued_release_data` when the release data lacks them. |
| Retry | Retries keep them in the retry payload, like `destination_key`. |

In `queue_release`, the server normalizes them: trimmed strings or `None`, and the ISBN
canonicalized to ISBN-13 or dropped.

**Hook payload.** The payload stays version 1; fields are only added.

- `task` gains `provider`, `provider_id`, `isbn_13` and `asin`.
- `output.details.booklore` gains `uploaded_files: [{"name": str, "size_bytes": int}]`,
  one per file actually uploaded, and `upload_started_at` (an ISO-8601 UTC timestamp taken
  just before the first upload).

### 4. Dispatcher and tagger hook (fleet-infra)

**Dispatcher.**

- **Location:** `.claude/skills/shelfmark-hooks/dispatch.py`. It is stdlib-only and
  shipped as a GENERATED ConfigMap `shelfmark-hook-dispatch`, mounted at
  `/opt/shelfmark-hooks` (mode 0755), with a `make-configmap.py` and a drift test like
  P1's.
- **Input:** it reads stdin once (the JSON payload) and keeps `argv[1]` (the target).
- **Routing:**
  - `task.content_type` containing "audiobook" goes to `/opt/abs-enrich/abs-enrich-hook.py`
  - an ebook (anything else, with `output.mode == "booklore"`) goes to
    `/opt/grimmory-tagger/grimmory_tagger.py hook`
  - anything else runs nothing
- **Running the hook:** it passes the same `argv[1]` and the same stdin bytes, with a
  280 s timeout. A timeout kills the child.
- **Exit:** it logs the child's exit code and the tail of its stderr to stdout, then
  **always exits 0**.
- **Bad payload:** an unparseable payload is logged, and the dispatcher exits 0.

**Tagger hook.** `grimmory_tagger.py hook [TARGET]` reads the payload from stdin, or from
`--payload FILE` for tests.

1. **Guard.** Run only when `task.content_type` is not audiobook,
   `output.details.booklore.destination == "library"` with a `library_id`, and
   `uploaded_files` is non-empty. Otherwise exit 0 silently.
2. **Queue first.** Append one `QueueEntry` per uploaded file (`append_entry`) before any
   network call. Each entry carries `task_id`, `library_id`, `path_id`, `filename`,
   `size`, title, author, series, language, `provider`, `provider_id`, `isbn_13`, `asin`
   and reason `"hook"`. A hook killed mid-run therefore never loses a book.
3. **Find the book.** Poll the library's books every 5 s for up to 60 s. A candidate is in
   `library_id`, with `primaryFile.fileSizeKb` within 1 KB of `size_bytes / 1024` and
   `primaryFile.addedOn` ≥ `upload_started_at − 120 s`.
   - **Exactly one candidate:** found.
   - **Several:** keep the one whose `fileName` equals the uploaded name. If that does not
     leave exactly one, the result is ambiguous.
   - **Recording the result:** a found `book_id` is written back onto the queue entry
     (rewrite under the queue lock), so later runs resolve it directly.
4. **Score.** Run the same identification and scoring `backfill` uses on that one book,
   with the queue entry as its hint, and save a one-book report (same format and
   directory).
5. **Outcome.**
   - **`accepted`:**
     - Write it immediately, through the same write and field-lock code `apply` uses.
     - Remove the queue entry.
     - Pushover: `Tagged: <title>` (ISBN, Hardcover ID).
   - **Everything else** (`review`, `conflict`, `no-match`, not found, ambiguous, Hardcover
     or Grimmory errors):
     - Keep the queue entry.
     - Pushover: `Needs review: <title> — <reason>`, naming the report id so
       `apply --report <id> --decisions …` works as in P1.
6. **Exit.** Always exit 0. A notification failure is logged, never raised.

**P1 change:** `resolve_entry` also matches by library + size (± 1 KB) when there is no
`book_id` and no filename match, with the same "exactly one" rule. Then a renamed upload
that the hook missed still resolves in a later `backfill`.

### 5. Manifest (fleet-infra, one commit, after the image is released)

- `media.shelfmark.yaml`:
  - bump the image
  - `BOOKLORE_DESTINATION=library`
  - `BOOKLORE_LIBRARY_ID=3`
  - `BOOKLORE_PATH_ID=3`
  - `CUSTOM_SCRIPT=/opt/shelfmark-hooks/dispatch.py`
  - add the `shelfmark-hook-dispatch` volume and mount
- `media/CLAUDE.md`, through `bin/nuance_bundle.md`:
  - the "ONE global upload destination / BookDrop" note becomes the picker plus direct
    upload
  - the tagger note drops "Not a CUSTOM_SCRIPT (yet)"
  - the custom-script note names the dispatcher

**Old image with the new manifest:** this is safe. The payload has no `uploaded_files`,
so the tagger hook's guard skips, and library mode with the Fiction default works on the
old image.

## Error handling

| Situation | Result |
|---|---|
| Grimmory unreachable when listing destinations | `[]`: the picker is hidden and the default is used |
| Stale or malformed ebook key | Default library, with a warning |
| Upload fails | Task fails (as today); never re-routed to another library |
| Hook: book never appears, or is ambiguous | Queue entry kept; "Needs review" Pushover; exit 0 |
| Hook: Hardcover or Grimmory error while scoring or writing | Queue entry kept; "Needs review"; exit 0 |
| Hook exceeds 280 s | Killed by the dispatcher; the queue entry already exists |
| Dispatcher receives a bad payload | Logged; exit 0 |
| Pushover fails | Logged |

## Testing

- **Shelfmark backend (pytest):**
  - the endpoint for both types, the 400 on a bad type, the admin gate, and ebook ordering
    and naming (single-path vs multi-path libraries)
  - `default_name`
  - `parse_grimmory_destination_key` (valid, malformed, non-digit)
  - target resolution: valid, stale, malformed, Grimmory down, bookdrop ignores the key
  - identity through direct download, request fulfil (filled from `book_data`) and retry
  - `uploaded_files` / `upload_started_at` in the payload
  - a non-admin key still stripped
- **Frontend (vitest):**
  - generic picker helpers for both formats and the default label
  - the release payload carries identity and the ebook key
  - combined mode sends a separate key per leg
- **fleet-infra (unittest, Python 3.13-compatible):**
  - **Dispatcher:** routing by type, stdin and argv passed through, kill on timeout,
    always exit 0, bad payload.
  - **Hook:**
    - the guard
    - queue written before network calls
    - polling: found by size+time, a tie broken by filename, never found, ambiguous
    - accepted means a write, the entry removed and a "Tagged" notice
    - review means the entry kept and a "Needs review" notice with the report id
  - `resolve_entry` size fallback.
  - ConfigMap drift test.
- **Gates:** `make python-checks python-test frontend-checks frontend-test`, and the
  fleet-infra skill tests.

## Rollout

The first plan task is a **probe**, run before any hook code is written. Upload one small
EPUB to a library via the API as the `shelfmark` account, then confirm:

1. the uploaded book's `fileName` (renamed or not)
2. `fileSizeKb` and `addedOn`
3. how long until it appears after `refresh`

The polling numbers (5 s / 60 s) and the ±1 KB tolerance are adjusted to the findings and
recorded in the spec before the hook is written.

Then, each push needing the user's OK:

1. Shelfmark: release the image (`scripts/release-local.sh`).
2. fleet-infra: the §5 commit (image bump, destination, dispatcher, mount, notes, drift
   link), then push and reconcile Flux.
3. Unpushed P3 bump `6525248` can be pushed on its own first, or be superseded by step 2.

## Acceptance

- Download an ebook while picking Light Novels: it appears in Light Novels, tagged (ISBN
  and Hardcover ID locked), with a "Tagged" Pushover.
- Download one with the default: it lands in Fiction.
- Approve an ebook request after changing the library in the approve panel: it lands in
  the chosen library.
- An audiobook download is still enriched by the ABS hook through the dispatcher.
- After the next Grimmory index sync, the new book badges as "In library" in Shelfmark.

## Out of scope

- Requester-side library choice.
- Moving already-misfiled books (Grimmory cannot; still a manual NFS `mv`).
- Making Grimmory's upload response or book id part of Shelfmark's model (the hook finds
  the book itself).
- Per-user default libraries beyond the existing user-overridable settings.
