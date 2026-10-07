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
  same module-level cache, now per content type, and still yields `[]` on error.
- The helpers in `utils/audiobookDestinations.ts` become generic, in
  `utils/downloadDestinations.ts`:
  - `shouldShowDestinationPicker(contentType, destinations)`: show when there is more than
    one destination, for either format.
  - `resolveDefaultDestinationKey`: unchanged behaviour.
  - Default-option label: `Default (<default_name>)` when the name is known, else
    `Default <format> destination` (ebooks always use the latter; see §1).
  - `withDestinationKey`: unchanged.
- **Release window:** `ReleaseModal.tsx` shows the picker for ebook releases too, under the
  same `canChooseDestination` gate (admin, not browse-fulfil mode).
- **Approval panel:** `ActivityCard.tsx` shows it for ebook requests.
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

**Protecting the key.** Authorization is unchanged:
- Authenticated non-admin keys are stripped (`authorize_destination_key()`, `main.py:1101`).
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
| Approved request | `fulfil_request` fills the four from the request's stored `book_data` when the release data lacks them (as a pair, per the rule above). |
| Combined and on-behalf downloads | Same builders, so they inherit the fields. A test covers each. |
| Retry and restart | Retries keep the fields in the retry payload and restore them, like `destination_key`. |

`queue_release` normalizes them: trimmed strings or `None`, and the ISBN canonicalized to
ISBN-13 or dropped.

**Hook payload.** The payload stays version 1; fields are only added.

- `task` gains `provider`, `provider_id`, `isbn_13` and `asin`.
- `output.details.booklore` gains:
  - `uploaded_files: [{"name": str, "size_bytes": int, "response": object|null}]`, one
    per file actually uploaded. `response` is Grimmory's parsed upload-response body when
    it is JSON, else `null`; today it is discarded.
  - `upload_started_at`: an ISO-8601 UTC timestamp taken just before the first upload.
  - `upload_finished_at`: taken after the last upload and refresh.

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
  280 s deadline.
  - On timeout, the dispatcher sends SIGTERM to the group, waits 5 s, sends SIGKILL to the
    group, and reaps.
  - Stdout and stderr go to files rather than pipes, so a descendant holding them open
    cannot hang the dispatcher.
- **Exit:**
  - It logs the child's exit code and the tail of its stderr.
  - Every launch, validation and output-handling exception is caught.
  - It **always exits 0**.
- **Shelfmark is unchanged.** A missing or non-executable dispatcher still fails a
  download, as any custom script does. That is a deployment error, and the rollout guards
  it (§5) rather than adding a special case to Shelfmark.

