# Ebook Library Picker and Tagged Ingest (Project 2)

**Date:** 2026-10-06
**Status:** Approved design, revised after Codex review (see end), pending implementation plan
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
   `BOOKLORE_DESTINATION=library`. Precedence is: the admin's explicit choice, then the
   target user's effective setting (these settings are user-overridable), then the global
   default.
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
  (P1 probe findings). The 2026-10-07 ingest probe showed that a library upload is
  **renamed** from the EPUB's embedded metadata and that the upload response carries no id
  (see Rollout, "Probe result"), hence the upload-window binding in §4.
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
  - `default_name` is `""`. The library a blank key lands in depends on the target user's
    overrides (a fulfilled request runs as the requester), so the picker labels the blank
    option neutrally rather than naming a library it cannot be sure of.
  - The display list comes from the existing settings-options cache
    (`config/booklore_settings.py`). That cache is display-only: it never decides where a
    book goes (§3 validates against a fresh read).
- **Old endpoint:** `/api/audiobook-destinations` is removed. Its only caller is
  Shelfmark's own frontend, which ships in the same image.

### 2. Picker (Shelfmark frontend)

- `useAudiobookDestinations` becomes `useDownloadDestinations(contentType)`. It keeps the
  same module-level cache, now per content type, and still yields `[]` on error. Neither an
  error nor an empty list is cached: the server answers `200 []` while Grimmory is down, and
  the next picker must retry rather than stay hidden until a page reload.
- The helpers in `utils/audiobookDestinations.ts` become generic, in
  `utils/downloadDestinations.ts`:
  - `shouldShowDestinationPicker(contentType, destinations)`: show when there is more than
    one destination, for either format, and always while an ebook pick is selected.
  - `resolveDefaultDestinationKey`: unchanged behaviour, for audiobooks.
  - **An explicit ebook pick is never blanked in the browser** — not by display-list
    membership, a list that is still loading, or a hidden picker. It is sent unchanged, and
    the server's fresh check (§3) decides and fails closed. A pick missing from the list is
    shown as its own option so the admin can see and clear it. Audiobooks keep today's
    fallback: a key the list no longer has, or a hidden picker, sends nothing.
  - Default-option label: `Default (<default_name>)` when the name is known, else
    `Default <format> destination` (ebooks always use the latter; see §1).
  - `withDestinationKey`: unchanged.
- **Release window:** `ReleaseModal.tsx` shows the picker for ebook releases too, under the
  same `canChooseDestination` gate (admin, not browse-fulfil mode).
- **Approval panel:** `ActivityCard.tsx` shows it for ebook requests. Every action that ends
  in a download carries the pick — Approve, browse-before-approve, and Browse Alternatives
  (whose release window hides its own picker); only manual approval, which downloads
  nothing, carries none.
- **Combined mode:** each leg keeps its own key. Today the ebook→audiobook step passes only
  a release (`ReleaseModal` `onNext` has no destination argument), and the final download
  writes either phase's selection into one field. So:
  - `onNext` carries the current phase's destination key.
  - `CombinedSelectionState` holds `ebookDestinationKey` and `audiobookDestinationKey`
    (renaming today's `destinationKey`).
  - Going Back to a phase restores that phase's picker value.
  - Each leg's payload carries only its own key, including skipped legs and on-behalf
    confirmation. An ebook key is never sent on an audiobook leg.


### 3. Upload target and book identity (Shelfmark backend)

**Key parsing.** A pure function `parse_grimmory_destination_key(key) -> (library_id,
path_id) | None` accepts only `grimmory:<digits>:<digits>`.

**Resolving the target.** In `build_booklore_config()`, library mode only:

- **No key:** use the effective default for the task's user (their override, else the
  global setting), as today. Blank-key retries re-evaluate it, as today.
- **An explicit key:** before any upload, read `GET /api/v1/libraries` fresh with the
  upload credentials (not the display cache) and confirm the (library, path) pair exists.
  - Present: upload there.
  - Malformed, missing (the library or path no longer exists), or unverifiable (Grimmory
    unreachable): **the task fails before uploading** with an actionable error naming the
    key. It never falls back to the default: an admin who picked Light Novels must not get
    a book silently filed in Fiction, which cannot be moved afterwards.