**Hook deadline.** The tagger hook works to one monotonic deadline (250 s from start,
inside the dispatcher's 280 s). It clamps every network timeout, retry backoff, poll wait
and lock wait to the remaining time, keeping 20 s in reserve to save its report and
notify. Notifications use a 10 s timeout. The P1 HTTP helpers gain an optional deadline
parameter; their existing callers are unchanged.

**Tagger hook.** `grimmory_tagger.py hook [TARGET]` reads the payload from stdin, or from
`--payload FILE` for tests.

1. **Guard.** Run only when `task.content_type` is not audiobook,
   `output.details.booklore.destination == "library"` with a `library_id`, and
   `uploaded_files` is non-empty. Otherwise exit 0 silently.
2. **Queue the whole batch first.** In one locked, atomic queue update (see "Queue
   integrity" below), add one `QueueEntry` per uploaded file before any network call. Each
   entry carries `task_id`, `library_id`, `path_id`, `filename`, `size`, title, author,
   series, language, `provider`, `provider_id`, `isbn_13`, `asin` and reason `"hook"`.
3. **Bind each file to its Grimmory book.** A binding must be *verified*; there is no
   guessing.
   - **Verified by the upload response:** the binding comes from the book or file id in
     `uploaded_files[].response`, if the probe (Rollout) shows Grimmory returns one. The
     book must exist in `library_id`.
   - **Verified by an exact file match:** otherwise the binding is the only book with all
     of:
     - in `library_id`, with `primaryFile.filePath` under the chosen path
     - `fileName` equal to the uploaded name (or to the name Grimmory's naming pattern
       produces, if the probe shows uploads are renamed and the pattern is
       deterministic)
     - `fileSizeKb` matching `size_bytes`
     - `addedOn` within [`upload_started_at` − 120 s, `upload_finished_at` + poll time]
   - **Waiting:** poll every 5 s until bound or until 60 s pass (clamped to the deadline).
   - **No verified binding:** the entry stays unresolved. There is no write and no
     suggestion acted on.
   - **On success:** the `book_id` is written onto the entry (atomic update), together
     with the filename it was verified against.
4. **Take the writer lock and score one book.** The hook takes the tagger's writer lock
   (below) before scoring.
   - **Scoring:** a new `score_books(book_ids, hint_entries)` function, extracted from
     `run_backfill`, scores exactly the given books. It loads the full inventory, so the
     ISBN-group and Hardcover-collision checks still see every book, and it uses only the
     given entries as hints. It never adds other queued books, unlike `run_backfill`'s
     `wanted |= set(queued)`.
   - **Report:** the hook saves a one-book report marked `kind: "hook"`.
5. **Write, under the same lock.** Only when the scoring outcome is `accepted`:
   - Apply through the same function `apply` uses, then **read the book back**.
   - **Success** means the final `ApplyResult` is `applied`, the book now carries a valid
     ISBN-13 and the Hardcover ID, and both are locked. Then the queue entry is removed
     and Pushover sends `Tagged: <title>` with the ISBN and Hardcover ID.
   - **Already complete:** an `unchanged` result whose book already carries both locked
     values counts as success, with the notice `Already tagged: <title>`.
   - **Anything else** keeps the entry: `stale`, `write-failed`, `lock-failed`, a missing
     goal value, or an accepted plan with no ISBN.
6. **Recovery instructions.** Everything that is not a success keeps its queue entry and
   sends `Needs attention: <title> — <reason>`. The notification names the right next step
   for the case:

   | Case | Next step |
   |---|---|
   | `review` or `conflict` row with a bound book | `apply --report <id> --decisions FILE`, as in P1 |
   | Unbound (not found, or no verified match) | The next full `backfill` retries the binding, using the filename rule and `book_id`. If it still cannot bind, run `backfill --book <id>` after finding the book in Grimmory. |
   | Dependency failure or timeout | The next `backfill` retries it automatically, since queued entries are always included. |

7. **Exit.** Always exit 0. A notification failure is logged, never raised.

**Writer lock and revalidation (P1 change).** One lock file,
`/config/grimmory-tagger/writer.lock` (flock), serializes every commit, whether made by
`apply` or by the hook.
- Under the lock, just before writing, the writer re-reads the target book. It refuses if
  the snapshot changed (the existing stale checks). It also re-checks inventory-wide
  collisions: if any other book now carries the same ISBN-13 or the same Hardcover ID in
  the same format, it refuses.
- If the lock cannot be taken within the remaining deadline, the hook leaves its entry
  queued and reports a timeout.

**Queue integrity (P1 change).**
- Every reader and writer of `grimmory-tag-queue.jsonl` takes a shared lock on a stable
  sibling file, `grimmory-tag-queue.lock`.
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
  never `kind: "hook"` reports.

**P1 `resolve_entry`.** It keeps resolving by `book_id` or by library + exact filename.
When it resolves by `book_id`, it also checks that the book is still in the entry's
library; otherwise the entry is unresolved. There is **no** size-only fallback.

### 5. Manifest (fleet-infra, one commit, after the image is released)

`media.shelfmark.yaml`:
- bump the image
- `BOOKLORE_DESTINATION=library`
- `BOOKLORE_LIBRARY_ID=3`
- `BOOKLORE_PATH_ID=3`
- `CUSTOM_SCRIPT=/opt/shelfmark-hooks/dispatch.py`
- keep `CUSTOM_SCRIPT_JSON_PAYLOAD=true`
- add the `shelfmark-hook-dispatch` volume and mount

The **same commit** regenerates the tagger ConfigMap, which carries the `hook` subcommand
and the P1 changes. A dispatcher that calls a tagger without `hook` would swallow a CLI
error on every ebook.

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
| Hook: no verified binding | Entry kept, unbound; "Needs attention" names the backfill route; exit 0 |
| Hook: scoring isn't `accepted`, or the apply/read-back isn't a full success | Entry kept; "Needs attention" with the matching next step; exit 0 |
| Hook: Hardcover or Grimmory error, lock wait, or deadline | Entry kept; next `backfill` retries; exit 0 |
| Hook exceeds 280 s | Dispatcher kills its process group; the queue entries already exist |
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
    cross-format key never resolves for ABS.
- **Frontend (vitest):**
  - generic picker helpers for both formats and the neutral ebook default label
  - every payload builder carries identity, and the ebook key when present
  - combined mode keeps per-phase keys through Next → Back → Next → Download, including
    skipped legs
- **fleet-infra (unittest, Python 3.13-compatible):**
  - **Dispatcher:**
    - routing by type, including mixed-case content type and the ABS out-of-root skip
    - stdin bytes and a spaced target path passed through
    - process-group kill on timeout, with a descendant keeping stdout open
    - missing or non-executable hook, malformed JSON
    - always exits 0
  - **Hook:**
    - the guard
    - the whole batch queued before any network call (multiple files)
    - binding: from the upload response; from an exact file match; never found; two
      books with the same size and no name match stay unbound
    - an accepted result that writes and verifies, a stale result, a write failure, a
      missing ISBN, and `unchanged`-already-complete
    - the deadline: a slow call, retries, a hanging notifier
  - **P1:**
    - queue: concurrent appends, removals and readers across processes, and an
      interrupted rewrite
    - the writer lock serializes apply and the hook, and collision revalidation refuses a
      duplicate Hardcover ID written in between
    - report ids are unique for simultaneous writers
    - `latest_report` ignores hook reports
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

Then, each push needing the user's OK:

1. Shelfmark: release the image (`scripts/release-local.sh`).
2. fleet-infra: the §5 commit (image bump, destination, dispatcher, regenerated tagger
   ConfigMap, mount, notes, drift link). Verify the pod's image and scripts, then push
   and reconcile Flux.
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
- Changing Shelfmark's custom-script failure policy.
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
  §5's pre-enable check.
- **#14, the receipts half:** incremental upload receipts. Books outside the queue are
  untagged, not lost, and the next full `backfill` covers them. The spec now states this
  durability boundary instead of "never loses a book".