- Bookdrop mode ignores the key.

**Protecting the key.**
- Authenticated non-admin keys are stripped (`authorize_destination_key()`, `main.py:1101`).
- The same guard runs on request submissions (`/api/requests` and `/api/requests/batch`),
  on `release_data` at the top level and in `extra`, before a request is stored or a
  download-policy submission is queued. Before this, a requester could plant a key there.
- Fulfilment ignores any key stored inside a request's `release_data` (top level or
  `extra`) and attaches only the approving admin's explicit key; a blank approval can no
  longer revive a stored nested key through `queue_release`.
- In auth mode `none`, direct-download callers keep their key, as today, and request
  workflows stay disabled in that mode.
- Ebook keys are never emitted on audiobook legs (§2). If one ever reaches Audiobookshelf
  resolution, it is an unmapped key there and follows ABS's existing stale-key fallback,
  which this project leaves unchanged.

**Upload failure.** As today, a failed upload fails the task. There is no fallback to
another library.

**Book identity on the task.** `DownloadTask` gains `provider`, `provider_id`, `isbn_13`
and `asin`, all `str | None`. `provider` and `provider_id` travel as a pair: a payload
that has one without the other drops both, and a request's identity never mixes with a
release's different provider.

Every producer is extended:

| Path | Change |
|---|---|
| Direct download | `buildReleaseDownloadPayload` adds the four fields from the metadata `Book`. The ISBN is `book.isbn_13 ?? book.isbn_10`, canonicalized server-side. Manual-provider books send none. |
| Book requests | `buildMetadataBookRequestData` also stores `isbn_13`/`isbn_10` (it already stores provider, provider_id and ASIN). |
| Release requests and the metadata release builder | `requestPayload.ts`'s release builder emits the four fields. |
| Browse-before-approve | `requestFulfil.ts` keeps ASIN and ISBNs when rebuilding the `Book` from a request. |
| Approved request | `fulfil_request` fills the four from the request's stored `book_data` when the release data lacks them (as a pair, per the rule above). A release that names a different provider but lost its id drops that half pair and imports nothing from the request, so one book's ISBN is never paired with another book's provider id. |
| Combined and on-behalf downloads | Same builders, so they inherit the fields. A test covers each. |
| Retry and restart | Retries keep the fields in the retry payload and restore them, like `destination_key`. |

`queue_release` normalizes them: trimmed strings or `None`, and the ISBN canonicalized to
ISBN-13 or dropped. A 13-digit value must carry the `978`/`979` prefix: other EANs can pass
the same check digit. This rule lives in the shared `normalize_isbn`, so library matching
gains it too.

**Hook payload.** The payload stays version 1; fields are only added.

- `task` gains `provider`, `provider_id`, `isbn_13` and `asin`.
- `output.details.booklore` gains:
  - `uploaded_files: [{"name": str, "size_bytes": int, "response": object|null}]`, one
    per file actually uploaded. `response` is Grimmory's parsed upload-response body when
    it is JSON, else `null`; today it is discarded.
  - `upload_started_at`: an ISO-8601 UTC timestamp taken just before the first upload.
  - `upload_finished_at`: taken after the last upload and refresh.

**The post-upload hook never fails an upload.** For the Grimmory output only, once the
upload has succeeded, a missing or non-executable custom script, a timeout, a non-zero
exit, or an exception while building the payload is logged as a warning, and the task
still completes as uploaded. Failing it would invite a retry that uploads the book again
into a library where it cannot be moved or deduplicated. Folder and email outputs keep
today's policy (a script failure fails the task).

**Durability boundary (stated, not over-promised).** The tag queue records a book only
once the hook runs. Some books are uploaded but not queued:
- a book from a task whose second file failed after the first uploaded
- a book whose process died before the hook ran

Those books are untagged, but never lost. Any full `backfill` scores every book in the
library, and the next one picks them up. This project does not add upload receipts.

### 4. Dispatcher and tagger hook (fleet-infra)

**Dispatcher.**

- **Location:** `.claude/skills/shelfmark-hooks/dispatch.py`. It is stdlib-only and
  shipped as a GENERATED ConfigMap `shelfmark-hook-dispatch`, mounted at
  `/opt/shelfmark-hooks` (mode 0755), with a `make-configmap.py` and a drift test like
  P1's.
- **Input:** it reads stdin bytes once and keeps `argv[1]` (the target). It parses the
  JSON and checks object shapes before reading fields; anything malformed is logged and
  the dispatcher exits 0.
- **Routing:** content type is classified with the backend's rule (case-insensitive
  "audiobook").
  - Audiobook: `[sys.executable, /opt/abs-enrich/abs-enrich-hook.py, target]`.
  - Ebook with `output.mode == "booklore"`:
    `[sys.executable, /opt/grimmory-tagger/grimmory_tagger.py, "hook", target]`.
  - Anything else runs nothing.
  - The same stdin bytes go to whichever hook runs. Each hook keeps its own guard. In
    particular, the ABS hook still skips audiobooks whose destination is outside its
    library root; that existing constraint is documented, not changed.
- **Timeout:** the child starts in its own process group (`start_new_session=True`) with a
  292 s deadline, so the ABS hook keeps essentially the budget it had as the custom script
  itself (Shelfmark kills the script at 300 s; the ABS hook itself is unchanged).
  - On timeout, the dispatcher sends SIGTERM to the group, waits 5 s, sends SIGKILL to the
    group, and reaps — done by 297 s.
  - If any exception escapes after the launch, the group is killed and reaped too.
  - Stdout and stderr go to files rather than pipes, so a descendant holding them open
    cannot hang the dispatcher; it reads at most a fixed tail of each.
- **Exit:**
  - It logs the child's exit code and the tails of its stdout and stderr, to its own
    stdout: Shelfmark discards a successful script's stderr but logs its stdout.
  - Logging never raises: a broken or closed stdout is swapped for `/dev/null` (otherwise
    Python's exit-time flush would turn the exit code into 120).
  - Every launch, validation and output-handling exception is caught.
  - It **always exits 0**.
- **Shelfmark side.** For Grimmory uploads, a missing or non-executable dispatcher no
  longer fails the download: it is logged, and the book stays uploaded but untagged until
  the next full `backfill` (§3). It is still a deployment error, and the rollout guards it
  (§5). Other outputs keep failing on a custom-script error, as today.

**Hook deadline.** The tagger hook works to one monotonic deadline (250 s from start,
inside the dispatcher's 292 s). It clamps every network timeout, retry backoff, poll wait
and lock wait to the remaining time, keeping 20 s in reserve to save its report and
notify. Notifications use a 10 s timeout. The P1 HTTP helpers gain an optional deadline
parameter; their existing callers are unchanged. A socket timeout applies per read, so a
trickling response or an endless event stream would never trip it: the work therefore
also runs under a wall-clock hard limit (SIGALRM, main thread), whose exception ends the
blocking call; the notifications run under their own hard limit inside the reserve.

**Tagger hook.** `grimmory_tagger.py hook [TARGET]` reads the payload from stdin, or from
`--payload FILE` for tests.

1. **Guard.** Run only when `task.content_type` is not audiobook,
   `output.details.booklore.destination == "library"` with a `library_id`, and
   `uploaded_files` is non-empty. Otherwise exit 0 silently.
2. **Queue the whole batch first.** In one locked, atomic queue update (see "Queue
   integrity" below), add one `QueueEntry` per uploaded file before any network call. Each
   entry carries `task_id`, `library_id`, `path_id`, `filename`, `size`, title, author,
   series, language, `provider`, `provider_id`, `isbn_13`, `asin` and reason `"hook"`.
   The queue lock is awaited only until the deadline; a busy or unwritable queue is
   treated as unqueued: "Needs attention — could not queue", and the next full
   `backfill` covers the book.
3. **Bind the upload to its Grimmory book (upload window).** A binding must be
   *verified*; there is no guessing. The probe (Rollout, 2026-10-07) ruled out both
   identity routes first planned: the upload answers HTTP 204 with an empty body (no book
   or file id), and Grimmory renames the file from the EPUB's embedded metadata (the
   uploaded name never matches). So the hook binds by the upload window, and only when
   all of these hold:
   - the task uploaded exactly **one** file; a multi-file task stays unbound
   - exactly **one** book in `library_id` — any size, any path — has an `addedOn` (UTC; a
     naive value is read as UTC) within [`upload_started_at` − 10 s,
     `upload_finished_at` + 10 s]; any other arrival in the window is ambiguous
   - that book's `primaryFile.filePath` is under the chosen path's root
   - its `fileSizeKb` is the floor or ceiling of `size_bytes`/1024
   - the same single result is seen on two consecutive polls (settling), so an arrival a
     moment later is caught. Grimmory rejects a second upload of the same file (409), so
     a duplicate copy cannot appear; window uniqueness would catch it anyway.
   - **Hydration:** a listing row missing any file identity field (`id`, `fileName`,
     `fileSizeKb`, `filePath`, `addedOn`) is completed from the full book before any
     filtering.
   - **Waiting:** poll every 5 s until bound or until 60 s pass (clamped to the deadline);
     the probe saw the book listed 3.3 s after the refresh.
   - **No verified binding:** the entry stays unresolved. There is no write and no
     suggestion acted on.
   - **On success:** the `book_id` is written onto the entry (atomic update), together
     with the stored (renamed) file name it was verified against.
4. **Take the writer lock and score one book.** The hook takes the tagger's writer lock
   (below) before scoring.
   - **Scoring:** a new `score_books(book_ids, hint_entries)` function, extracted from
     `run_backfill`, scores exactly the given books. It loads the full inventory, so the
     ISBN-group and Hardcover-collision checks still see every book, and it uses only the
     given entries as hints. It never adds other queued books, unlike `run_backfill`'s
     `wanted |= set(queued)`.
   - **Report:** the hook saves a one-book report marked `kind: "hook"`.
   - **Bound file:** if the scored book's primary file is no longer the bound file, that
     row is not written (entry kept, retried by the next `backfill`).
5. **Write, under the same lock.** Only when the scoring outcome is `accepted`:
   - Apply through the same function `apply` uses, then **read the book back**.
   - **Success** means the final `ApplyResult` is `applied`, the book now carries a valid
     ISBN-13 and the Hardcover ID, and both are locked. Then the queue entry is removed
     and Pushover sends `Tagged: <title>` with the ISBN and Hardcover ID.
   - **Already complete:** an `unchanged` result whose book already carries both locked
     values counts as success, with the notice `Already tagged: <title>`.
   - **Anything else** keeps the entry: `stale`, `write-failed`, `lock-failed`, a missing
     goal value, or an accepted plan with no ISBN.
   - **The same bar for `apply`:** `apply` removes a queue entry whose reason is `"hook"`
     only when this read-back finds both values complete, matching and locked, so a later
     `apply` of an incomplete book never drops its entry.
6. **Recovery instructions.** Everything that is not a success keeps its queue entry and
   sends `Needs attention: <title> — <reason>`. The notification names the right next step
   for the case:

   | Case | Next step |
   |---|---|
   | `review` row with a bound book | `apply --report <id> --decisions FILE`, as in P1 |
   | `conflict` row (decisions cannot choose one), or a collision found under the lock | Fix the conflicting metadata or binding in Grimmory, re-run `backfill --book <id>`, then apply the new report |
   | Unbound (multi-file task, nothing or more than one arrival in the window, or a path/size mismatch) | Find the book in Grimmory (added at the upload time) and run `backfill --book <id>`; the next full `backfill` also scores it like any other book. |
   | Dependency failure or timeout | The next `backfill` retries it automatically, since queued entries are always included. |

7. **Exit.** Always exit 0. A notification failure is logged, never raised.

**Writer lock and revalidation (P1 change).** One lock file,
`/config/grimmory-tagger/writer.lock` (flock), serializes every commit, whether made by
`apply` or by the hook.
- Under the lock, just before writing each row, the writer re-reads the target book. It
  refuses if the snapshot changed (the existing stale checks), and also if any scoring
  evidence changed: `backfill` snapshots the title, subtitle, language, publisher,
  library and primary-file identity (id, name, size) besides the planned fields (authors
  and series are already among them).
- Before each row that writes, it re-reads the inventory (keeping every value claimed
  earlier in the run) and re-checks collisions: if any other book now carries the same
  ISBN-13 or the same Hardcover ID, it refuses — unless the two books are duplicate
  holdings (one volume in two file formats), P1's existing rule.
- If the lock cannot be taken within the remaining deadline, the hook leaves its entry
  queued and reports a timeout.

**Queue integrity (P1 change).**
- Every reader and writer of `grimmory-tag-queue.jsonl` locks a stable sibling file,
  `grimmory-tag-queue.lock` — shared to read, exclusive to write. A reader checks whether
  the queue exists only once it holds the lock.
- The hook's queue operations wait for the lock only until its deadline (non-blocking
  polls); other callers wait as long as it takes.
- Writers read the current contents under the lock, build the new contents, write a temp
  file, flush and `fsync` it, `os.replace` it into place, `fsync` the directory, and only
  then release the lock.
- Updates and removals act on entry ids against freshly read contents. They keep unknown
  lines and concurrent additions, and never recreate an entry already removed.
- An interrupted rewrite leaves either the old file or the new one.

**Reports (P1 change).**
- Report ids gain sub-second precision and a short random suffix:
  `YYYYMMDDTHHMMSS.ffffffZ-xxxx`. Existing ids still load.
- A report is written to a temp file and published with `os.replace`.
- `latest_report` (the coverage baseline) considers only full-inventory backfill reports,
  never `kind: "hook"` reports. Each report records its `scope` (`full`, or `filtered` for
  `--library`/`--book` runs and hook reports), and the newest is chosen by its parsed
  creation time, never by file name (old and new id formats do not sort by time).

**P1 `resolve_entry`.** It keeps resolving by `book_id` or by library + exact filename.
When it resolves by `book_id`, it also checks that the book is still in the entry's
library; otherwise the entry is unresolved. There is **no** size-only fallback.

### 5. Manifest (fleet-infra, two commits, after the image is released)

`media.shelfmark.yaml`:
- bump the image
- `BOOKLORE_DESTINATION=library`
- `BOOKLORE_LIBRARY_ID=3`
- `BOOKLORE_PATH_ID=3`
- `CUSTOM_SCRIPT=/opt/shelfmark-hooks/dispatch.py`
- keep `CUSTOM_SCRIPT_JSON_PAYLOAD=true`
- add the `shelfmark-hook-dispatch` volume and mount

Two commits, each pushed with the user's OK, so the pre-enable checks below can run against
the live pod: **A** bumps the image and adds the dispatcher volume and mount (inert:
BookDrop and the ABS `CUSTOM_SCRIPT` unchanged); **B** sets the destination, the library
and path ids, `CUSTOM_SCRIPT`, and the notes. The tagger ConfigMap with the `hook`
subcommand and the P1 changes ships before A (it is regenerated with each tagger change),
so the dispatcher is never the custom script before the hook-capable tagger is mounted: a
dispatcher that calls a tagger without `hook` would swallow a CLI error on every ebook.

`media/CLAUDE.md`, through `bin/nuance_bundle.md`:
- the "ONE global upload destination / BookDrop" note becomes the picker plus direct
  upload
- the tagger note drops "Not a CUSTOM_SCRIPT (yet)"
- the custom-script note names the dispatcher and the ABS library-root constraint

**Before enabling library mode:**
- confirm the running pod has the new image (`kubectl get pod … -o jsonpath='{..image}'`)
- confirm `/opt/shelfmark-hooks/dispatch.py` and `grimmory_tagger.py hook --help` exist in
  the pod

**Mixed versions and rollback.** On the old image, the new manifest still uploads straight
to the effective default library (Fiction, or a user override), but its payload has no
`uploaded_files`, so the hook neither queues nor tags. Such books are untagged until the
next full `backfill`. This is acceptable for a rollback, but it does **not** meet
tagged-ingest acceptance.

## Error handling

| Situation | Result |
|---|---|
| Grimmory unreachable when listing destinations | Display list is `[]`: the picker is hidden and the effective default is used |
| Explicit ebook key is malformed, missing, or can't be verified | The task fails before upload, with an error naming the key; never re-routed |
| No key | Effective default for the task's user |
| Upload fails | Task fails (as today) |
| Custom script missing, failing, timed out, or its payload can't be built, after a successful Grimmory upload | Logged as a warning; the task completes as uploaded (book untagged until the next `backfill`) |
| A requester puts a destination key in a request's `release_data` | Stripped before the request is stored or queued |
| Hook: no verified binding (multi-file task, an ambiguous or empty upload window, a path or size mismatch) | Entry kept, unbound; "Needs attention" names the `backfill --book` route; exit 0 |
| Hook: scoring isn't `accepted`, or the apply/read-back isn't a full success | Entry kept; "Needs attention" with the matching next step; exit 0 |
| Hook: Hardcover or Grimmory error, lock wait, or deadline | Entry kept; next `backfill` retries; exit 0 |
| Hook exceeds 292 s | Dispatcher kills its process group; the queue entries already exist (the hook stops itself at 250 s, even inside a stalled read) |
| Dispatcher receives a malformed payload, or a launch fails | Logged; exit 0 |
| Pushover fails or hangs | 10 s timeout; logged |
| Uploaded but never queued (partial upload, crash before the hook) | Untagged until the next full `backfill` (stated durability boundary) |

## Testing

- **Shelfmark backend (pytest):**
  - **Endpoint:** both types, the 400 on a bad type, the admin gate, auth mode `none`, and
    ebook ordering and naming.
  - **Key parsing:** `parse_grimmory_destination_key`.
  - **Target resolution:** no key uses the effective default, including a user override
    and a blank-key retry. An explicit key that is valid, missing, malformed, or meets
    Grimmory down fails before upload, except the valid one. Bookdrop ignores the key.
  - **Identity, through every producer:** direct download, book request, release request,
    browse-before-approve, fulfil filling from `book_data`, combined, on-behalf, retry,
    and restart restore. Plus the provider/provider_id pair rule.
  - **Payload:** `uploaded_files` (with `response`), and the upload start and finish times.
  - **Keys:** a non-admin key is still stripped, a no-auth key is kept, and a
    cross-format key never resolves for ABS. Request submissions strip a non-admin's key
    on both endpoints, both policy paths and both locations; fulfilment sends only the
    approval's key.
  - **Hook failure after upload:** a missing or non-executable script, a timeout, a
    non-zero exit and a payload exception each leave the task completed, with a warning.
  - **ISBN:** a checksum-valid EAN without the `978`/`979` prefix is dropped.
- **Frontend (vitest):**
  - generic picker helpers for both formats and the neutral ebook default label
  - an ebook pick survives a loading or empty list; audiobook fallback unchanged
  - an empty list is not cached, so it recovers on the next lookup
  - Browse Alternatives carries the approve panel's pick to the fulfil payload
  - every payload builder carries identity, and the ebook key when present
  - combined mode keeps per-phase keys through Next → Back → Next → Download, including
    skipped legs
- **fleet-infra (unittest, Python 3.13-compatible):**
  - **Dispatcher:**
    - routing by type, including mixed-case content type and the ABS out-of-root skip
    - stdin bytes and a spaced target path passed through
    - process-group kill on timeout, with a descendant keeping stdout open; group cleanup
      when an exception escapes after the launch
    - missing or non-executable hook, malformed JSON
    - a broken or closed stdout through the real executable; a bounded output tail
    - always exits 0
  - **Hook:**
    - the guard
    - the whole batch queued before any network call (multiple files)
    - binding by the upload window: a single arrival binds only once settled (a renamed
      file name is irrelevant); two books in the window, a different-size book in the
      window, or a second copy appearing on a later poll stay unbound; a file outside
      the path root, a wrong size, an `addedOn` outside the window, or a multi-file task
      stay unbound; a naive `addedOn` is read as UTC; rows missing `primaryFile` or file
      fields are hydrated
    - an accepted result that writes and verifies, a stale result, a write failure, a
      missing ISBN, and `unchanged`-already-complete
    - the deadline: a slow call, retries, a hanging notifier, a held queue lock, and real
      loopback servers that trickle a JSON body or stream endless SSE heartbeats
    - a conflict names the fix-then-rescore route, never `--decisions`; a bound file
      replaced before scoring is not written; `apply` keeps an incomplete hook entry
  - **P1:**
    - queue: concurrent appends, removals and readers across processes, and an
      interrupted rewrite
    - the writer lock serializes apply and the hook, and collision revalidation refuses a
      duplicate Hardcover ID written in between, including one written by someone else
      after the run's first row
    - evidence revalidation refuses a title, language or primary-file change since scoring
    - the queue: a deadline-bounded wait on a held lock, and a queue created while a reader
      waited
    - report ids are unique for simultaneous writers
    - `latest_report` ignores hook reports and filtered runs, and orders by creation time
      (same-second mixed id formats)
    - `resolve_entry` checks the library on `book_id`
    - `score_books` scores exactly the given books
  - ConfigMap drift tests for both ConfigMaps.
- **Gates:** `make python-checks python-test frontend-checks frontend-test`, and the
  fleet-infra skill tests.

## Rollout

The first plan task is a **probe**, run before any hook code is written. As the
`shelfmark` account, upload one small EPUB to a library via the API, then record:

1. the upload response body (does it carry a book or file id?)
2. the stored `fileName` and `filePath` (renamed by a pattern or not)
3. `fileSizeKb` rounding
4. what `addedOn` means, and its timezone
5. how long until the book appears after `refresh`
6. what happens to a second upload of the same file

The binding rule in §4.3 is finalized from these findings and recorded in this spec
before the hook is written. The test book is then removed.

The probe must survive an interruption: before the first upload it persists and fsyncs a
receipt (tag, EPUB UUID, destination, file name), and it checkpoints every response and
every discovered book into it. Discovery validates each HTTP status and page shape and
follows Spring paging (`page.totalPages`); a probe book is one in the destination library
carrying an exact marker (the probe title or file name). Cleanup is complete only after a
successful full enumeration finds nothing and the NFS library root holds no file with the
probe tag.

**Probe result (2026-10-07):** tag `06c07b5d`, test book 306, Fiction (library 3, path 3).
The upload answered HTTP 204 with an empty body (no id); Grimmory renamed the file from the
embedded metadata (`… - Probe Author.epub`, absolute `filePath` under `/books/fiction`);
`fileSizeKb` = floor (1 for 1,743 bytes); `addedOn` in UTC with `Z`, inside the upload's
own start/finish; listed 3.3 s after the refresh; a second upload of the same file got HTTP
409 "File already exists"; listing rows carry every `primaryFile` field, and there is no
hash. The gate failed (neither a response id nor the uploaded name can bind), and the user
chose **upload-window binding** (§4.3): single-file tasks only; exactly one book of the
library with `addedOn` in [started − 10 s, finished + 10 s]; under the path root; size =
floor or ceiling of bytes/1024; the same result on two consecutive polls; `POLL_WINDOW` =
60 s; a naive `addedOn` read as UTC. The response-id and exact-name routes are dropped.
Details: fleet-infra `docs/superpowers/specs/2026-10-06-grimmory-tagger-probe-findings.md`
§8.

Then, each push needing the user's OK:

1. Shelfmark: release the image (`scripts/release-local.sh`).
2. fleet-infra, on `main` in the main checkout (the repo does not branch): §5 commit A
   (image bump, dispatcher volume and mount), push, reconcile, and verify the pod's image
   and scripts; then commit B (destination, ids, `CUSTOM_SCRIPT`, notes, drift link),
   push and reconcile. Each commit stages only its own files; the bundle note change is
   staged on top of `HEAD`, never with another session's pending bundle edits.
3. Unpushed P3 bump `6525248` can be pushed on its own first, or be superseded by step 2.

## Acceptance

- Download an ebook while picking Light Novels: it appears in Light Novels, tagged (ISBN
  and Hardcover ID locked and read back), with a "Tagged" Pushover.
- Download one with the default: it lands in your effective default (Fiction).
- Approve an ebook request after changing the library in the approve panel: it lands in
  the chosen library.
- Combined mode with different picks per leg: each leg lands where it was sent.
- Pick a library, then make its key stale (a test key): the task fails before upload, with
  nothing uploaded.
- An audiobook download is still enriched by the ABS hook through the dispatcher.
- After the next Grimmory index sync, the new book badges as "In library" in Shelfmark.

## Out of scope

- Requester-side library choice.
- Moving already-misfiled books (Grimmory cannot; still a manual NFS `mv`).
- Upload receipts for partial uploads or a crash before the hook (covered by the next
  full `backfill`).
- Changing Shelfmark's custom-script failure policy for folder and email outputs (the
  Grimmory post-upload hook is best-effort, §3).
- Per-user default libraries beyond the existing user-overridable settings.

## Revisions after Codex review (2026-10-06)

**Adopted:**

| # | Finding | Change |
|---|---|---|
| 1 | Explicit stale ebook keys failed open | Now they fail before upload |
| 2 | The cache isn't live | It is display-only; uploads validate against a fresh read |
| 3 | Size/time matching isn't identity | Bindings must be verified; the size-only fallback is dropped |
| 4 | Queue locking is unsafe | Stable lock file, atomic replace, fsync |
| 5 | `run_backfill` pulls in every queued book | New `score_books` |
| 6 | Concurrent writers can bypass collision checks | Writer lock with revalidation |
| 7 | `accepted` is not success | Branch on `ApplyResult` and read the book back |
| 8 | Combined-mode keys could be lost or swapped | Per-phase keys through Next/Back |
| 9 | Some identity producers were missing | Every producer listed |
| 10 | The per-user default isn't Fiction for everyone | Precedence defined; neutral default label |
| 11 | Killing the child alone leaves descendants | Process-group kill; outputs to files |
| 12 | No overall time limit | One deadline |
| 13 | Report ids collide and hook reports pollute the coverage baseline | Unique ids, atomic publish, hook reports excluded |
| 15 | Notifications gave the wrong next step | Per-case recovery instructions |
| 16 | The ABS root constraint, content-type case, and argv form were unstated | All documented |
| 17 | Rollout gaps | Same-commit tagger ConfigMap, pre-enable checks, honest rollback |
| 18 | No-auth and cross-format key behaviour was unstated | Clarified |

**Declined:**
- **#11, the Shelfmark-side half:** a best-effort custom-script policy in Shelfmark. The
  dispatcher catches everything, and a missing dispatcher is a deployment error guarded by
  §5's pre-enable check. *(Reversed for the Grimmory `post_upload` path on 2026-10-07; see
  below.)*
- **#14, the receipts half:** incremental upload receipts. Books outside the queue are
  untagged, not lost, and the next full `backfill` covers them. The spec now states this
  durability boundary instead of "never loses a book".

## Revisions after Codex review of the Shelfmark plan (2026-10-07)

All adopted:

| # | Finding | Change |
|---|---|---|
| 1 | A requester could plant a destination key in a request's `release_data` (top level or `extra`); download-policy submissions queued it, and a blank approval revived the nested copy | Request submissions run `authorize_destination_key`; fulfilment strips stored keys and sends only the approval's (§3) |
| 2 | Browse Alternatives in the approve panel dropped the chosen library | Every download action carries the pick (§2) |
| 3 | Frontend validation could blank an explicit ebook pick while the list loaded or the picker was hidden | A nonblank ebook pick is never blanked in the browser; the server decides (§2) |
| 4 | A post-upload hook failure failed an uploaded task, inviting a duplicate upload on retry | The Grimmory post-upload hook is best-effort (§3); this reverses the Shelfmark-side half of the earlier declined #11 for that path only |
| 5 | The fulfil fill could pair one book's ISBN with another book's provider id | A release naming a different provider without an id imports nothing (§3) |
| 6 | The frontend cached a successful empty list forever | Empty lists are never cached (§2) |
| 7 | A checksum-valid non-ISBN EAN passed ISBN normalization | 13-digit ISBNs need a `978`/`979` prefix, in the shared `normalize_isbn` (§3) |
