# Ebook Library Picker and Tagged Ingest — Shelfmark Side (Plan A of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin picks the Grimmory library an ebook goes into (release window, approve panel, combined mode); Shelfmark verifies that choice fresh, uploads straight there, and hands the custom-script hook the book's identity and what was uploaded — without a hook failure ever failing the upload, and without any non-admin path being able to choose a library.

**Architecture:** An ebook choice travels as the existing `destination_key`, spelled `grimmory:<libraryId>:<pathId>`. One endpoint, `GET /api/download-destinations?content_type=…`, lists choices for both formats (the ebook list comes from the cached Grimmory library list and is display-only); `build_booklore_config()` re-checks an explicit key against a fresh `GET /api/v1/libraries` and fails the task before upload if it cannot. `DownloadTask` gains `provider`, `provider_id`, `isbn_13`, `asin`, normalized once at queue time and carried by every producer (direct download, requests, fulfil, retry), and the version-1 hook payload gains those plus `uploaded_files` and the upload window.

**Tech Stack:** Python 3.14 (Flask, `requests`, SQLite), pytest, Ruff, BasedPyright, vulture; React 19 + TypeScript, vitest, oxlint, oxfmt, knip.

**Spec:** `docs/superpowers/specs/2026-10-06-ebook-library-picker-ingest-design.md` — sections 1, 2 and 3 only (including the 2026-10-07 revisions: request-key authorization, best-effort Grimmory post-upload hook, browser never blanking an ebook pick, empty lists not cached, `978`/`979` ISBN prefix). Read it before any task. Sections 4–5 (dispatcher, tagger hook, manifest) are Plan B in `fleet-infra` and are **out of scope here**.

## Global Constraints

- Ebook destination keys are exactly `grimmory:<libraryId>:<pathId>` with ASCII digits; anything else is malformed.
- An explicit ebook key is verified against a **fresh** `GET /api/v1/libraries` made with the upload credentials (never the settings cache). Malformed, missing or unverifiable → the task fails **before upload** with an error naming the key. It **never** falls back to the default library.
- No key → the effective default for the task's user (`BOOKLORE_LIBRARY_ID` / `BOOKLORE_PATH_ID`, user override first), re-evaluated on every attempt, as today. Bookdrop mode ignores the key.
- `GET /api/download-destinations?content_type=ebook|audiobook` returns `{"destinations": [{"key": str, "name": str}], "default_name": str}`; `default_name` is `""` for both types. Admin-only, open in auth mode `none`; a missing or unknown `content_type` is a 400. `/api/audiobook-destinations` is removed.
- Ebook option names: the library name, or `"<library> — <path>"` (em dash) when the library has more than one path; ordered by library name, then path. Not `booklore` output mode, or Grimmory unreachable → `[]`.
- Picker blank-option label: `Default (<default_name>)` when the name is known, else `Default <format> destination` (`Default ebook destination` / `Default audiobook destination`).
- Authorization: `authorize_destination_key()` (`shelfmark/main.py:1101`) still strips a non-admin's key on `/api/releases/download`; auth mode `none` keeps it. Do not edit that function. The same guard now also runs on `/api/requests` and `/api/requests/batch` submissions (both the stored-request and the download-policy path, top-level and `extra` keys), and fulfilment strips any key stored inside `release_data` so only the approving admin's explicit key travels (Task 6).
- An explicit **ebook** pick is never blanked in the browser — not by display-list membership, a list still loading, or a hidden picker. It is sent unchanged and the server's fresh check decides (fail closed). Audiobooks keep today's fallback (a key not in the list, or a hidden picker, sends nothing).
- The frontend never caches an empty destinations list (the server answers `200 []` while Grimmory is down).
- After a successful Grimmory upload, the `post_upload` custom script is **best-effort**: a missing or non-executable script, a timeout, a non-zero exit or an exception while building the payload is logged as a warning and the task still completes as uploaded. Folder and email outputs keep failing the task on a script failure, as today.
- Combined mode: each leg carries only its own key (`ebookDestinationKey` / `audiobookDestinationKey`); an ebook key is never sent on an audiobook leg.
- `provider` and `provider_id` travel as a pair: one without the other drops both. A request's identity never mixes with a release's different provider — including a release that names another provider but lost its id. ISBN is canonical ISBN-13 with a `978`/`979` prefix, or dropped; strings trimmed or `None`.
- Hook payload stays `"version": 1`; fields are only added: `task.provider`, `task.provider_id`, `task.isbn_13`, `task.asin` (str or null); `output.details.booklore.uploaded_files: [{"name": str, "size_bytes": int, "response": object|null}]`, `upload_started_at`, `upload_finished_at` (ISO-8601 UTC strings). Existing fields unchanged; `library_id`/`path_id` reflect the target actually used. This is the contract Plan B consumes — do not rename anything.
- Python: bare `except A, B:` (PEP 758) is valid Python 3.14 here — do not "fix" it.
- Gates before each commit: the task's own test command plus `uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests` (backend) or `cd src/frontend && npm run typecheck && npm run lint && npm run format:check && npm run test:unit` (frontend). Before release: `make python-checks python-test frontend-checks frontend-test`.
- Known pre-existing noise (not yours to fix): 9 failures in `tests/config/test_entrypoint_permissions.py` on macOS; 4 BasedPyright errors at `shelfmark/main.py:2305-2308`; `npm run knip` exits 1 on unused exports in `src/types/settings.ts` and `src/services/api.ts` that already exist on `main`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An ebook key in the wrong shape** — an audiobook key (`lib-kids`), padding, upper case, Unicode digits (`grimmory:³:4`), a missing part — is malformed and fails the upload; it is never trimmed into validity or re-routed. Test: `TestKeyFormat.test_rejects_anything_else` in Task 1.
2. **Junk ISBNs from metadata** — a bad check digit, a checksum-valid EAN without the `978`/`979` prefix (`1234567890128`), `"N/A"`, a zero-filled placeholder, a number or a boolean — are dropped, never forwarded as an ISBN. Test: `TestNormalizeBookIdentity.test_an_invalid_isbn_is_dropped` in Task 4.
3. **An upload response that is JSON but not an object** (`"OK"`, a list, `null`, a number) gives `response: null`, never a crash or a non-object in the payload. Test: `test_booklore_upload_file_returns_none_for_json_that_is_not_an_object` in Task 7.
4. **Grimmory returning ids as strings, or junk rows** in `GET /api/v1/libraries` — verification still matches `"3"` to `3`, and junk never matches. Test: `TestLibraryPathExists.test_string_ids_from_the_api_still_match` in Task 1.
5. **`content_type` spelled differently** (`Ebook`, ` ebook`, `AUDIOBOOK`) is a 400, not silently treated as one of the formats. Test: `TestContentTypeParameter.test_content_type_is_case_sensitive_and_untrimmed` in Task 3.

## Rulings on spec gaps

Where the spec is silent, this plan decides as follows (revised after a Codex review of the first version; the spec was updated where a finding changed one of its statements) (the reviewer should check these, not rediscover them):

- **Endpoint home.** The route moves to a new `shelfmark/core/destination_routes.py`; `shelfmark/audiobookshelf/routes.py` held only the old endpoint and is deleted (its tests move to `tests/core/test_destination_routes.py`).
- **Auth before validation.** A non-admin gets 403 even with a bad `content_type`; `content_type` must match `ebook`/`audiobook` exactly.
- **Display cache lifetime.** The ebook list reuses the settings-options cache as-is, keyed by credentials: a library added in Grimmory shows after a credential change, a "Test connection" in settings, or a restart. Uploads never depend on it. (A failed server-side read is never cached, and neither is an empty list in the browser.)
- **Where verification happens.** Inside `build_booklore_config()` (as the spec says), with its own login, so a bad key fails before staging; the upload logs in again as today.
- **`response`.** Only a JSON *object* body is kept; any other JSON or a non-JSON body is `null` (the contract says `object|null`). `size_bytes` is the prepared file's on-disk size just before its upload. Timestamps are `datetime.now(UTC).isoformat()` (`+00:00` offset).
- **Identity sources.** `queue_release` reads identity from top-level release fields only, never from a release source's `extra`. A `manual` provider carries no provider identity (frontend and backend). The ISBN is read from `isbn_13`, else `isbn_10`.
- **Fulfil fill rule.** A release with a complete provider pair keeps it and takes ISBN/ASIN from `book_data` only when `book_data` names the same pair (provider compared case-insensitively); a release without a complete pair takes `book_data`'s pair whole, plus any missing ISBN/ASIN.
- **Direct-browse mode** (`buildDirectRequestPayload`, `buildReleaseDataFromDirectBook`) is not in the spec's producer table and is left unchanged.
- **Combined Next/Back** carry the key the phase would send (`chosenDestinationKey`, from `destinationKeyToSend`): for an ebook that is the raw nonblank pick even while the list loads; for an audiobook only a listed key with the picker shown. Each restores the stored key of the phase being entered.
- **Unlisted ebook pick in the UI.** The picker stays visible while an ebook key is selected, and a key missing from the list is shown as its own option, `"<key> (not in the current list)"`, so the admin can see and clear it.
- **Request-submission keys.** The actor's own admin flag decides (an admin may still submit a key on a download-policy request); keys stored in a pending request are never used — fulfilment sends only the approval's explicit key.
- **Fill rule refinement.** A release that names a provider but has no id, against `book_data` naming a *different* provider, drops its half pair and imports nothing; with the same provider (case-insensitive) it takes `book_data`'s pair and hints. With no `book_data` identity nothing is adopted.
- **ISBN prefix.** `normalize_isbn` lives in `shelfmark/library/matching.py` and is shared with library matching and the Grimmory index provider. The `978`/`979` rule is added there, so a non-ISBN EAN also stops making a library match key — strictly safer under that module's "a false 'owned' is worse than a missed badge" rule; every existing library test fixture is `978`/`979` and stays green.
- **Best-effort hook scope.** Only `_post_process_booklore` changes (new `_run_post_upload_hook`); `maybe_run_custom_script` and the folder/email outputs are untouched. Script "error" statuses are captured and logged instead of being forwarded, so the task never flashes an error after a good upload. This reverses the spec's earlier "declined: Shelfmark-side best-effort policy" for this one path; the spec is updated to match.
- **Empty-list caching.** An empty list resolves normally but is evicted from the cache, so the next picker mount retries.
- **Hook shape.** `useDownloadDestinations(contentType | null)` returns `{ destinations, defaultName }`; `null` skips the lookup, and a list loaded for the other format is never returned.

## File Structure

| File | Change |
|---|---|
| `shelfmark/grimmory/destinations.py` | **new** — key format/parse, `library_path_exists`, `build_destination_options` (pure) |
| `shelfmark/download/outputs/booklore.py` | explicit-key verification in `build_booklore_config`; upload returns the JSON body; `uploaded_files` + upload window in the hook details; best-effort `_run_post_upload_hook` |
| `shelfmark/config/booklore_settings.py` | cache also holds ebook picker options; `get_booklore_destination_options()` |
| `shelfmark/core/destination_routes.py` | **new** — `GET /api/download-destinations` |
| `shelfmark/audiobookshelf/routes.py` | **deleted** (held only `/api/audiobook-destinations`) |
| `shelfmark/main.py` | register the new route module instead of the old one |
| `shelfmark/core/book_identity.py` | **new** — `BookIdentity`, `normalize_book_identity`, `fill_identity_from_book_data` |
| `shelfmark/core/models.py` | `DownloadTask.provider/provider_id/isbn_13/asin` |
| `shelfmark/download/orchestrator.py` | identity in `queue_release`, retry serialize/restore |
| `shelfmark/core/requests_service.py` | `fulfil_request` fills identity from `book_data`; strips keys stored inside `release_data` |
| `shelfmark/core/request_routes.py` | request submissions run `authorize_destination_key` on `release_data` |
| `shelfmark/library/matching.py` | `normalize_isbn` requires a `978`/`979` prefix for 13 digits |
| `shelfmark/download/postprocess/custom_script.py` | four identity fields in `task` |
| `src/frontend/src/utils/combinedSelection.ts` | **new** — combined-mode state type and pure transitions with per-phase keys |
| `src/frontend/src/App.tsx` | uses the combined-selection helpers; passes staged keys to the modal |
| `src/frontend/src/components/ReleaseModal.tsx` | `onNext`/`onBack` carry the key; per-phase restore; generic picker |
| `src/frontend/src/utils/downloadDestinations.ts` | **renamed** from `audiobookDestinations.ts`; generic helpers, label, loader |
| `src/frontend/src/hooks/useDownloadDestinations.ts` | **renamed** from `useAudiobookDestinations.ts`; per-type cache |
| `src/frontend/src/services/api.ts` | `getDownloadDestinations(contentType)`; identity on `DownloadReleasePayload` |
| `src/frontend/src/components/activity/ActivityCard.tsx` | approve-panel picker for ebook requests; every download action carries the pick |
| `src/frontend/src/components/activity/reviewApproval.ts` | **new** — `RequestApproveOptions`, `reviewApproveOptions(action, key)` |
| `src/frontend/src/utils/bookIdentity.ts` | **new** — `bookIdentityFields(book)` |
| `src/frontend/src/utils/{releasePayload,requestPayload,requestFulfil}.ts` | identity in every builder |
| Backend tests | `tests/grimmory/test_destinations.py` (new), `tests/core/test_booklore_multiuser.py`, `tests/core/test_booklore_target.py` (new), `tests/core/test_destination_routes.py` (new, replaces `tests/audiobookshelf/test_routes.py`), `tests/core/test_auth_mode_fail_closed.py` (docstring), `tests/core/test_book_identity.py` (new), `tests/download/test_orchestrator_identity.py` (new), `tests/library/test_matching.py`, `tests/core/test_request_identity.py` (new), `tests/core/test_request_destination_authorization.py` (new), `tests/core/test_download_api_guardrails.py`, `tests/audiobookshelf/test_routing.py`, `tests/core/test_booklore_payload.py` (new), `tests/core/test_booklore_upload.py`, `tests/core/test_download_processing.py` |
| Frontend tests | `src/tests/combinedSelection.test.ts` (new), `src/tests/downloadDestinations.test.ts` (renamed from `audiobookDestinations.test.ts`), `src/tests/reviewApproval.test.ts` (new), `src/tests/releasePayload.test.ts`, `src/tests/requestPayload.test.ts`, `src/tests/requestFulfil.test.ts` |

Task order note: the combined-mode refactor (Task 8) lands **before** the ebook picker is switched on (Task 9). The other way round, an ebook key picked on the last combined step would be written into today's single `destinationKey` and sent on the audiobook leg for one commit.

---
### Task 1: Grimmory destination keys and picker options (pure)

**Files:**
- Create: `shelfmark/grimmory/destinations.py`
- Test: `tests/grimmory/test_destinations.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `GRIMMORY_KEY_PREFIX = "grimmory:"`; `grimmory_destination_key(library_id: int, path_id: int) -> str`; `parse_grimmory_destination_key(key: object) -> tuple[int, int] | None`; `library_path_exists(libraries: object, library_id: int, path_id: int) -> bool` (takes raw `GET /api/v1/libraries` JSON); `build_destination_options(libraries: object) -> list[dict[str, str]]` (each `{"key", "name"}`, sorted).

- [ ] **Step 1: Write the failing tests**

Create `tests/grimmory/test_destinations.py`:

```python
"""Tests for Grimmory upload destination keys (`grimmory:<libraryId>:<pathId>`)."""

from shelfmark.grimmory.destinations import (
    build_destination_options,
    grimmory_destination_key,
    library_path_exists,
    parse_grimmory_destination_key,
)

LIBRARIES = [
    {
        "id": 5,
        "name": "Light Novels",
        "paths": [{"id": 8, "path": "/books/light-novels"}],
    },
    {
        "id": 3,
        "name": "Fiction",
        "paths": [
            {"id": 4, "path": "/books/fiction-b"},
            {"id": 3, "path": "/books/fiction-a"},
        ],
    },
]


class TestKeyFormat:
    def test_builds_the_namespaced_key(self):
        assert grimmory_destination_key(3, 4) == "grimmory:3:4"

    def test_parses_a_well_formed_key(self):
        assert parse_grimmory_destination_key("grimmory:3:4") == (3, 4)

    def test_round_trips(self):
        assert parse_grimmory_destination_key(grimmory_destination_key(12, 40)) == (12, 40)

    def test_rejects_anything_else(self):
        # Review Focus #1: an audiobook key, junk, padding and non-ASCII digits are
        # all malformed. Whitespace is not trimmed here: the queue already trims.
        for key in (
            "",
            "lib-kids",
            "grimmory:3",
            "grimmory:3:4:5",
            "grimmory:a:4",
            "grimmory:3:-4",
            "grimmory: 3:4",
            " grimmory:3:4",
            "grimmory:3:4 ",
            "GRIMMORY:3:4",
            "grimmory:³:4",
            "grimmory::4",
            None,
            34,
        ):
            assert parse_grimmory_destination_key(key) is None, repr(key)


class TestLibraryPathExists:
    def test_finds_a_path_in_its_library(self):
        assert library_path_exists(LIBRARIES, 3, 4) is True

    def test_a_path_of_another_library_does_not_count(self):
        assert library_path_exists(LIBRARIES, 3, 8) is False

    def test_a_missing_library(self):
        assert library_path_exists(LIBRARIES, 99, 3) is False

    def test_string_ids_from_the_api_still_match(self):
        libraries = [{"id": "3", "paths": [{"id": "4"}]}]

        assert library_path_exists(libraries, 3, 4) is True

    def test_junk_payloads_never_match(self):
        for libraries in (None, {}, "3", [None, "x", {"id": 3, "paths": "4"}]):
            assert library_path_exists(libraries, 3, 4) is False, repr(libraries)


class TestBuildDestinationOptions:
    def test_one_option_per_library_path_sorted_by_library_then_path(self):
        assert build_destination_options(LIBRARIES) == [
            {"key": "grimmory:3:3", "name": "Fiction — /books/fiction-a"},
            {"key": "grimmory:3:4", "name": "Fiction — /books/fiction-b"},
            {"key": "grimmory:5:8", "name": "Light Novels"},
        ]

    def test_a_single_path_library_is_named_by_the_library_alone(self):
        options = build_destination_options([{"id": 1, "name": "Manga", "paths": [{"id": 2}]}])

        assert options == [{"key": "grimmory:1:2", "name": "Manga"}]

    def test_unnamed_libraries_and_paths_get_fallback_names(self):
        options = build_destination_options([{"id": 7, "paths": [{"id": 1}, {"id": 2}]}])

        assert options == [
            {"key": "grimmory:7:1", "name": "Library 7 — Path 1"},
            {"key": "grimmory:7:2", "name": "Library 7 — Path 2"},
        ]

    def test_sorting_ignores_case(self):
        options = build_destination_options(
            [
                {"id": 1, "name": "zebra", "paths": [{"id": 1}]},
                {"id": 2, "name": "Apple", "paths": [{"id": 2}]},
            ]
        )

        assert [option["name"] for option in options] == ["Apple", "zebra"]

    def test_skips_rows_that_cannot_make_a_key(self):
        options = build_destination_options(
            [
                None,
                {"name": "No id", "paths": [{"id": 1}]},
                {"id": 2, "name": "No paths"},
                {"id": 3, "name": "Bad paths", "paths": "x"},
                {"id": 4, "name": "Pathless rows", "paths": [None, {"path": "/x"}]},
                {"id": True, "name": "Bool id", "paths": [{"id": 1}]},
                {"id": "x", "name": "Text id", "paths": [{"id": 1}]},
            ]
        )

        assert options == []

    def test_a_non_list_payload_gives_no_options(self):
        assert build_destination_options({"content": []}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/grimmory/test_destinations.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shelfmark.grimmory.destinations'` (collection error).

- [ ] **Step 3: Implement `shelfmark/grimmory/destinations.py`**

```python
"""Grimmory upload destinations: the `grimmory:<libraryId>:<pathId>` key (fork-only).

An admin picks a (library, path) pair for an ebook; the pair travels as an
opaque `destination_key`, the same field audiobook routing uses. The key is
only ever *trusted* after it has been checked against a fresh library listing
(see `shelfmark.download.outputs.booklore`): the options built here are for
display only.
"""

from __future__ import annotations

import re
from typing import Any

GRIMMORY_KEY_PREFIX = "grimmory:"

# ASCII digits only: `\d` would also accept "³" and other Unicode digits.
_KEY_PATTERN = re.compile(r"grimmory:([0-9]+):([0-9]+)")


def grimmory_destination_key(library_id: int, path_id: int) -> str:
    """Build the destination key for one Grimmory library path."""
    return f"{GRIMMORY_KEY_PREFIX}{library_id}:{path_id}"


def parse_grimmory_destination_key(key: object) -> tuple[int, int] | None:
    """Return `(library_id, path_id)` for a well-formed key, else None.

    Strict on purpose: a key that is almost right (padded, upper-cased, an
    audiobook key) is a key that names nothing, and the upload must fail
    rather than guess.
    """
    if not isinstance(key, str):
        return None
    match = _KEY_PATTERN.fullmatch(key)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _row_id(value: object) -> int | None:
    """Read a Grimmory id that may arrive as an int or a digit string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _library_paths(library: object) -> list[dict[str, Any]]:
    if not isinstance(library, dict):
        return []
    paths = library.get("paths")
    if not isinstance(paths, list):
        return []
    return [
        path for path in paths if isinstance(path, dict) and _row_id(path.get("id")) is not None
    ]


def library_path_exists(libraries: object, library_id: int, path_id: int) -> bool:
    """Whether `GET /api/v1/libraries` output contains this exact library path."""
    if not isinstance(libraries, list):
        return False
    for library in libraries:
        if not isinstance(library, dict) or _row_id(library.get("id")) != library_id:
            continue
        if any(_row_id(path.get("id")) == path_id for path in _library_paths(library)):
            return True
    return False


def build_destination_options(libraries: object) -> list[dict[str, str]]:
    """Build picker options, one per (library, path), ordered by library then path.

    A library with a single path is named by the library alone; with more, each
    option is "<library> — <path>" so the admin can tell them apart.
    """
    if not isinstance(libraries, list):
        return []

    rows: list[tuple[str, str, dict[str, str]]] = []
    for library in libraries:
        if not isinstance(library, dict):
            continue
        library_id = _row_id(library.get("id"))
        paths = _library_paths(library)
        if library_id is None or not paths:
            continue

        library_name = str(library.get("name") or f"Library {library_id}")
        for path in paths:
            path_id = _row_id(path.get("id"))
            if path_id is None:
                continue
            path_name = str(path.get("path") or f"Path {path_id}")
            name = library_name if len(paths) == 1 else f"{library_name} — {path_name}"
            rows.append(
                (
                    library_name.casefold(),
                    path_name.casefold(),
                    {"key": grimmory_destination_key(library_id, path_id), "name": name},
                )
            )

    rows.sort(key=lambda row: (row[0], row[1]))
    return [option for _, _, option in rows]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/grimmory -q`
Expected: PASS (15 new tests, plus the existing Grimmory tests).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/grimmory tests/grimmory && uv run ruff format --check shelfmark/grimmory tests/grimmory
uv run basedpyright shelfmark/grimmory
git add shelfmark/grimmory/destinations.py tests/grimmory/test_destinations.py
git commit -m "feat(grimmory): destination keys and picker options

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: Verify an explicit ebook key before upload; no key keeps the effective default

**Files:**
- Modify: `shelfmark/download/outputs/booklore.py` (imports; new `_verify_explicit_destination`; `build_booklore_config` signature and library branch; the call in `_post_process_booklore`)
- Test: `tests/core/test_booklore_multiuser.py` (append), `tests/core/test_booklore_target.py` (new)

**Interfaces:**
- Consumes: `parse_grimmory_destination_key`, `library_path_exists` (Task 1); `booklore_login`, `booklore_list_libraries`, `BookloreConfig`, `BookloreError` from `shelfmark.grimmory.client`.
- Produces: `build_booklore_config(values: Mapping[str, Any], user_id: int | None = None, destination_key: str | None = None) -> BookloreConfig` — raises `BookloreError` whose message contains the key when an explicit key is malformed, missing from Grimmory, or cannot be verified. `shelfmark.download.outputs.booklore` now imports `booklore_list_libraries` (tests patch it there). `_post_process_booklore` passes `task.destination_key`.

- [ ] **Step 1: Write the failing tests**

In `tests/core/test_booklore_multiuser.py`, replace the import line

```python
from shelfmark.download.outputs.booklore import build_booklore_config
```

with

```python
import pytest

from shelfmark.download.outputs.booklore import BookloreError, build_booklore_config
```

(the module docstring stays first; `import pytest` goes on the line after it, separated by a blank line). Then append to the end of the file:

```python
class FakeGrimmory:
    """Stands in for login + `GET /api/v1/libraries` during target resolution."""

    def __init__(self, libraries=None, error=None):
        self.libraries = libraries if libraries is not None else []
        self.error = error
        self.list_calls = 0
        self.login_configs = []

    def login(self, booklore_config):
        self.login_configs.append(booklore_config)
        if self.error is not None:
            raise self.error
        return "token"

    def list_libraries(self, booklore_config, token):
        self.list_calls += 1
        assert token == "token"
        return self.libraries


LIBRARIES = [
    {"id": 3, "name": "Fiction", "paths": [{"id": 3, "path": "/books/fiction"}]},
    {"id": 5, "name": "Light Novels", "paths": [{"id": 8, "path": "/books/ln"}]},
]


class TestExplicitDestinationKey:
    """An admin's ebook library choice is verified fresh, and never falls back."""

    BASE_SETTINGS = TestBuildBookloreConfigWithOverrides.BASE_SETTINGS

    @pytest.fixture
    def grimmory(self, monkeypatch):
        fake = FakeGrimmory(libraries=LIBRARIES)
        monkeypatch.setattr("shelfmark.download.outputs.booklore.booklore_login", fake.login)
        monkeypatch.setattr(
            "shelfmark.download.outputs.booklore.booklore_list_libraries", fake.list_libraries
        )
        return fake

    def test_a_verified_key_sets_the_upload_target(self, grimmory):
        config = build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:5:8")

        assert (config.library_id, config.path_id) == (5, 8)
        assert config.upload_to_bookdrop is False
        assert grimmory.list_calls == 1

    def test_the_key_beats_a_user_override(self, grimmory, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 3
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 3
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)

        config = build_booklore_config(
            self.BASE_SETTINGS, user_id=7, destination_key="grimmory:5:8"
        )

        assert (config.library_id, config.path_id) == (5, 8)

    def test_verification_uses_the_upload_credentials(self, grimmory):
        build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:5:8")

        used = grimmory.login_configs[0]
        assert (used.base_url, used.username, used.password) == (
            "http://booklore:6060",
            "admin",
            "secret",
        )

    def test_a_key_works_without_any_default_configured(self, grimmory):
        settings = {
            key: value
            for key, value in self.BASE_SETTINGS.items()
            if key not in {"BOOKLORE_LIBRARY_ID", "BOOKLORE_PATH_ID"}
        }

        config = build_booklore_config(settings, destination_key="grimmory:3:3")

        assert (config.library_id, config.path_id) == (3, 3)

    def test_a_missing_library_fails_naming_the_key(self, grimmory):
        with pytest.raises(BookloreError, match=r"grimmory:9:9"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:9:9")

    def test_a_path_from_another_library_fails(self, grimmory):
        with pytest.raises(BookloreError, match=r"grimmory:3:8"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:3:8")

    def test_a_malformed_key_fails_without_calling_grimmory(self, grimmory):
        # Review Focus #1: an audiobook key on an ebook task is malformed here.
        with pytest.raises(BookloreError, match=r"lib-kids"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="lib-kids")

        assert grimmory.login_configs == []

    def test_grimmory_down_fails_instead_of_using_the_default(self, monkeypatch):
        fake = FakeGrimmory(error=BookloreError("Could not connect to Grimmory"))
        monkeypatch.setattr("shelfmark.download.outputs.booklore.booklore_login", fake.login)

        with pytest.raises(BookloreError, match=r"grimmory:5:8.*Could not connect"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:5:8")

    def test_no_key_never_calls_grimmory(self, grimmory):
        for blank in (None, "", "   "):
            config = build_booklore_config(self.BASE_SETTINGS, destination_key=blank)
            assert (config.library_id, config.path_id) == (1, 10)

        assert grimmory.login_configs == []

    def test_bookdrop_ignores_the_key(self, grimmory):
        settings = {**self.BASE_SETTINGS, "BOOKLORE_DESTINATION": "bookdrop"}

        config = build_booklore_config(settings, destination_key="grimmory:9:9")

        assert config.upload_to_bookdrop is True
        assert (config.library_id, config.path_id) == (0, 0)
        assert grimmory.login_configs == []
```

Create `tests/core/test_booklore_target.py`:

```python
"""End-to-end Grimmory upload targeting: an explicit library, or the effective default."""

from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask, SearchMode

LIBRARIES = [
    {"id": 3, "name": "Fiction", "paths": [{"id": 3, "path": "/books/fiction"}]},
    {"id": 5, "name": "Light Novels", "paths": [{"id": 8, "path": "/books/ln"}]},
]

SETTINGS = {
    "BOOKS_OUTPUT_MODE": "booklore",
    "BOOKLORE_HOST": "http://grimmory:6060",
    "BOOKLORE_USERNAME": "shelfmark",
    "BOOKLORE_PASSWORD": "secret",
    "BOOKLORE_DESTINATION": "library",
    "BOOKLORE_LIBRARY_ID": 3,
    "BOOKLORE_PATH_ID": 3,
}


def _task(destination_key=None, user_id=None):
    return DownloadTask(
        task_id="ebook-1",
        source="direct_download",
        title="Overlord",
        author="Kugane Maruyama",
        format="epub",
        search_mode=SearchMode.DIRECT,
        destination_key=destination_key,
        user_id=user_id,
    )


def _run(tmp_path, task, *, settings=None, user_overrides=None, libraries=LIBRARIES):
    """Run the real post-processing pipeline with Grimmory's HTTP calls stubbed."""
    from shelfmark.download.postprocess.router import post_process_download

    values = settings or SETTINGS
    overrides = user_overrides or {}
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    temp_file = staging / "book.epub"
    temp_file.write_text("content")

    statuses = []
    uploads = []
    refreshes = []

    def config_get(key, default=None, user_id=None, **_kwargs):
        if user_id is not None and (user_id, key) in overrides:
            return overrides[(user_id, key)]
        return values.get(key, default)

    def upload(booklore_config, _token, file_path):
        uploads.append((booklore_config.library_id, booklore_config.path_id, file_path.name))

    def refresh(booklore_config, _token):
        refreshes.append(booklore_config.library_id)

    with (
        patch("shelfmark.core.config.config") as mock_config,
        patch("shelfmark.config.env.TMP_DIR", staging),
        patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"),
        patch(
            "shelfmark.download.outputs.booklore.booklore_list_libraries",
            return_value=libraries,
        ) as list_libraries,
        patch("shelfmark.download.outputs.booklore.booklore_upload_file", side_effect=upload),
        patch("shelfmark.download.outputs.booklore.booklore_refresh_library", side_effect=refresh),
    ):
        mock_config.get = MagicMock(side_effect=config_get)
        mock_config.CUSTOM_SCRIPT = None
        result = post_process_download(
            temp_file, task, Event(), lambda status, message: statuses.append((status, message))
        )

    return result, uploads, refreshes, statuses, list_libraries.call_count


def test_an_explicit_key_uploads_to_the_chosen_library(tmp_path):
    result, uploads, refreshes, _, list_calls = _run(tmp_path, _task("grimmory:5:8"))

    assert result == "booklore://ebook-1"
    assert uploads == [(5, 8, "book.epub")]
    assert refreshes == [5]
    assert list_calls == 1


def test_a_stale_key_fails_before_anything_is_uploaded(tmp_path):
    result, uploads, _, statuses, _ = _run(tmp_path, _task("grimmory:9:9"))

    assert result is None
    assert uploads == []
    errors = [message for status, message in statuses if status == "error"]
    assert errors
    assert "grimmory:9:9" in errors[-1]


def test_a_malformed_key_fails_before_anything_is_uploaded(tmp_path):
    result, uploads, _, statuses, list_calls = _run(tmp_path, _task("lib-kids"))

    assert result is None
    assert uploads == []
    assert list_calls == 0
    assert any(status == "error" and "lib-kids" in message for status, message in statuses)


def test_no_key_uses_the_users_effective_default(tmp_path):
    overrides = {(7, "BOOKLORE_LIBRARY_ID"): 5, (7, "BOOKLORE_PATH_ID"): 8}

    result, uploads, _, _, list_calls = _run(tmp_path, _task(user_id=7), user_overrides=overrides)

    assert result == "booklore://ebook-1"
    assert uploads == [(5, 8, "book.epub")]
    assert list_calls == 0


def test_a_blank_key_retry_re_evaluates_the_default(tmp_path):
    """The user's override changed between the first attempt and the retry."""
    first = _run(tmp_path, _task(user_id=7))
    retried = _run(
        tmp_path,
        _task(user_id=7),
        user_overrides={(7, "BOOKLORE_LIBRARY_ID"): 5, (7, "BOOKLORE_PATH_ID"): 8},
    )

    assert first[1] == [(3, 3, "book.epub")]
    assert retried[1] == [(5, 8, "book.epub")]


def test_bookdrop_mode_ignores_the_key(tmp_path):
    settings = {**SETTINGS, "BOOKLORE_DESTINATION": "bookdrop"}

    result, uploads, refreshes, _, list_calls = _run(
        tmp_path, _task("grimmory:9:9"), settings=settings
    )

    assert result == "booklore://ebook-1"
    assert uploads == [(0, 0, "book.epub")]
    assert refreshes == []
    assert list_calls == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_booklore_multiuser.py tests/core/test_booklore_target.py -q`
Expected: FAIL — `AttributeError: module 'shelfmark.download.outputs.booklore' has no attribute 'booklore_list_libraries'` in every new test's patch or fixture (9 errors in `TestExplicitDestinationKey`, 6 failures in `test_booklore_target.py`), plus `TypeError: build_booklore_config() got an unexpected keyword argument 'destination_key'` in `test_grimmory_down_fails_instead_of_using_the_default` (it patches only the login). The seven existing override tests still pass.

- [ ] **Step 3: Implement in `shelfmark/download/outputs/booklore.py`**

Extend the client import and add the destinations import. Replace:

```python
    BookloreError,
    booklore_login,
    parse_destination,
    parse_int,
)
```

with:

```python
    BookloreError,
    booklore_list_libraries,
    booklore_login,
    parse_destination,
    parse_int,
)
from shelfmark.grimmory.destinations import (
    library_path_exists,
    parse_grimmory_destination_key,
)
```

Replace the head of `build_booklore_config` —

```python
def build_booklore_config(
    values: Mapping[str, Any],
    user_id: int | None = None,
) -> BookloreConfig:
    """Build and validate the effective Booklore configuration."""
```

— with a new helper followed by the new signature and docstring:

```python
def _verify_explicit_destination(
    base_url: str,
    username: str,
    password: str,
    destination_key: str,
) -> tuple[int, int]:
    """Resolve an admin-chosen ebook destination key, or raise.

    The key is checked against a fresh `GET /api/v1/libraries` made with the
    upload credentials, never the settings dropdown cache. There is no fallback
    to the default library: Grimmory cannot move books between libraries on a
    network disk, so a book filed in the wrong library stays there.
    """
    parsed = parse_grimmory_destination_key(destination_key)
    if parsed is None:
        msg = (
            f"{BOOKLORE_DISPLAY_NAME} destination {destination_key!r} is not a valid "
            "library choice; nothing was uploaded"
        )
        raise BookloreError(msg)

    library_id, path_id = parsed
    auth_config = BookloreConfig(
        base_url=base_url,
        username=username,
        password=password,
        library_id=library_id,
        path_id=path_id,
    )
    try:
        token = booklore_login(auth_config)
        libraries = booklore_list_libraries(auth_config, token)
    except BookloreError as exc:
        msg = (
            f"Could not verify {BOOKLORE_DISPLAY_NAME} destination {destination_key!r}: "
            f"{exc}; nothing was uploaded"
        )
        raise BookloreError(msg) from exc

    if not library_path_exists(libraries, library_id, path_id):
        msg = (
            f"{BOOKLORE_DISPLAY_NAME} destination {destination_key!r} no longer exists "
            f"(library {library_id}, path {path_id}); nothing was uploaded. "
            "Pick another library and download again."
        )
        raise BookloreError(msg)

    return library_id, path_id


def build_booklore_config(
    values: Mapping[str, Any],
    user_id: int | None = None,
    destination_key: str | None = None,
) -> BookloreConfig:
    """Build and validate the effective Booklore configuration.

    In library mode an explicit `destination_key` (an admin's pick) is verified
    and used; without one, the effective default for `user_id` applies. Bookdrop
    mode ignores both.
    """
```

In the body of `build_booklore_config`, replace:

```python
    library_id = 0
    path_id = 0
    if not upload_to_bookdrop:
        if user_id is not None:
```

with:

```python
    library_id = 0
    path_id = 0
    explicit_key = (destination_key or "").strip()
    if not upload_to_bookdrop and explicit_key:
        library_id, path_id = _verify_explicit_destination(
            base_url.rstrip("/"), username, password, explicit_key
        )
    elif not upload_to_bookdrop:
        if user_id is not None:
```

(the rest of that block — the per-user `core_config.config.get` reads and the two `parse_int` calls — is unchanged and now sits under the `elif`).

In `_post_process_booklore`, replace:

```python
        booklore_config = build_booklore_config(
            _get_booklore_settings(),
            user_id=task.user_id,
        )
```

with:

```python
        booklore_config = build_booklore_config(
            _get_booklore_settings(),
            user_id=task.user_id,
            destination_key=task.destination_key,
        )
```

(The existing `except BookloreError` right below already reports the error and returns before any staging or upload.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_booklore_multiuser.py tests/core/test_booklore_target.py tests/core/test_booklore_upload.py tests/core/test_download_processing.py tests/core/test_processing_integration.py -q`
Expected: PASS (17 in `test_booklore_multiuser.py`, 6 in `test_booklore_target.py`; the existing Booklore pipeline tests still pass because they carry no key).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/download/outputs/booklore.py
git add shelfmark/download/outputs/booklore.py tests/core/test_booklore_multiuser.py tests/core/test_booklore_target.py
git commit -m "feat(booklore): verify explicit ebook destination keys before upload

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: One `GET /api/download-destinations` endpoint for both formats

**Files:**
- Modify: `shelfmark/config/booklore_settings.py` (import; cache entry; `_get_booklore_select_options` cache update; new `get_booklore_destination_options`)
- Create: `shelfmark/core/destination_routes.py`
- Delete: `shelfmark/audiobookshelf/routes.py`, `tests/audiobookshelf/test_routes.py`
- Modify: `shelfmark/main.py` (route registration, ~line 535), `tests/core/test_auth_mode_fail_closed.py` (docstring reference)
- Test: `tests/core/test_destination_routes.py` (new), `tests/grimmory/test_destinations.py` (append)

**Interfaces:**
- Consumes: `build_destination_options` (Task 1); `list_destination_options()` from `shelfmark.audiobookshelf.destinations` (unchanged).
- Produces: `get_booklore_destination_options() -> list[dict[str, str]]` in `shelfmark.config.booklore_settings`; `register_destination_routes(app: Flask, *, resolve_auth_mode: Callable[[], str] | None = None) -> None` in `shelfmark.core.destination_routes`, which imports `get_booklore_destination_options` by name (tests patch `shelfmark.core.destination_routes.get_booklore_destination_options`). HTTP: `GET /api/download-destinations?content_type=ebook|audiobook` → `{"destinations": [...], "default_name": ""}`. `register_audiobookshelf_routes` no longer exists.

- [ ] **Step 1: Write the failing tests**

Delete the old route tests (they move to the new file below):

```bash
git rm tests/audiobookshelf/test_routes.py
```

Create `tests/core/test_destination_routes.py`:

```python
"""Tests for `GET /api/download-destinations`, the release modal and approve panel picker."""

from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.destination_routes import register_destination_routes
from tests.audiobookshelf.test_destinations import patch_config

AUDIOBOOK_DESTINATIONS = {
    "AUDIOBOOK_DESTINATIONS": [
        {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
        {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"},
    ]
}

EBOOK_OPTIONS = [
    {"key": "grimmory:3:3", "name": "Fiction"},
    {"key": "grimmory:5:8", "name": "Light Novels"},
]


def build_client(auth_mode: str = "builtin"):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    register_destination_routes(app, resolve_auth_mode=lambda: auth_mode)
    return app.test_client()


@pytest.fixture
def client():
    return build_client()


def as_admin(client, *, is_admin: bool = True):
    with client.session_transaction() as session:
        session["is_admin"] = is_admin
        session["db_user_id"] = 1
        session["user_id"] = "admin"
    return client


def ebook_options(options=EBOOK_OPTIONS):
    return patch(
        "shelfmark.core.destination_routes.get_booklore_destination_options",
        return_value=options,
    )


class TestAudiobookDestinations:
    """`content_type=audiobook` serves today's Audiobookshelf destination map."""

    def test_lists_configured_destinations(self, client):
        as_admin(client)

        with patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get("/api/download-destinations?content_type=audiobook")

        assert response.status_code == 200
        assert response.get_json() == {
            "destinations": [
                {"key": "lib-fiction", "name": "Fiction"},
                {"key": "lib-kids", "name": "Kids"},
            ],
            "default_name": "",
        }

    def test_returns_an_empty_list_when_unconfigured(self, client):
        as_admin(client)

        with patch_config({}):
            response = client.get("/api/download-destinations?content_type=audiobook")

        assert response.status_code == 200
        assert response.get_json()["destinations"] == []

    def test_never_exposes_local_paths(self, client):
        as_admin(client)

        with patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get("/api/download-destinations?content_type=audiobook")

        assert "/audiobooks/fiction" not in response.get_data(as_text=True)


class TestEbookDestinations:
    """`content_type=ebook` serves the Grimmory library paths."""

    def test_lists_the_grimmory_library_paths(self, client):
        as_admin(client)

        with ebook_options():
            response = client.get("/api/download-destinations?content_type=ebook")

        assert response.status_code == 200
        assert response.get_json() == {"destinations": EBOOK_OPTIONS, "default_name": ""}

    def test_an_unreachable_grimmory_is_an_empty_list(self, client):
        as_admin(client)

        with ebook_options([]):
            response = client.get("/api/download-destinations?content_type=ebook")

        assert response.status_code == 200
        assert response.get_json() == {"destinations": [], "default_name": ""}


class TestContentTypeParameter:
    def test_a_missing_content_type_is_a_400(self, client):
        as_admin(client)

        assert client.get("/api/download-destinations").status_code == 400

    def test_an_unknown_content_type_is_a_400(self, client):
        as_admin(client)

        response = client.get("/api/download-destinations?content_type=magazine")

        assert response.status_code == 400

    def test_content_type_is_case_sensitive_and_untrimmed(self, client):
        # Review Focus #5: only the exact values the frontend sends are accepted.
        as_admin(client)

        for value in ("Ebook", " ebook", "AUDIOBOOK"):
            response = client.get(f"/api/download-destinations?content_type={value}")
            assert response.status_code == 400, value

    def test_the_old_endpoint_is_gone(self, client):
        as_admin(client)

        assert client.get("/api/audiobook-destinations").status_code == 404


class TestAccess:
    """Destination routing is admin-only; auth mode "none" makes every caller an admin."""

    @pytest.mark.parametrize("content_type", ["ebook", "audiobook"])
    def test_requires_admin(self, client, content_type):
        as_admin(client, is_admin=False)

        with ebook_options(), patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get(f"/api/download-destinations?content_type={content_type}")

        assert response.status_code == 403

    def test_a_non_admin_gets_403_even_with_a_bad_content_type(self, client):
        as_admin(client, is_admin=False)

        assert client.get("/api/download-destinations?content_type=x").status_code == 403

    @pytest.mark.parametrize("content_type", ["ebook", "audiobook"])
    def test_serves_an_anonymous_caller_in_no_auth_mode(self, content_type):
        client = build_client(auth_mode="none")

        with ebook_options(), patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get(f"/api/download-destinations?content_type={content_type}")

        assert response.status_code == 200
        assert len(response.get_json()["destinations"]) == 2

    def test_still_requires_admin_when_auth_is_configured(self):
        client = build_client(auth_mode="builtin")

        with ebook_options():
            response = client.get("/api/download-destinations?content_type=ebook")

        assert response.status_code == 403
```

In `tests/grimmory/test_destinations.py`, add `import pytest` (with a blank line after it) between the module docstring and the `from shelfmark.grimmory.destinations import (` line, then append to the end of the file:

```python
class TestDisplayOptions:
    """`get_booklore_destination_options` reads through the settings-dropdown cache."""

    SETTINGS = {
        "BOOKS_OUTPUT_MODE": "booklore",
        "BOOKLORE_HOST": "http://grimmory:6060/",
        "BOOKLORE_USERNAME": "shelfmark",
        "BOOKLORE_PASSWORD": "secret",
    }

    @pytest.fixture
    def settings(self, monkeypatch):
        from shelfmark.config import booklore_settings

        values = dict(self.SETTINGS)
        monkeypatch.setattr(
            booklore_settings.config,
            "get",
            lambda key, default=None, **_kw: values.get(key, default),
        )
        monkeypatch.setattr(
            booklore_settings,
            "_BOOKLORE_OPTIONS_CACHE",
            {"key": None, "library_options": [], "path_options": [], "destination_options": []},
        )
        return booklore_settings, values

    def test_lists_options_from_one_library_read(self, settings, monkeypatch):
        booklore_settings, _ = settings
        calls = []
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(
            booklore_settings,
            "booklore_list_libraries",
            lambda cfg, token: calls.append(cfg.base_url) or LIBRARIES,
        )

        first = booklore_settings.get_booklore_destination_options()
        second = booklore_settings.get_booklore_destination_options()

        assert first == second == build_destination_options(LIBRARIES)
        assert calls == ["http://grimmory:6060"]

    def test_the_settings_dropdowns_still_get_their_options(self, settings, monkeypatch):
        booklore_settings, _ = settings
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(booklore_settings, "booklore_list_libraries", lambda cfg, t: LIBRARIES)

        booklore_settings.get_booklore_destination_options()

        assert [o["value"] for o in booklore_settings.get_booklore_library_options()] == ["5", "3"]

    def test_unreachable_grimmory_gives_no_options(self, settings, monkeypatch):
        booklore_settings, _ = settings

        def fail(cfg):
            raise booklore_settings.BookloreError("Could not connect to Grimmory")

        monkeypatch.setattr(booklore_settings, "booklore_login", fail)

        assert booklore_settings.get_booklore_destination_options() == []

    def test_other_output_modes_give_no_options(self, settings, monkeypatch):
        booklore_settings, values = settings
        values["BOOKS_OUTPUT_MODE"] = "folder"
        monkeypatch.setattr(
            booklore_settings,
            "booklore_login",
            lambda cfg: pytest.fail("must not contact Grimmory"),
        )

        assert booklore_settings.get_booklore_destination_options() == []

    def test_missing_credentials_give_no_options(self, settings, monkeypatch):
        booklore_settings, values = settings
        values["BOOKLORE_PASSWORD"] = ""
        monkeypatch.setattr(
            booklore_settings,
            "booklore_login",
            lambda cfg: pytest.fail("must not contact Grimmory"),
        )

        assert booklore_settings.get_booklore_destination_options() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_destination_routes.py tests/grimmory/test_destinations.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shelfmark.core.destination_routes'` (collection error), and the five `TestDisplayOptions` tests fail with `AttributeError: module 'shelfmark.config.booklore_settings' has no attribute 'get_booklore_destination_options'`.

- [ ] **Step 3: Implement**

**`shelfmark/config/booklore_settings.py`.** After the `from shelfmark.grimmory.client import (...)` block (which ends `    list_books,\n)`), add:

```python
from shelfmark.grimmory.destinations import build_destination_options
```

Replace the cache literal's tail:

```python
    "path_options": [],
}
```

with:

```python
    "path_options": [],
    # Ebook picker options (fork-only). Display only: an upload re-verifies its
    # target against a fresh library listing.
    "destination_options": [],
}
```

At the end of `_get_booklore_select_options`, replace:

```python
            "library_options": library_options,
            "path_options": path_options,
        }
    )

    return library_options, path_options
```

with:

```python
            "library_options": library_options,
            "path_options": path_options,
            "destination_options": build_destination_options(libraries),
        }
    )

    return library_options, path_options
```

Insert this new function immediately above `def check_booklore_connection(`:

```python
def get_booklore_destination_options() -> list[dict[str, str]]:
    """List every Grimmory library path as an ebook picker option.

    Served from the same credential-keyed cache as the settings dropdowns, so
    it never costs a Grimmory call per modal. An empty list (not booklore mode,
    missing credentials, Grimmory unreachable) hides the picker.
    """
    if config.get("BOOKS_OUTPUT_MODE", "folder") != "booklore":
        return []

    base_url = str(config.get("BOOKLORE_HOST", "") or "").strip().rstrip("/")
    username = str(config.get("BOOKLORE_USERNAME", "") or "").strip()
    password = str(config.get("BOOKLORE_PASSWORD", "") or "")

    if not base_url or not username or not password:
        return []

    if _BOOKLORE_OPTIONS_CACHE.get("key") != _get_booklore_cache_key(base_url, username, password):
        try:
            _get_booklore_select_options(base_url, username, password)
        except Exception:
            logger.exception("Failed to fetch Grimmory destinations")
            return []

    return list(_BOOKLORE_OPTIONS_CACHE.get("destination_options", []))


```

**`shelfmark/core/destination_routes.py`** (new):

```python
"""HTTP route for the download destination picker (fork-only).

One endpoint serves both formats: audiobooks route to an Audiobookshelf
library mapped in settings, ebooks to a Grimmory library path.
"""

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, session

from shelfmark.audiobookshelf.destinations import list_destination_options
from shelfmark.config.booklore_settings import get_booklore_destination_options

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue

_CONTENT_TYPES = frozenset({"ebook", "audiobook"})


def register_destination_routes(
    app: Flask,
    *,
    resolve_auth_mode: Callable[[], str] | None = None,
) -> None:
    """Register `GET /api/download-destinations` on the Flask app.

    `resolve_auth_mode` is resolved per request rather than captured, so a
    runtime auth-mode change takes effect without re-registering routes.
    """

    def no_auth_configured() -> bool:
        return resolve_auth_mode is not None and resolve_auth_mode() == "none"

    def require_admin() -> ResponseReturnValue | None:
        # Auth mode "none" means there are no accounts at all and every caller
        # is a full admin — that is what `/api/auth/check` reports, and the UI
        # renders admin controls on that basis. Gating on a session flag nobody
        # can hold would silently hide the picker on that setup.
        if no_auth_configured():
            return None
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403
        return None

    @app.route("/api/download-destinations", methods=["GET"])
    def api_download_destinations() -> ResponseReturnValue:
        """List where an admin can route a download of the given content type.

        Audiobook destinations come from stored config, so approving keeps
        working while Audiobookshelf is down. Ebook destinations come from the
        cached Grimmory library list; they are display only, and an upload
        verifies its target again. `default_name` is always "": the default a
        blank choice lands in depends on the target user's own settings.
        """
        forbidden = require_admin()
        if forbidden is not None:
            return forbidden

        content_type = request.args.get("content_type", "")
        if content_type not in _CONTENT_TYPES:
            return jsonify({"error": "content_type must be 'ebook' or 'audiobook'"}), 400

        destinations = (
            list_destination_options()
            if content_type == "audiobook"
            else get_booklore_destination_options()
        )
        return jsonify({"destinations": destinations, "default_name": ""})
```

**Delete the old route module:**

```bash
git rm shelfmark/audiobookshelf/routes.py
```

**`shelfmark/main.py`** (inside `if user_db is not None:`, ~line 535). Replace:

```python
        from shelfmark.audiobookshelf.routes import register_audiobookshelf_routes
        from shelfmark.core.activity_routes import register_activity_routes
```

with:

```python
        from shelfmark.core.activity_routes import register_activity_routes
        from shelfmark.core.destination_routes import register_destination_routes
```

and replace:

```python
        register_audiobookshelf_routes(app, resolve_auth_mode=_resolve_auth_mode_for_routes)
```

with:

```python
        register_destination_routes(app, resolve_auth_mode=_resolve_auth_mode_for_routes)
```

**`tests/core/test_auth_mode_fail_closed.py`** — the module docstring lists consumers of the open mode. Replace `` `audiobookshelf/routes.py:27` `` with `` `destination_routes.py:34` `` (line 34 of the new module is `return resolve_auth_mode is not None and resolve_auth_mode() == "none"`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_destination_routes.py tests/grimmory tests/audiobookshelf tests/core/test_auth_mode_fail_closed.py tests/core/test_download_api_guardrails.py -q`
Expected: PASS (the guardrail tests import `shelfmark.main`, which proves the new registration loads).

- [ ] **Step 5: Lint, typecheck, dead code, commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/core/destination_routes.py shelfmark/config/booklore_settings.py
uv run vulture shelfmark
git add shelfmark/config/booklore_settings.py shelfmark/core/destination_routes.py shelfmark/main.py \
  tests/core/test_destination_routes.py tests/core/test_auth_mode_fail_closed.py tests/grimmory/test_destinations.py
git commit -m "feat(api): one download-destinations endpoint for ebooks and audiobooks

Replaces /api/audiobook-destinations; the frontend switches in a later commit.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(The two `git rm` calls above already staged the deletions.)

---
### Task 4: Book identity on `DownloadTask`, through queueing, retry and restart restore

**Files:**
- Create: `shelfmark/core/book_identity.py`
- Modify: `shelfmark/core/models.py` (`DownloadTask`, after `destination_key`), `shelfmark/download/orchestrator.py` (import; `queue_release`; `serialize_task_for_retry`; `_restore_task_from_retry_payload`), `shelfmark/library/matching.py` (`normalize_isbn`: `978`/`979` prefix)
- Test: `tests/core/test_book_identity.py` (new), `tests/download/test_orchestrator_identity.py` (new), `tests/library/test_matching.py` (one new test)

**Interfaces:**
- Consumes: `normalize_isbn(value: object) -> str` from `shelfmark.library.matching` (returns canonical ISBN-13 or `""`; shared with library matching and the Grimmory index provider — this task tightens it to require a `978`/`979` prefix on 13-digit input); `normalize_optional_text` from `shelfmark.core.request_helpers`.
- Produces: `@dataclass(frozen=True) class BookIdentity` with `provider`, `provider_id`, `isbn_13`, `asin` (all `str | None = None`) and `as_dict() -> dict[str, str | None]`; `normalize_book_identity(data: Mapping[str, Any]) -> BookIdentity`. `DownloadTask` gains `provider`, `provider_id`, `isbn_13`, `asin: str | None = None`. `queue_release` fills them from top-level release fields; the retry payload carries the four keys and restore re-normalizes them.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_book_identity.py`:

```python
"""Tests for the book identity carried onto a download task (fork-only)."""

from shelfmark.core.book_identity import BookIdentity, normalize_book_identity

ISBN_13 = "9780316005142"  # check digit valid
ISBN_10 = "0316005142"  # the same book as ISBN-10


class TestNormalizeBookIdentity:
    def test_keeps_a_complete_identity(self):
        identity = normalize_book_identity(
            {
                "provider": "hardcover",
                "provider_id": "886465",
                "isbn_13": ISBN_13,
                "asin": "B0BSHZ1234",
            }
        )

        assert identity == BookIdentity(
            provider="hardcover", provider_id="886465", isbn_13=ISBN_13, asin="B0BSHZ1234"
        )

    def test_trims_strings_and_blanks_become_none(self):
        identity = normalize_book_identity(
            {"provider": "  hardcover ", "provider_id": " 886465 ", "asin": "   "}
        )

        assert identity == BookIdentity(provider="hardcover", provider_id="886465")

    def test_a_numeric_provider_id_becomes_text(self):
        identity = normalize_book_identity({"provider": "hardcover", "provider_id": 886465})

        assert identity.provider_id == "886465"

    def test_provider_without_provider_id_drops_both(self):
        assert normalize_book_identity({"provider": "hardcover"}) == BookIdentity()
        assert normalize_book_identity({"provider_id": "886465"}) == BookIdentity()
        assert normalize_book_identity({"provider": "hardcover", "provider_id": " "}) == (
            BookIdentity()
        )

    def test_the_pair_rule_keeps_the_isbn_and_asin(self):
        identity = normalize_book_identity({"provider": "hardcover", "isbn_13": ISBN_13})

        assert identity == BookIdentity(isbn_13=ISBN_13)

    def test_manual_books_carry_no_provider_identity(self):
        identity = normalize_book_identity({"provider": "Manual", "provider_id": "manual-1"})

        assert identity == BookIdentity()

    def test_an_isbn_10_is_canonicalized_to_isbn_13(self):
        assert normalize_book_identity({"isbn_13": ISBN_10}).isbn_13 == ISBN_13
        assert normalize_book_identity({"isbn_10": ISBN_10}).isbn_13 == ISBN_13

    def test_hyphens_and_spaces_in_the_isbn_are_ignored(self):
        assert normalize_book_identity({"isbn_13": " 978-0-316-00514-2 "}).isbn_13 == ISBN_13

    def test_an_invalid_isbn_is_dropped(self):
        # Review Focus #2: a bad check digit, a placeholder, junk and non-strings.
        for value in (
            "9780316005143",
            "1234567890128",  # checksum-valid EAN, but not a 978/979 ISBN
            "0000000000",
            "N/A",
            9780316005142,
            True,
            "",
        ):
            assert normalize_book_identity({"isbn_13": value}).isbn_13 is None, repr(value)

    def test_a_bad_isbn_13_falls_back_to_a_valid_isbn_10(self):
        identity = normalize_book_identity({"isbn_13": "N/A", "isbn_10": ISBN_10})

        assert identity.isbn_13 == ISBN_13

    def test_non_string_values_are_ignored(self):
        identity = normalize_book_identity(
            {"provider": ["hardcover"], "provider_id": True, "asin": 12}
        )

        assert identity == BookIdentity()

    def test_as_dict_lists_the_four_fields(self):
        assert BookIdentity(provider="hardcover", provider_id="1").as_dict() == {
            "provider": "hardcover",
            "provider_id": "1",
            "isbn_13": None,
            "asin": None,
        }
```

Create `tests/download/test_orchestrator_identity.py`:

```python
"""Book identity survives queueing, retry and restart restore (fork-only)."""

from unittest.mock import MagicMock

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download import orchestrator

ISBN_13 = "9780316005142"
ISBN_10 = "0316005142"

IDENTITY = {
    "provider": "hardcover",
    "provider_id": "886465",
    "isbn_13": ISBN_13,
    "asin": "B0BSHZ1234",
}


@pytest.fixture
def queued(monkeypatch):
    """Capture the task `queue_release` builds."""
    captured: dict[str, DownloadTask] = {}

    def fake_add(task: DownloadTask) -> bool:
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", lambda _key, default=None, **_kw: default)
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    def queue(release_data: dict) -> DownloadTask:
        ok, error = orchestrator.queue_release(
            {"source": "direct_download", "source_id": "abc", "title": "Overlord", **release_data}
        )
        assert ok, error
        return captured["task"]

    return queue


def _identity(task: DownloadTask) -> dict:
    return {field: getattr(task, field) for field in IDENTITY}


def test_a_task_has_no_identity_by_default():
    assert _identity(DownloadTask(task_id="t", source="prowlarr", title="T")) == dict.fromkeys(
        IDENTITY
    )


def test_queue_release_carries_the_identity(queued):
    assert _identity(queued(IDENTITY)) == IDENTITY


def test_queue_release_normalizes_the_identity(queued):
    task = queued({"provider": " hardcover ", "provider_id": " 886465 ", "isbn_13": ISBN_10})

    assert _identity(task) == {**dict.fromkeys(IDENTITY), **IDENTITY, "asin": None}


def test_queue_release_drops_a_half_pair(queued):
    task = queued({"provider": "hardcover", "isbn_13": ISBN_13})

    assert task.provider is None
    assert task.provider_id is None
    assert task.isbn_13 == ISBN_13


def test_identity_in_extra_is_not_read(queued):
    """Release-source `extra` blobs belong to the indexer, not to the book."""
    task = queued({"extra": {"provider": "hardcover", "provider_id": "1", "asin": "B0X"}})

    assert _identity(task) == dict.fromkeys(IDENTITY)


def test_retry_payload_round_trips_the_identity():
    task = DownloadTask(task_id="t", source="prowlarr", title="T", **IDENTITY)

    payload = orchestrator.serialize_task_for_retry(task)
    restored = orchestrator._restore_task_from_retry_payload(payload)

    assert {field: payload[field] for field in IDENTITY} == IDENTITY
    assert restored is not None
    assert _identity(restored) == IDENTITY


def test_a_legacy_retry_payload_restores_without_identity():
    payload = orchestrator.serialize_task_for_retry(
        DownloadTask(task_id="t", source="prowlarr", title="T", **IDENTITY)
    )
    for field in IDENTITY:
        del payload[field]

    restored = orchestrator._restore_task_from_retry_payload(payload)

    assert restored is not None
    assert _identity(restored) == dict.fromkeys(IDENTITY)


def test_restore_applies_the_pair_rule():
    payload = orchestrator.serialize_task_for_retry(
        DownloadTask(task_id="t", source="prowlarr", title="T")
    )
    payload["provider"] = "hardcover"

    restored = orchestrator._restore_task_from_retry_payload(payload)

    assert restored is not None
    assert restored.provider is None


def test_restart_restore_requeues_with_the_identity(monkeypatch):
    """`retry_persisted_download` rebuilds a task lost to a restart."""
    queue = MagicMock()
    queue.add.return_value = True
    monkeypatch.setattr(orchestrator, "book_queue", queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)
    payload = orchestrator.serialize_task_for_retry(
        DownloadTask(task_id="t", source="prowlarr", title="T", **IDENTITY)
    )

    ok, error = orchestrator.retry_persisted_download(payload, final_status="cancelled")

    assert ok, error
    assert _identity(queue.add.call_args.args[0]) == IDENTITY
```

In `tests/library/test_matching.py`, class `TestNormalizeIsbn`, add after `test_rejects_zero_filled_placeholders`:

```python
    def test_rejects_a_checksum_valid_ean_that_is_not_an_isbn(self):
        # Only the 978/979 "Bookland" prefixes are ISBNs. Any other 13-digit EAN
        # can pass the same check digit, and an exact match on it is a false yes.
        assert normalize_isbn("1234567890128") == ""
        assert isbn_match_key("1234567890128") == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_book_identity.py tests/download/test_orchestrator_identity.py tests/library/test_matching.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shelfmark.core.book_identity'` for the first file; `AttributeError: 'DownloadTask' object has no attribute 'provider'` (6) / `TypeError: DownloadTask.__init__() got an unexpected keyword argument 'provider'` (3) across the second; and `AssertionError: assert '1234567890128' == ''` in `test_rejects_a_checksum_valid_ean_that_is_not_an_isbn`. Every other library matching test still passes.

- [ ] **Step 3: Implement**

**`shelfmark/core/book_identity.py`** (new):

```python
"""Book identity carried from the metadata provider onto a download task (fork-only).

A post-upload hook tags the book it was given, and the strongest evidence it
can start from is what the admin was looking at when they picked the release:
the metadata provider's id, the ISBN and the ASIN. `provider` and
`provider_id` only mean something together, so they travel as a pair: one
without the other drops both rather than mixing ids from different books.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.library.matching import normalize_isbn

if TYPE_CHECKING:
    from collections.abc import Mapping

# Manual books have no metadata record: their "provider id" names nothing.
_PROVIDERS_WITHOUT_IDENTITY = frozenset({"manual"})


@dataclass(frozen=True)
class BookIdentity:
    """The four identity fields a download task carries (all optional)."""

    provider: str | None = None
    provider_id: str | None = None
    isbn_13: str | None = None
    asin: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """Return the fields as a plain dict, for payloads and retry rows."""
        return asdict(self)


def _identity_text(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return normalize_optional_text(value)


def normalize_book_identity(data: Mapping[str, Any]) -> BookIdentity:
    """Read the identity fields from a release payload, request or retry row.

    Strings are trimmed (blank becomes None); the ISBN is canonicalized to
    ISBN-13 from `isbn_13`, else `isbn_10`, and dropped when invalid.
    """
    provider = _identity_text(data.get("provider"))
    provider_id = _identity_text(data.get("provider_id"))
    if (
        provider is None
        or provider_id is None
        or provider.casefold() in _PROVIDERS_WITHOUT_IDENTITY
    ):
        provider = provider_id = None

    isbn_13 = normalize_isbn(data.get("isbn_13")) or normalize_isbn(data.get("isbn_10"))

    return BookIdentity(
        provider=provider,
        provider_id=provider_id,
        isbn_13=isbn_13 or None,
        asin=normalize_optional_text(data.get("asin")),
    )
```

**`shelfmark/library/matching.py`** — replace:

```python
ISBN_KEY_PREFIX = "isbn:"
```

with:

```python
ISBN_KEY_PREFIX = "isbn:"
# Only the "Bookland" EAN prefixes are ISBNs; any other 13-digit EAN can still
# pass the ISBN-13 check digit.
_ISBN13_PREFIXES = ("978", "979")
```

and in `normalize_isbn` replace:

```python
    if _ISBN13_SHAPE.match(candidate):
        return candidate if _isbn13_is_valid(candidate) else ""
```

with:

```python
    if _ISBN13_SHAPE.match(candidate):
        if not candidate.startswith(_ISBN13_PREFIXES):
            return ""
        return candidate if _isbn13_is_valid(candidate) else ""
```

(The ISBN-10 branch always produces a `978` ISBN-13, so it needs no change.)

**`shelfmark/core/models.py`** — in `DownloadTask`, replace:

```python
    destination_key: str | None = None
```

with:

```python
    destination_key: str | None = None

    # Book identity from the metadata provider (fork-only), for post-upload hooks
    # that tag the book. Normalized by `normalize_book_identity` at queue time:
    # provider/provider_id travel as a pair, the ISBN is a canonical ISBN-13.
    provider: str | None = None
    provider_id: str | None = None
    isbn_13: str | None = None
    asin: str | None = None
```

**`shelfmark/download/orchestrator.py`** — five edits.

1. Imports: replace `from shelfmark.core.config import config` with:

```python
from shelfmark.core.book_identity import normalize_book_identity
from shelfmark.core.config import config
```

2. In `queue_release`, replace:

```python
        destination_key = normalize_optional_text(
            release_data.get("destination_key") or extra.get("destination_key")
        )
```

with:

```python
        destination_key = normalize_optional_text(
            release_data.get("destination_key") or extra.get("destination_key")
        )

        # Top-level only: a release source's `extra` describes the release as the
        # indexer saw it, and must not be mistaken for the book's identity.
        identity = normalize_book_identity(release_data)
```

3. In the `DownloadTask(...)` call inside `queue_release`, replace:

```python
            destination_key=destination_key,
            priority=priority,
```

with:

```python
            destination_key=destination_key,
            provider=identity.provider,
            provider_id=identity.provider_id,
            isbn_13=identity.isbn_13,
            asin=identity.asin,
            priority=priority,
```

4. In `serialize_task_for_retry`, replace:

```python
        "destination_key": getattr(task, "destination_key", None),
```

with:

```python
        "destination_key": getattr(task, "destination_key", None),
        "provider": getattr(task, "provider", None),
        "provider_id": getattr(task, "provider_id", None),
        "isbn_13": getattr(task, "isbn_13", None),
        "asin": getattr(task, "asin", None),
```

5. In `_restore_task_from_retry_payload`, replace:

```python
    output_args = payload.get("output_args")
    retry_source_context = payload.get("retry_source_context")
```

with:

```python
    output_args = payload.get("output_args")
    retry_source_context = payload.get("retry_source_context")
    identity = normalize_book_identity(payload)
```

and in its `DownloadTask(...)` call replace:

```python
        destination_key=normalize_optional_text(payload.get("destination_key")),
```

with:

```python
        destination_key=normalize_optional_text(payload.get("destination_key")),
        provider=identity.provider,
        provider_id=identity.provider_id,
        isbn_13=identity.isbn_13,
        asin=identity.asin,
```

(`retry_persisted_download` — the restart path — goes through `_restore_task_from_retry_payload`, so it needs no edit.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_book_identity.py tests/download tests/audiobookshelf tests/library -q`
Expected: PASS (12 + 9 + 1 new tests; existing orchestrator, routing and library tests unchanged).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/core shelfmark/library shelfmark/download/orchestrator.py
git add shelfmark/core/book_identity.py shelfmark/core/models.py shelfmark/download/orchestrator.py \
  shelfmark/library/matching.py tests/core/test_book_identity.py tests/download/test_orchestrator_identity.py \
  tests/library/test_matching.py
git commit -m "feat(download): carry book identity on DownloadTask through queue and retry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Approved requests fill identity from `book_data`; pin the HTTP producers and key rules

**Files:**
- Modify: `shelfmark/core/book_identity.py` (append `fill_identity_from_book_data`), `shelfmark/core/requests_service.py` (import; `fulfil_request`)
- Test: `tests/core/test_book_identity.py` (append), `tests/core/test_request_identity.py` (new), `tests/core/test_download_api_guardrails.py` (append), `tests/audiobookshelf/test_routing.py` (append)

**Interfaces:**
- Consumes: `BookIdentity`, `normalize_book_identity` (Task 4).
- Produces: `fill_identity_from_book_data(release_data: Mapping[str, Any], book_data: object) -> dict[str, Any]` — returns a new dict; never mutates its input. Rule: a release with a complete pair keeps it and takes ISBN/ASIN only from a `book_data` naming the same pair; a release naming a *different* provider without an id drops its half pair and imports nothing; otherwise a release without a complete pair takes `book_data`'s pair and missing hints. `fulfil_request` queues `fill_identity_from_book_data(selected_release_data, request_row.get("book_data"))` plus `_request_id` and `destination_key`.

- [ ] **Step 1: Write the failing tests**

In `tests/core/test_book_identity.py`, replace the import line

```python
from shelfmark.core.book_identity import BookIdentity, normalize_book_identity
```

with

```python
from shelfmark.core.book_identity import (
    BookIdentity,
    fill_identity_from_book_data,
    normalize_book_identity,
)
```

add a second constant directly after the `ISBN_10 = ...` line:

```python
OTHER_ISBN_13 = "9780593135204"  # a different, valid book
```

and append to the end of the file:

```python
class TestFillIdentityFromBookData:
    """An approved request fills identity the release lacks from its stored book data."""

    BOOK_DATA = {
        "title": "Overlord",
        "author": "Kugane Maruyama",
        "provider": "hardcover",
        "provider_id": "886465",
        "isbn_13": ISBN_13,
        "asin": "B0BSHZ1234",
    }

    def test_fills_every_field_a_bare_release_lacks(self):
        filled = fill_identity_from_book_data({"source": "prowlarr"}, self.BOOK_DATA)

        assert filled == {
            "source": "prowlarr",
            "provider": "hardcover",
            "provider_id": "886465",
            "isbn_13": ISBN_13,
            "asin": "B0BSHZ1234",
        }

    def test_the_release_keeps_its_own_values(self):
        release = {"provider": "hardcover", "provider_id": "886465", "isbn_13": ISBN_10}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled["isbn_13"] == ISBN_10
        assert filled["asin"] == "B0BSHZ1234"

    def test_a_release_for_a_different_book_takes_nothing(self):
        release = {"provider": "openlibrary", "provider_id": "OL1W"}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled == release

    def test_provider_names_compare_case_insensitively(self):
        release = {"provider": "Hardcover", "provider_id": "886465"}

        assert fill_identity_from_book_data(release, self.BOOK_DATA)["asin"] == "B0BSHZ1234"

    def test_a_same_provider_half_pair_is_replaced_as_a_pair(self):
        release = {"provider": "Hardcover"}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert (filled["provider"], filled["provider_id"]) == ("hardcover", "886465")
        assert filled["asin"] == "B0BSHZ1234"

    def test_a_half_pair_naming_another_provider_imports_nothing(self):
        """The release says Open Library but lost its id; the request is Hardcover.

        Adopting the request's pair would pin this release's ISBN to another
        book's Hardcover id, so the half pair is dropped and nothing is imported.
        """
        release = {"provider": "openlibrary", "isbn_13": OTHER_ISBN_13}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled["provider"] is None
        assert filled["provider_id"] is None
        assert filled["isbn_13"] == OTHER_ISBN_13
        assert "asin" not in filled

    def test_an_isbn_10_in_book_data_is_used(self):
        book_data = {**self.BOOK_DATA, "isbn_13": None, "isbn_10": ISBN_10}

        assert fill_identity_from_book_data({}, book_data)["isbn_13"] == ISBN_13

    def test_book_data_without_identity_changes_nothing(self):
        release = {"source": "prowlarr", "provider": "hardcover"}

        assert fill_identity_from_book_data(release, {"title": "x"}) == release

    def test_non_dict_book_data_changes_nothing(self):
        assert fill_identity_from_book_data({"source": "x"}, None) == {"source": "x"}

    def test_the_input_is_not_mutated(self):
        release = {"source": "prowlarr"}

        fill_identity_from_book_data(release, self.BOOK_DATA)

        assert release == {"source": "prowlarr"}
```

Create `tests/core/test_request_identity.py`:

```python
"""Book identity on approved requests (fork-only): fulfil fills it from book_data."""

import os
import tempfile
from typing import Any

import pytest

from shelfmark.core.requests_service import fulfil_request
from shelfmark.core.user_db import UserDB

ISBN_13 = "9780316005142"

BOOK_DATA = {
    "title": "Overlord",
    "author": "Kugane Maruyama",
    "content_type": "ebook",
    "provider": "hardcover",
    "provider_id": "886465",
    "isbn_13": ISBN_13,
    "asin": "B0BSHZ1234",
}


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "shelfmark.db"))
        db.initialize()
        yield db


def approve(user_db: UserDB, *, release_data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    requester = user_db.create_user("ada")
    admin = user_db.create_user("root", role="admin")
    created = user_db.create_request(
        user_id=requester["id"],
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=BOOK_DATA,
        release_data=release_data,
    )
    queued: list[dict[str, Any]] = []

    def fake_queue_release(data, priority=0, **_kwargs):
        queued.append(data)
        return True, None

    fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=fake_queue_release,
        **kwargs,
    )
    return queued[0]


def test_an_old_release_without_identity_is_filled_from_book_data(user_db):
    queued = approve(user_db, release_data={"source": "prowlarr", "source_id": "r1", "title": "O"})

    assert {k: queued.get(k) for k in ("provider", "provider_id", "isbn_13", "asin")} == {
        "provider": "hardcover",
        "provider_id": "886465",
        "isbn_13": ISBN_13,
        "asin": "B0BSHZ1234",
    }


def test_a_browsed_release_for_another_provider_keeps_its_own_identity(user_db):
    release = {
        "source": "prowlarr",
        "source_id": "r2",
        "title": "O",
        "provider": "openlibrary",
        "provider_id": "OL1W",
    }

    queued = approve(user_db, release_data=release)

    assert (queued["provider"], queued["provider_id"]) == ("openlibrary", "OL1W")
    assert "isbn_13" not in queued
    assert "asin" not in queued


def test_the_destination_key_still_travels_alongside(user_db):
    queued = approve(
        user_db,
        release_data={"source": "prowlarr", "source_id": "r3", "title": "O"},
        destination_key="grimmory:5:8",
    )

    assert queued["destination_key"] == "grimmory:5:8"
    assert queued["provider_id"] == "886465"
```

Append to the end of `tests/core/test_download_api_guardrails.py` (it already imports `uuid`, `patch`, and defines `_create_user` / `_set_authenticated_session`):

```python
class TestEbookKeyAndIdentityPassThrough:
    """Fork-only: the ebook library key and book identity reach the queue unchanged."""

    IDENTITY = {
        "provider": "hardcover",
        "provider_id": "886465",
        "isbn_13": "9780316005142",
        "asin": "B0BSHZ1234",
    }

    def _queue(self, main_module, client, payload, *, auth_mode="builtin"):
        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured.update({"release_data": release_data, "user_id": user_id})
            return True, None

        with patch.object(main_module, "get_auth_mode", return_value=auth_mode):
            with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 200
        return captured

    def _payload(self, **extra):
        return {
            "source": "prowlarr",
            "source_id": f"release-{uuid.uuid4().hex[:8]}",
            "title": "Overlord",
            "content_type": "ebook",
            **self.IDENTITY,
            **extra,
        }

    def test_an_admin_direct_download_keeps_key_and_identity(self, main_module, client):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client, user_id=admin_user["username"], db_user_id=admin_user["id"], is_admin=True
        )

        captured = self._queue(main_module, client, self._payload(destination_key="grimmory:5:8"))

        release_data = captured["release_data"]
        assert release_data["destination_key"] == "grimmory:5:8"
        assert {k: release_data[k] for k in self.IDENTITY} == self.IDENTITY

    def test_a_non_admin_loses_the_ebook_key_but_keeps_identity(self, main_module, client):
        _set_authenticated_session(client, user_id="ada", db_user_id=23, is_admin=False)

        captured = self._queue(main_module, client, self._payload(destination_key="grimmory:5:8"))

        release_data = captured["release_data"]
        assert "destination_key" not in release_data
        assert {k: release_data[k] for k in self.IDENTITY} == self.IDENTITY

    def test_no_auth_mode_keeps_the_ebook_key(self, main_module, client):
        captured = self._queue(
            main_module, client, self._payload(destination_key="grimmory:5:8"), auth_mode="none"
        )

        assert captured["release_data"]["destination_key"] == "grimmory:5:8"

    def test_an_on_behalf_download_keeps_key_and_identity(self, main_module, client):
        target_user = _create_user(main_module, prefix="target")
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client, user_id=admin_user["username"], db_user_id=admin_user["id"], is_admin=True
        )

        captured = self._queue(
            main_module,
            client,
            self._payload(destination_key="grimmory:5:8", on_behalf_of_user_id=target_user["id"]),
        )

        release_data = captured["release_data"]
        assert captured["user_id"] == target_user["id"]
        assert release_data["destination_key"] == "grimmory:5:8"
        assert {k: release_data[k] for k in self.IDENTITY} == self.IDENTITY
```

Append to the end of `tests/audiobookshelf/test_routing.py`:

```python
class TestCrossFormatKeys:
    """An ebook key never routes an audiobook (fork-only)."""

    def test_a_grimmory_key_on_an_audiobook_falls_back_to_the_default(self):
        with patch_config(DESTINATION_CONFIG):
            resolved = get_final_destination(audiobook_task(destination_key="grimmory:5:8"))

        assert resolved == Path("/audiobooks")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_book_identity.py tests/core/test_request_identity.py tests/core/test_download_api_guardrails.py tests/audiobookshelf/test_routing.py -q`
Expected: FAIL — `ImportError: cannot import name 'fill_identity_from_book_data'` (collection error in `test_book_identity.py`), (including the new `test_a_half_pair_naming_another_provider_imports_nothing`, which pins the review finding: release `{"provider": "openlibrary", "isbn_13": A}` against a Hardcover request for book B must not become B's pair with A's ISBN), and in `test_request_identity.py` two failures (`test_an_old_release_without_identity_is_filled_from_book_data`, `test_the_destination_key_still_travels_alongside`) with a `KeyError`/assertion on the missing identity. The guardrail, cross-format and "another provider" tests **pass already**: they pin behaviour that must not change (the route forwards the payload untouched; non-admin keys are stripped; auth mode `none` keeps the key; an unmapped key falls back for Audiobookshelf).

- [ ] **Step 3: Implement**

Append to `shelfmark/core/book_identity.py`:

```python
def fill_identity_from_book_data(
    release_data: Mapping[str, Any],
    book_data: object,
) -> dict[str, Any]:
    """Fill identity an approved release lacks from the request's stored book data.

    A release carrying its own complete provider pair keeps it, and takes the
    ISBN and ASIN from the book data only when both name the same book (same
    provider and id): an admin may have browsed to a release described by a
    different provider. A release that names a provider but lost its id, while
    the book data names a different provider, drops its half pair and imports
    nothing: adopting the request's pair would pin the release's ISBN to
    another book.
    Otherwise a release without a complete pair takes the book data's pair
    whole. Returns a new dict; the input is not modified.
    """
    filled = dict(release_data)
    if not isinstance(book_data, dict):
        return filled

    release = normalize_book_identity(release_data)
    book = normalize_book_identity(book_data)

    if release.provider is None:
        named_provider = _identity_text(release_data.get("provider"))
        if (
            named_provider is not None
            and book.provider is not None
            and named_provider.casefold() != book.provider.casefold()
        ):
            filled["provider"] = None
            filled["provider_id"] = None
            return filled
        if book.provider is not None:
            filled["provider"] = book.provider
            filled["provider_id"] = book.provider_id
    elif (
        book.provider is None
        or release.provider.casefold() != book.provider.casefold()
        or release.provider_id != book.provider_id
    ):
        return filled

    if release.isbn_13 is None and book.isbn_13 is not None:
        filled["isbn_13"] = book.isbn_13
    if release.asin is None and book.asin is not None:
        filled["asin"] = book.asin
    return filled
```

In `shelfmark/core/requests_service.py`, add above `from shelfmark.core.models import QueueStatus`:

```python
from shelfmark.core.book_identity import fill_identity_from_book_data
```

and in `fulfil_request` replace:

```python
    queued_release_data = dict(selected_release_data)
    queued_release_data["_request_id"] = request_id
```

with:

```python
    # Identity the release lacks comes from the request's book data, so a
    # post-upload hook can tag what the requester actually asked for.
    queued_release_data = fill_identity_from_book_data(
        selected_release_data, request_row.get("book_data")
    )
    queued_release_data["_request_id"] = request_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core tests/audiobookshelf tests/download -q`
Expected: PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/core
git add shelfmark/core/book_identity.py shelfmark/core/requests_service.py tests/core/test_book_identity.py \
  tests/core/test_request_identity.py tests/core/test_download_api_guardrails.py tests/audiobookshelf/test_routing.py
git commit -m "feat(requests): fill book identity from book_data on fulfil

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: Only an admin's key reaches the queue from request workflows

**Files:**
- Modify: `shelfmark/core/request_routes.py` (import; `_prepare_request_create_arguments`), `shelfmark/core/requests_service.py` (new `_without_destination_keys`; `fulfil_request`)
- Test: `tests/core/test_request_destination_authorization.py` (new)

**Interfaces:**
- Consumes: `authorize_destination_key(payload, *, is_admin)` from `shelfmark.audiobookshelf.destinations` (unchanged); `fill_identity_from_book_data` (Task 5).
- Produces: `_prepare_request_create_arguments` returns `release_data` with `destination_key` stripped (top level and `extra`) unless the session is an admin — this feeds both `create_request(s)` (stored requests) and `_queue_prepared_download_submission` (download policy). `fulfil_request` queues release data with every stored key removed, then sets only the explicit `destination_key` argument.

Why: `authorize_destination_key` guarded `/api/releases/download` only. A requester could post `release_data.destination_key` (or `release_data.extra.destination_key`) to `/api/requests` or `/api/requests/batch`: a download-policy submission queued it at once, and a pending request stored it, where an approval with a blank library set the top-level key to `None` and `queue_release` (`release_data.get("destination_key") or extra.get("destination_key")`) revived the nested copy. This predates the ebook picker (audiobook keys were exposed the same way) and is fixed here because ebook keys write into a library that cannot be corrected afterwards.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_request_destination_authorization.py`:

```python
"""A destination key reaches the queue only from an admin (fork-only).

`authorize_destination_key` guarded `/api/releases/download` alone. A requester
could put a key in a request's `release_data` (top level or `extra`): a
download-policy submission queued it straight away, and a pending request
stored it, where a blank approval let `queue_release` revive the nested copy.
"""

from __future__ import annotations

import importlib
import os
import tempfile
import uuid
from typing import Any
from unittest.mock import patch

import pytest

from shelfmark.core.requests_service import fulfil_request
from shelfmark.core.user_db import UserDB


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _login(client, user: dict, *, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = is_admin


def _create_user(main_module, *, role: str = "user") -> dict:
    return main_module.user_db.create_user(username=f"u-{uuid.uuid4().hex[:8]}", role=role)


def _policy(*, ebook: str, audiobook: str) -> dict:
    return {
        "REQUESTS_ENABLED": True,
        "REQUEST_POLICY_DEFAULT_EBOOK": ebook,
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": audiobook,
        "MAX_PENDING_REQUESTS_PER_USER": 20,
        "REQUESTS_ALLOW_NOTES": True,
        "REQUEST_POLICY_RULES": [],
    }


def _payload(content_type: str = "ebook") -> dict:
    tag = uuid.uuid4().hex[:8]
    return {
        "book_data": {
            "title": f"Planted {tag}",
            "author": "Shelfmark",
            "content_type": content_type,
            "provider": "openlibrary",
            "provider_id": f"planted-{tag}",
        },
        "context": {"source": "prowlarr", "content_type": content_type, "request_level": "release"},
        "release_data": {
            "source": "prowlarr",
            "source_id": f"planted-release-{tag}",
            "title": f"Planted {tag}.epub",
            "destination_key": "grimmory:5:8",
            "extra": {"destination_key": "grimmory:5:8", "indexer": "x"},
        },
    }


def _post(main_module, client, path, body, *, policy):
    queued: list[dict[str, Any]] = []

    def fake_queue_release(release_data, priority, user_id=None, username=None):
        queued.append(release_data)
        return True, None

    with (
        patch.object(main_module, "get_auth_mode", return_value="builtin"),
        patch.object(main_module, "load_users_request_policy_settings", return_value=policy),
        patch(
            "shelfmark.core.request_routes.load_users_request_policy_settings",
            return_value=policy,
        ),
        patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release),
        patch("shelfmark.core.request_routes.notify_admin"),
        patch("shelfmark.core.request_routes.notify_user"),
    ):
        resp = client.post(path, json=body)
    return resp, queued


def _has_key(release_data: dict) -> bool:
    extra = release_data.get("extra") or {}
    return "destination_key" in release_data or "destination_key" in extra


class TestRequestSubmission:
    def test_a_stored_request_keeps_no_key(self, main_module, client):
        user = _create_user(main_module)
        _login(client, user, is_admin=False)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests",
            _payload(),
            policy=_policy(ebook="request_release", audiobook="request_release"),
        )

        assert resp.status_code == 201, resp.json
        assert queued == []
        stored = main_module.user_db.get_request(resp.json["id"])
        assert not _has_key(stored["release_data"])
        assert stored["release_data"]["extra"] == {"indexer": "x"}

    def test_a_download_policy_submission_queues_no_key(self, main_module, client):
        user = _create_user(main_module)
        _login(client, user, is_admin=False)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests",
            _payload(),
            policy=_policy(ebook="download", audiobook="download"),
        )

        assert resp.status_code == 200, resp.json
        assert len(queued) == 1
        assert not _has_key(queued[0])

    def test_a_batch_strips_both_policy_paths(self, main_module, client):
        user = _create_user(main_module)
        _login(client, user, is_admin=False)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests/batch",
            {"requests": [_payload("ebook"), _payload("audiobook")]},
            policy=_policy(ebook="download", audiobook="request_release"),
        )

        assert resp.status_code == 201, resp.json
        assert len(queued) == 1
        assert not _has_key(queued[0])
        stored = [row for row in resp.json if row.get("kind") != "download"]
        assert len(stored) == 1
        assert not _has_key(main_module.user_db.get_request(stored[0]["id"])["release_data"])

    def test_an_admin_download_policy_submission_keeps_the_key(self, main_module, client):
        admin = _create_user(main_module, role="admin")
        _login(client, admin, is_admin=True)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests",
            _payload(),
            policy=_policy(ebook="download", audiobook="download"),
        )

        assert resp.status_code == 200, resp.json
        assert queued[0]["destination_key"] == "grimmory:5:8"


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "shelfmark.db"))
        db.initialize()
        yield db


class TestFulfilment:
    """Only the approving admin's explicit choice travels with an approval."""

    def approve(self, user_db: UserDB, **kwargs: Any) -> dict[str, Any]:
        requester = user_db.create_user("ada")
        admin = user_db.create_user("root", role="admin")
        # A row saved before submissions were sanitized, or written by hand.
        created = user_db.create_request(
            user_id=requester["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={"title": "Overlord", "author": "Kugane Maruyama"},
            release_data={
                "source": "prowlarr",
                "source_id": "r1",
                "title": "Overlord.epub",
                "destination_key": "grimmory:9:9",
                "extra": {"destination_key": "grimmory:9:9", "indexer": "x"},
            },
        )
        queued: list[dict[str, Any]] = []

        def fake_queue_release(data, priority=0, **_kwargs):
            queued.append(data)
            return True, None

        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=fake_queue_release,
            **kwargs,
        )
        return queued[0]

    def test_a_blank_approval_revives_no_stored_key(self, user_db):
        queued = self.approve(user_db)

        assert queued["destination_key"] is None
        assert queued["extra"] == {"indexer": "x"}

    def test_the_approval_key_is_the_only_key(self, user_db):
        queued = self.approve(user_db, destination_key="grimmory:5:8")

        assert queued["destination_key"] == "grimmory:5:8"
        assert "destination_key" not in queued["extra"]

    def test_queue_release_sees_no_key_after_a_blank_approval(self, user_db, monkeypatch):
        from shelfmark.download import orchestrator

        captured = {}
        monkeypatch.setattr(orchestrator.config, "get", lambda _k, default=None, **_kw: default)
        monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _s: None)
        monkeypatch.setattr(orchestrator.book_queue, "add", lambda t: captured.setdefault("t", t))
        monkeypatch.setattr(orchestrator, "ws_manager", None)

        ok, error = orchestrator.queue_release(self.approve(user_db))

        assert ok, error
        assert captured["t"].destination_key is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_request_destination_authorization.py -q`
Expected: FAIL — 6 of 7: the three submission tests fail with `assert not True` from `_has_key(...)` (the key is stored or queued as sent; the batch test fails on its first `_has_key` assertion), and the three fulfilment tests fail because the stored key survives (`'destination_key' not in {... 'grimmory:9:9' ...}`, and `queue_release` builds a task whose `destination_key == 'grimmory:9:9'`). `test_an_admin_download_policy_submission_keeps_the_key` passes already and must keep passing.

- [ ] **Step 3: Implement**

**`shelfmark/core/request_routes.py`** — add the import directly above `from shelfmark.core.logger import setup_logger`:

```python
from shelfmark.audiobookshelf.destinations import authorize_destination_key
```

In `_prepare_request_create_arguments`, the release data is normalized and then validated twice. Replace the second validation and the policy lookup that follows it:

```python
    _validate_release_source_matches_policy_context(
        source=source,
        release_data=release_data,
    )

    global_settings, user_settings, effective, requests_enabled = _resolve_effective_policy(
```

with:

```python
    _validate_release_source_matches_policy_context(
        source=source,
        release_data=release_data,
    )
    # Routing to a specific library is an admin decision. Without this, a
    # requester could plant a key in release_data (top level or `extra`): a
    # download-policy submission would queue it, and a stored request would
    # carry it to approval.
    if isinstance(release_data, dict):
        release_data = authorize_destination_key(
            release_data, is_admin=bool(session.get("is_admin", False))
        )

    global_settings, user_settings, effective, requests_enabled = _resolve_effective_policy(
```

(Only the second `_validate_release_source_matches_policy_context(...)` call is directly followed by a blank line and `global_settings, ...`, so the anchor is unique.)

**`shelfmark/core/requests_service.py`** — insert directly above `def fulfil_request(`:

```python
def _without_destination_keys(release_data: dict[str, Any]) -> dict[str, Any]:
    """Drop any destination key stored inside a request's release data.

    The only key an approval may carry is the one the approving admin passes
    now. A stored one (top level or in `extra`) would otherwise be revived by
    `queue_release` when the admin left the library blank.
    """
    cleaned = {key: value for key, value in release_data.items() if key != "destination_key"}
    extra = cleaned.get("extra")
    if isinstance(extra, dict) and "destination_key" in extra:
        cleaned["extra"] = {key: value for key, value in extra.items() if key != "destination_key"}
    return cleaned


```

and in `fulfil_request` replace:

```python
    queued_release_data = fill_identity_from_book_data(
        selected_release_data, request_row.get("book_data")
    )
```

with:

```python
    queued_release_data = fill_identity_from_book_data(
        _without_destination_keys(selected_release_data), request_row.get("book_data")
    )
```

(The existing `queued_release_data["destination_key"] = normalized_destination_key` line below then attaches the approval's key — or `None` — as the only one.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_request_destination_authorization.py tests/core/test_request_routes_api.py tests/core/test_requests_service.py tests/core/test_request_identity.py tests/audiobookshelf -q`
Expected: PASS (7 new tests; the existing request API, service and destination tests unchanged).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/core
git add shelfmark/core/request_routes.py shelfmark/core/requests_service.py \
  tests/core/test_request_destination_authorization.py
git commit -m "fix(requests): only an admin's approval key reaches the queue

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: Identity and uploaded files in the custom-script payload; the post-upload hook never fails an upload

**Files:**
- Modify: `shelfmark/download/outputs/booklore.py` (imports; `_utc_now`; `booklore_upload_file` return; new `_run_post_upload_hook`; the upload loop, hook details and hook call in `_post_process_booklore`), `shelfmark/download/postprocess/custom_script.py` (`_build_custom_script_payload`)
- Modify: `tests/core/test_download_processing.py` (one patch gains `return_value=None`)
- Test: `tests/core/test_booklore_payload.py` (new), `tests/core/test_booklore_upload.py` (append)

**Interfaces:**
- Consumes: `DownloadTask.provider/provider_id/isbn_13/asin` (Task 4); explicit-key resolution (Task 2) for `library_id`/`path_id`.
- Produces: `booklore_upload_file(booklore_config, token, file_path) -> dict[str, Any] | None` (the JSON object body, else `None`); `_utc_now() -> str` in `shelfmark.download.outputs.booklore` (tests patch it); `_run_post_upload_hook(script_context, task, status_callback) -> None` (never raises, never reports `"error"`; logs `"Task %s: post-upload custom script failed; the upload stands: %s"` as a warning, or `"Task %s: post-upload custom script crashed; the upload stands"` via `logger.exception`); payload fields `task.provider|provider_id|isbn_13|asin` and `output.details.booklore.uploaded_files|upload_started_at|upload_finished_at` exactly as in Global Constraints.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_booklore_payload.py`:

```python
"""The custom-script payload after a Grimmory upload (fork-only additions, version 1)."""

import json
import subprocess
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import ANY, MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask, SearchMode

SETTINGS = {
    "BOOKS_OUTPUT_MODE": "booklore",
    "BOOKLORE_HOST": "http://grimmory:6060",
    "BOOKLORE_USERNAME": "shelfmark",
    "BOOKLORE_PASSWORD": "secret",
    "BOOKLORE_DESTINATION": "library",
    "BOOKLORE_LIBRARY_ID": 3,
    "BOOKLORE_PATH_ID": 3,
    "CUSTOM_SCRIPT_JSON_PAYLOAD": True,
}

LIBRARIES = [{"id": 5, "name": "Light Novels", "paths": [{"id": 8, "path": "/books/ln"}]}]

IDENTITY = {
    "provider": "hardcover",
    "provider_id": "886465",
    "isbn_13": "9780316005142",
    "asin": "B0BSHZ1234",
}


def _run(tmp_path, *, files, responses, destination_key=None, identity=None):
    """Run the pipeline on a folder of files and return (result, payload, events)."""
    from shelfmark.download.postprocess.router import post_process_download

    staging = tmp_path / "staging"
    source = staging / "release"
    source.mkdir(parents=True)
    for name, content in files.items():
        (source / name).write_bytes(content)

    task = DownloadTask(
        task_id="ebook-1",
        source="direct_download",
        title="Overlord",
        author="Kugane Maruyama",
        format="epub",
        search_mode=SearchMode.DIRECT,
        destination_key=destination_key,
        **(identity or {}),
    )
    events: list[str] = []

    def upload(_config, _token, file_path):
        events.append(f"upload {file_path.name}")
        return responses.get(file_path.name)

    def refresh(_config, _token):
        events.append("refresh")

    def clock():
        events.append("clock")
        return f"2026-10-06T12:00:0{events.count('clock')}+00:00"

    with (
        patch("shelfmark.core.config.config") as mock_config,
        patch("shelfmark.config.env.TMP_DIR", staging),
        patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"),
        patch(
            "shelfmark.download.outputs.booklore.booklore_list_libraries", return_value=LIBRARIES
        ),
        patch("shelfmark.download.outputs.booklore.booklore_upload_file", side_effect=upload),
        patch("shelfmark.download.outputs.booklore.booklore_refresh_library", side_effect=refresh),
        patch("shelfmark.download.outputs.booklore._utc_now", side_effect=clock),
        patch("subprocess.run") as mock_run,
    ):
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None, **_kwargs: SETTINGS.get(key, default)
        )
        mock_config.CUSTOM_SCRIPT = "/opt/shelfmark-hooks/dispatch.py"
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = post_process_download(source, task, Event(), lambda *_args: None)

    payload = json.loads(mock_run.call_args.kwargs["input"])
    return result, payload, events


def test_the_task_carries_the_book_identity(tmp_path):
    _, payload, _ = _run(tmp_path, files={"a.epub": b"1234"}, responses={}, identity=IDENTITY)

    assert payload["version"] == 1
    assert {k: payload["task"][k] for k in IDENTITY} == IDENTITY


def test_identity_fields_are_null_when_unknown(tmp_path):
    _, payload, _ = _run(tmp_path, files={"a.epub": b"1234"}, responses={})

    assert {k: payload["task"][k] for k in IDENTITY} == dict.fromkeys(IDENTITY)


def test_every_uploaded_file_is_listed_with_its_response(tmp_path):
    result, payload, _ = _run(
        tmp_path,
        files={"a.epub": b"1234", "b.epub": b"123456789"},
        responses={"a.epub": {"id": 41, "fileName": "a.epub"}},
    )

    assert result == "booklore://ebook-1"
    uploaded = payload["output"]["details"]["booklore"]["uploaded_files"]
    assert sorted(uploaded, key=lambda f: f["name"]) == [
        {"name": "a.epub", "size_bytes": 4, "response": {"id": 41, "fileName": "a.epub"}},
        {"name": "b.epub", "size_bytes": 9, "response": None},
    ]


def test_the_upload_window_brackets_every_upload_and_the_refresh(tmp_path):
    _, payload, events = _run(tmp_path, files={"a.epub": b"1", "b.epub": b"2"}, responses={})

    details = payload["output"]["details"]["booklore"]
    assert events[0] == "clock"
    assert events[-1] == "clock"
    assert events.count("clock") == 2
    assert events[-2] == "refresh"
    assert details["upload_started_at"] == "2026-10-06T12:00:01+00:00"
    assert details["upload_finished_at"] == "2026-10-06T12:00:02+00:00"


def test_the_payload_names_the_library_actually_used(tmp_path):
    _, payload, _ = _run(
        tmp_path, files={"a.epub": b"1"}, responses={}, destination_key="grimmory:5:8"
    )

    details = payload["output"]["details"]["booklore"]
    assert (details["destination"], details["library_id"], details["path_id"]) == (
        "library",
        5,
        8,
    )


def test_the_real_clock_gives_ordered_utc_timestamps():
    from shelfmark.download.outputs.booklore import _utc_now

    first = datetime.fromisoformat(_utc_now())
    second = datetime.fromisoformat(_utc_now())

    assert first.utcoffset() == timedelta(0)
    assert first <= second


def _run_with_failing_hook(tmp_path, *, run_side_effect=None, payload_error=None):
    """Upload one file, then make the post-upload custom script fail."""
    from shelfmark.download.postprocess.router import post_process_download

    staging = tmp_path / "staging"
    staging.mkdir()
    temp_file = staging / "book.epub"
    temp_file.write_bytes(b"1234")
    task = DownloadTask(
        task_id="ebook-1",
        source="direct_download",
        title="Overlord",
        author="Kugane Maruyama",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )
    statuses: list[tuple[str, str | None]] = []
    uploads: list[str] = []

    with (
        patch("shelfmark.core.config.config") as mock_config,
        patch("shelfmark.config.env.TMP_DIR", staging),
        patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"),
        patch(
            "shelfmark.download.outputs.booklore.booklore_upload_file",
            side_effect=lambda _c, _t, path: uploads.append(path.name),
        ),
        patch("shelfmark.download.outputs.booklore.booklore_refresh_library"),
        patch("shelfmark.download.outputs.booklore.logger") as mock_logger,
        patch("subprocess.run", side_effect=run_side_effect) as mock_run,
        patch(
            "shelfmark.download.postprocess.custom_script._build_custom_script_payload",
            side_effect=payload_error,
            return_value={"version": 1},
        ),
    ):
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None, **_kwargs: SETTINGS.get(key, default)
        )
        mock_config.CUSTOM_SCRIPT = "/opt/shelfmark-hooks/dispatch.py"
        if run_side_effect is None:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = post_process_download(
            temp_file, task, Event(), lambda status, message: statuses.append((status, message))
        )

    return result, statuses, uploads, mock_logger


class TestHookFailureAfterUpload:
    """The book is already in Grimmory, which cannot move or dedupe it.

    Failing the task would invite a retry that uploads it again, so any hook
    failure after a successful upload is logged and the task still completes.
    """

    @pytest.mark.parametrize(
        "run_side_effect",
        [
            FileNotFoundError("/opt/shelfmark-hooks/dispatch.py"),
            PermissionError("/opt/shelfmark-hooks/dispatch.py"),
            subprocess.TimeoutExpired("dispatch.py", 300),
            subprocess.CalledProcessError(1, "dispatch.py", stderr="boom"),
        ],
        ids=["missing", "not-executable", "timeout", "non-zero-exit"],
    )
    def test_a_failing_script_still_completes_the_upload(self, tmp_path, run_side_effect):
        result, statuses, uploads, mock_logger = _run_with_failing_hook(
            tmp_path, run_side_effect=run_side_effect
        )

        assert result == "booklore://ebook-1"
        assert uploads == ["book.epub"]
        assert [status for status, _ in statuses if status == "error"] == []
        assert statuses[-1][0] == "complete"
        mock_logger.warning.assert_any_call(
            "Task %s: post-upload custom script failed; the upload stands: %s",
            "ebook-1",
            ANY,
        )

    def test_a_payload_error_still_completes_the_upload(self, tmp_path):
        result, statuses, uploads, mock_logger = _run_with_failing_hook(
            tmp_path, payload_error=TypeError("not JSON serializable")
        )

        assert result == "booklore://ebook-1"
        assert uploads == ["book.epub"]
        assert statuses[-1][0] == "complete"
        mock_logger.exception.assert_called_once_with(
            "Task %s: post-upload custom script crashed; the upload stands", "ebook-1"
        )
```

Append to the end of `tests/core/test_booklore_upload.py`:

```python
def _upload_with_body(tmp_path, json_side_effect):
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"content")
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = json_side_effect

    with patch("shelfmark.download.outputs.booklore.requests.post", return_value=response):
        return booklore_upload_file(_booklore_config(upload_to_bookdrop=False), "token", file_path)


def test_booklore_upload_file_returns_the_parsed_json_object(tmp_path):
    body = {"id": 41, "fileName": "book.epub"}

    assert _upload_with_body(tmp_path, lambda: body) == body


def test_booklore_upload_file_returns_none_for_a_non_json_body(tmp_path):
    def not_json():
        raise ValueError("Expecting value")

    assert _upload_with_body(tmp_path, not_json) is None


def test_booklore_upload_file_returns_none_for_json_that_is_not_an_object(tmp_path):
    # Review Focus #3: "OK", [ ... ] and null are JSON but not an object.
    for body in ("OK", [{"id": 41}], None, 41):
        assert _upload_with_body(tmp_path, lambda body=body: body) is None, repr(body)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_booklore_payload.py tests/core/test_booklore_upload.py -q`
Expected: FAIL — 12 failed, 4 passed: `AttributeError: <module 'shelfmark.download.outputs.booklore'> does not have the attribute '_utc_now'` for the five pipeline payload tests; `ImportError: cannot import name '_utc_now'` for the clock test; `test_booklore_upload_file_returns_the_parsed_json_object` with `assert None == {'id': 41, ...}`; and all five `TestHookFailureAfterUpload` tests with `assert None == 'booklore://ebook-1'` (today a hook failure, or a payload error caught by the outer `except (OSError, TypeError, ValueError)`, fails the task after the upload). The two "returns none" upload tests and the two existing upload tests pass already.

- [ ] **Step 3: Implement**

**`shelfmark/download/outputs/booklore.py`** — seven edits.

1. Imports: replace

```python
import os
from pathlib import Path
```

with

```python
import os
from datetime import UTC, datetime
from pathlib import Path
```

2. Replace the head of `booklore_upload_file`:

```python
def booklore_upload_file(booklore_config: BookloreConfig, token: str, file_path: Path) -> None:
    """Upload a completed file into Booklore."""
```

with:

```python
def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string (for the hook payload)."""
    return datetime.now(UTC).isoformat()


def booklore_upload_file(
    booklore_config: BookloreConfig, token: str, file_path: Path
) -> dict[str, Any] | None:
    """Upload a completed file into Booklore.

    Returns Grimmory's response body when it is a JSON object, else None. It is
    passed on to the custom script, which may use it to find the new book.
    """
```

3. At the end of `booklore_upload_file`, after its last `except` clause, replace:

```python
    except requests.exceptions.RequestException as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} upload failed: {exc}"
        raise BookloreError(msg) from exc


def booklore_refresh_library(
```

with:

```python
    except requests.exceptions.RequestException as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} upload failed: {exc}"
        raise BookloreError(msg) from exc

    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def booklore_refresh_library(
```

4. In `_post_process_booklore`, replace the upload loop and refresh:

```python
        for index, file_path in enumerate(prepared.files, start=1):
            if cancel_flag.is_set():
                logger.info("Task %s: cancelled during Booklore upload", task.task_id)
                return None
            status_callback(
                "resolving",
                f"Uploading to {BOOKLORE_DISPLAY_NAME} ({index}/{len(prepared.files)})",
            )
            booklore_upload_file(booklore_config, token, file_path)

        if booklore_config.refresh_after_upload:
            try:
                booklore_refresh_library(booklore_config, token)
            except BookloreError as e:
                logger.warning("Task %s: Booklore refresh failed: %s", task.task_id, e)
```

with:

```python
        uploaded_files: list[dict[str, Any]] = []
        upload_started_at = _utc_now()
        for index, file_path in enumerate(prepared.files, start=1):
            if cancel_flag.is_set():
                logger.info("Task %s: cancelled during Booklore upload", task.task_id)
                return None
            status_callback(
                "resolving",
                f"Uploading to {BOOKLORE_DISPLAY_NAME} ({index}/{len(prepared.files)})",
            )
            size_bytes = file_path.stat().st_size
            upload_response = booklore_upload_file(booklore_config, token, file_path)
            uploaded_files.append(
                {"name": file_path.name, "size_bytes": size_bytes, "response": upload_response}
            )

        if booklore_config.refresh_after_upload:
            try:
                booklore_refresh_library(booklore_config, token)
            except BookloreError as e:
                logger.warning("Task %s: Booklore refresh failed: %s", task.task_id, e)
        upload_finished_at = _utc_now()
```

and in the `output_details` dict a few lines below, replace:

```python
                    "refresh_after_upload": bool(booklore_config.refresh_after_upload),
                }
```

with:

```python
                    "refresh_after_upload": bool(booklore_config.refresh_after_upload),
                    # Fork-only: what a tagging hook needs to find the new book.
                    "uploaded_files": uploaded_files,
                    "upload_started_at": upload_started_at,
                    "upload_finished_at": upload_finished_at,
                }
```

5. In the `if TYPE_CHECKING:` block at the top, replace:

```python
    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)
```

with:

```python
    from shelfmark.core.models import DownloadTask
    from shelfmark.download.postprocess.custom_script import CustomScriptContext

logger = setup_logger(__name__)
```

6. Insert this helper directly above `def _post_process_booklore(`:

```python
def _run_post_upload_hook(
    script_context: CustomScriptContext,
    task: DownloadTask,
    status_callback: StatusCallback,
) -> None:
    """Run the custom script after a Grimmory upload without failing the task.

    The book is already in Grimmory, which cannot move or dedupe it: failing
    the task here would invite a retry that uploads it a second time. So a
    missing or non-executable script, a timeout, a non-zero exit, or an error
    while building its payload is logged, and the upload still counts (fork-only;
    folder and email outputs keep failing the task as before).
    """
    from shelfmark.download.postprocess.pipeline import maybe_run_custom_script

    failures: list[str] = []

    def hook_status(status: str, message: str | None) -> None:
        if status == "error":
            failures.append(message or "custom script failed")
        else:
            status_callback(status, message)

    try:
        succeeded = maybe_run_custom_script(script_context, status_callback=hook_status)
    except Exception:
        logger.exception(
            "Task %s: post-upload custom script crashed; the upload stands", task.task_id
        )
        return
    if not succeeded:
        logger.warning(
            "Task %s: post-upload custom script failed; the upload stands: %s",
            task.task_id,
            "; ".join(failures) or "unknown error",
        )


```

7. In `_post_process_booklore`, delete `        maybe_run_custom_script,` from the local `from shelfmark.download.postprocess.pipeline import (...)` block (the helper imports it now), and replace:

```python
        if not maybe_run_custom_script(script_context, status_callback=status_callback):
            return None
```

with:

```python
        _run_post_upload_hook(script_context, task, status_callback)
```

(`maybe_run_custom_script`, `run_custom_script` and the folder/email outputs are untouched: there, a script failure still fails the task, as `tests/core/test_download_processing.py::TestCustomScriptExecution` already pins.)

**`shelfmark/download/postprocess/custom_script.py`** — in `_build_custom_script_payload`, replace:

```python
            "original_download_path": context.task.original_download_path,
        },
```

with:

```python
            "original_download_path": context.task.original_download_path,
            # Fork-only book identity, for hooks that tag the book.
            "provider": context.task.provider,
            "provider_id": context.task.provider_id,
            "isbn_13": context.task.isbn_13,
            "asin": context.task.asin,
        },
```

**`tests/core/test_download_processing.py`** — `test_runs_custom_script_for_booklore_output_with_json_payload` patches the upload without a return value, so the stub now returns a `MagicMock` that cannot be serialized into the payload. In that test replace:

```python
            patch("shelfmark.download.outputs.booklore.booklore_upload_file"),
```

with:

```python
            patch("shelfmark.download.outputs.booklore.booklore_upload_file", return_value=None),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_booklore_payload.py tests/core/test_booklore_upload.py tests/core/test_download_processing.py tests/core/test_processing_integration.py tests/core/test_booklore_target.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/download
git add shelfmark/download/outputs/booklore.py shelfmark/download/postprocess/custom_script.py \
  tests/core/test_booklore_payload.py tests/core/test_booklore_upload.py tests/core/test_download_processing.py
git commit -m "feat(hooks): identity and uploaded files in the custom-script payload; post-upload hook is best-effort

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Combined mode keeps one destination key per leg through Next/Back

**Files:**
- Create: `src/frontend/src/utils/combinedSelection.ts`
- Modify: `src/frontend/src/App.tsx` (imports; delete the local `CombinedSelectionState` type; `executeCombinedAction`; `handleCombinedNext`; `handleCombinedBack`; `handleCombinedDownload`; the `combinedMode` prop passed to `ReleaseModal`)
- Modify: `src/frontend/src/components/ReleaseModal.tsx` (`CombinedModeConfig`; staged-key reads; the Back and Next buttons)
- Test: `src/frontend/src/tests/combinedSelection.test.ts` (new)

All paths below are relative to `src/frontend/`.

**Interfaces:**
- Consumes: `buildReleaseDownloadPayload`, `ReleaseDownloadOptions` from `src/utils/releasePayload.ts` (unchanged).
- Produces (in `src/utils/combinedSelection.ts`): `interface CombinedSelectionState { phase: ContentType; ebookMode; audiobookMode; stagedEbook?; stagedAudiobook?; ebookDestinationKey?: string; audiobookDestinationKey?: string }`; `stagedDestinationKeyForPhase(state, phase) -> string | undefined`; `advanceCombinedSelection(state, nextPhase, book, release | null, destinationKey | undefined)`; `retreatCombinedSelection(state, audiobookRelease | null, destinationKey | undefined)`; `completeCombinedSelection(state, book, release | null, destinationKey | undefined)` — each returns a new `CombinedSelectionState` and stores `destinationKey` on the phase being *left* (`state.phase`); `combinedLegOptions(state, leg) -> ReleaseDownloadOptions` (`{ destinationKey }` of that leg only). `ReleaseModal`'s `CombinedModeConfig` gains `stagedEbookDestinationKey?`, `stagedAudiobookDestinationKey?`, and `onNext(release, destinationKey?)` / `onBack(audiobookRelease, destinationKey?)`.

- [ ] **Step 1: Write the failing test**

Create `src/tests/combinedSelection.test.ts`:

```ts
import { describe, it, expect } from 'vitest';

import type { Book, Release } from '../types';
import {
  advanceCombinedSelection,
  combinedLegOptions,
  type CombinedSelectionState,
  completeCombinedSelection,
  retreatCombinedSelection,
  stagedDestinationKeyForPhase,
} from '../utils/combinedSelection';
import { buildReleaseDownloadPayload } from '../utils/releasePayload';

const book: Book = {
  id: 'hc-1',
  title: 'Overlord',
  author: 'Kugane Maruyama',
  provider: 'hardcover',
  provider_id: '886465',
};

const ebookRelease: Release = { source: 'prowlarr', source_id: 'ebook-1', title: 'Overlord.epub' };
const audiobookRelease: Release = {
  source: 'audiobookbay',
  source_id: 'audio-1',
  title: 'Overlord (Unabridged)',
};

const start: CombinedSelectionState = {
  phase: 'ebook',
  ebookMode: 'download',
  audiobookMode: 'download',
};

describe('combined selection keeps one library per leg', () => {
  it('survives Next → Back → Next → Download', () => {
    // Ebook step: pick a release and Light Novels, then Next.
    let state = advanceCombinedSelection(start, 'audiobook', book, ebookRelease, 'grimmory:5:8');
    expect(state.phase).toBe('audiobook');
    expect(stagedDestinationKeyForPhase(state, 'audiobook')).toBeUndefined();

    // Audiobook step: pick Kids, then Back — the ebook picker shows Light Novels again.
    state = retreatCombinedSelection(state, audiobookRelease, 'lib-kids');
    expect(state.phase).toBe('ebook');
    expect(stagedDestinationKeyForPhase(state, 'ebook')).toBe('grimmory:5:8');

    // Change the ebook library, Next — the audiobook picker shows Kids again.
    state = advanceCombinedSelection(state, 'audiobook', book, ebookRelease, 'grimmory:3:3');
    expect(stagedDestinationKeyForPhase(state, 'audiobook')).toBe('lib-kids');

    // Download from the audiobook step.
    state = completeCombinedSelection(state, book, audiobookRelease, 'lib-kids');
    expect(state.stagedEbook?.release).toBe(ebookRelease);
    expect(state.stagedAudiobook).toBe(audiobookRelease);
    expect(combinedLegOptions(state, 'ebook')).toEqual({ destinationKey: 'grimmory:3:3' });
    expect(combinedLegOptions(state, 'audiobook')).toEqual({ destinationKey: 'lib-kids' });
  });

  it('never sends one leg the other leg key', () => {
    const state = completeCombinedSelection(
      advanceCombinedSelection(start, 'audiobook', book, ebookRelease, 'grimmory:5:8'),
      book,
      audiobookRelease,
      'lib-kids',
    );

    const ebookPayload = buildReleaseDownloadPayload(
      book,
      ebookRelease,
      'ebook',
      combinedLegOptions(state, 'ebook'),
    );
    const audiobookPayload = buildReleaseDownloadPayload(
      book,
      audiobookRelease,
      'audiobook',
      combinedLegOptions(state, 'audiobook'),
    );

    expect(ebookPayload.destination_key).toBe('grimmory:5:8');
    expect(audiobookPayload.destination_key).toBe('lib-kids');
  });

  it('a skipped ebook step leaves the ebook leg without a key', () => {
    const state = completeCombinedSelection(
      advanceCombinedSelection(start, 'audiobook', book, null, undefined),
      book,
      audiobookRelease,
      'lib-kids',
    );

    expect(state.stagedEbook).toBeUndefined();
    expect(combinedLegOptions(state, 'ebook')).toEqual({ destinationKey: undefined });
    expect(combinedLegOptions(state, 'audiobook')).toEqual({ destinationKey: 'lib-kids' });
  });

  it('an audiobook-only flow never gives the ebook leg a key', () => {
    // The ebook leg is a request, so the selection starts on the audiobook step.
    const audiobookOnly: CombinedSelectionState = {
      ...start,
      phase: 'audiobook',
      ebookMode: 'request_book',
    };

    const state = completeCombinedSelection(audiobookOnly, book, audiobookRelease, 'lib-kids');

    expect(combinedLegOptions(state, 'ebook').destinationKey).toBeUndefined();
    expect(combinedLegOptions(state, 'audiobook').destinationKey).toBe('lib-kids');
  });

  it('an ebook-only flow stores the key on the ebook leg', () => {
    const ebookOnly: CombinedSelectionState = { ...start, audiobookMode: 'request_book' };

    const state = completeCombinedSelection(ebookOnly, book, ebookRelease, 'grimmory:5:8');

    expect(state.stagedEbook?.release).toBe(ebookRelease);
    expect(combinedLegOptions(state, 'ebook').destinationKey).toBe('grimmory:5:8');
    expect(combinedLegOptions(state, 'audiobook').destinationKey).toBeUndefined();
  });

  it('clearing a pick on Back keeps the stored library', () => {
    const forward = advanceCombinedSelection(
      start,
      'audiobook',
      book,
      ebookRelease,
      'grimmory:5:8',
    );

    const back = retreatCombinedSelection(forward, null, undefined);

    expect(back.stagedAudiobook).toBeUndefined();
    expect(stagedDestinationKeyForPhase(back, 'ebook')).toBe('grimmory:5:8');
    expect(stagedDestinationKeyForPhase(back, 'audiobook')).toBeUndefined();
  });

  it('does not modify the state it was given', () => {
    advanceCombinedSelection(start, 'audiobook', book, ebookRelease, 'grimmory:5:8');

    expect(start).toEqual({ phase: 'ebook', ebookMode: 'download', audiobookMode: 'download' });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/frontend && npx vitest run src/tests/combinedSelection.test.ts`
Expected: FAIL — `Error: Cannot find module '../utils/combinedSelection'`.

- [ ] **Step 3: Implement**

**`src/utils/combinedSelection.ts`** (new):

```ts
import type { Book, ContentType, Release, RequestPolicyMode } from '../types';
import type { ReleaseDownloadOptions } from './releasePayload';

/** The ebook + audiobook selection in combined mode (both formats in one go). */
export interface CombinedSelectionState {
  phase: ContentType;
  ebookMode: RequestPolicyMode;
  audiobookMode: RequestPolicyMode;
  stagedEbook?: { book: Book; release: Release };
  stagedAudiobook?: Release;
  // Each leg keeps its own library (fork-only): a Grimmory key for the ebook,
  // an Audiobookshelf key for the audiobook. A leg never carries the other's.
  ebookDestinationKey?: string;
  audiobookDestinationKey?: string;
}

type DestinationKeys = Pick<
  CombinedSelectionState,
  'ebookDestinationKey' | 'audiobookDestinationKey'
>;

const withPhaseDestinationKey = (
  state: CombinedSelectionState,
  phase: ContentType,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  phase === 'ebook'
    ? { ...state, ebookDestinationKey: destinationKey }
    : { ...state, audiobookDestinationKey: destinationKey };

/** The library a phase's picker shows when the admin (re)enters that phase. */
export const stagedDestinationKeyForPhase = (
  state: DestinationKeys,
  phase: ContentType,
): string | undefined =>
  phase === 'ebook' ? state.ebookDestinationKey : state.audiobookDestinationKey;

/** Next: stage the ebook pick and the current phase's library, move on. */
export const advanceCombinedSelection = (
  state: CombinedSelectionState,
  nextPhase: ContentType,
  book: Book,
  release: Release | null,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  withPhaseDestinationKey(
    { ...state, phase: nextPhase, stagedEbook: release ? { book, release } : undefined },
    state.phase,
    destinationKey,
  );

/** Back: stage the audiobook pick and the current phase's library, return to the ebook. */
export const retreatCombinedSelection = (
  state: CombinedSelectionState,
  audiobookRelease: Release | null,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  withPhaseDestinationKey(
    { ...state, phase: 'ebook', stagedAudiobook: audiobookRelease ?? undefined },
    state.phase,
    destinationKey,
  );

/** Download: stage the final phase's pick and library. */
export const completeCombinedSelection = (
  state: CombinedSelectionState,
  book: Book,
  release: Release | null,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  withPhaseDestinationKey(
    state.phase === 'ebook'
      ? { ...state, stagedEbook: release ? { book, release } : undefined }
      : { ...state, stagedAudiobook: release ?? undefined },
    state.phase,
    destinationKey,
  );

/** Download options for one leg: that leg's own library, and nothing else. */
export const combinedLegOptions = (
  state: DestinationKeys,
  leg: ContentType,
): ReleaseDownloadOptions => ({ destinationKey: stagedDestinationKeyForPhase(state, leg) });
```

**`src/App.tsx`** — six edits.

1. Add the import directly after `import { buildSearchQuery } from './utils/buildSearchQuery';`:

```ts
import {
  advanceCombinedSelection,
  combinedLegOptions,
  type CombinedSelectionState,
  completeCombinedSelection,
  retreatCombinedSelection,
} from './utils/combinedSelection';
```

2. Delete the local type (and the blank line after it):

```ts
type CombinedSelectionState = {
  phase: 'ebook' | 'audiobook';
  ebookMode: RequestPolicyMode;
  audiobookMode: RequestPolicyMode;
  stagedEbook?: { book: Book; release: Release };
  stagedAudiobook?: Release;
  // Applies to the audiobook leg only — the ebook lane has one destination.
  destinationKey?: string;
};
```

3. In `executeCombinedAction`, replace:

```ts
        await executeReleaseDownload(book, ebookRelease, 'ebook', onBehalfOfUserId);
```

with:

```ts
        await executeReleaseDownload(
          book,
          ebookRelease,
          'ebook',
          onBehalfOfUserId,
          combinedLegOptions(selection, 'ebook'),
        );
```

and replace:

```ts
        await executeReleaseDownload(book, audiobookRelease, 'audiobook', onBehalfOfUserId, {
          destinationKey: selection.destinationKey,
        });
```

with:

```ts
        await executeReleaseDownload(
          book,
          audiobookRelease,
          'audiobook',
          onBehalfOfUserId,
          combinedLegOptions(selection, 'audiobook'),
        );
```

(The on-behalf confirmation replays the same stored `combinedState` through `executeCombinedAction`, so it inherits both legs' keys.)

4. Replace `handleCombinedNext` and `handleCombinedBack`:

```ts
  const handleCombinedNext = useCallback(
    (release: Release | null) => {
      if (!releaseBook || !combinedState) return;
      const phases = getCombinedSelectionPhases(combinedState);
      const nextPhase = phases[phases.indexOf(combinedState.phase) + 1];

      setCombinedState({
        ...combinedState,
        phase: nextPhase,
        stagedEbook: release ? { book: releaseBook, release } : undefined,
      });
    },
    [combinedState, getCombinedSelectionPhases, releaseBook],
  );

  const handleCombinedBack = useCallback((audiobookRelease: Release | null) => {
    setCombinedState((prev) =>
      prev ? { ...prev, phase: 'ebook', stagedAudiobook: audiobookRelease ?? undefined } : null,
    );
  }, []);
```

with:

```ts
  const handleCombinedNext = useCallback(
    (release: Release | null, destinationKey?: string) => {
      if (!releaseBook || !combinedState) return;
      const phases = getCombinedSelectionPhases(combinedState);
      const nextPhase = phases[phases.indexOf(combinedState.phase) + 1];

      setCombinedState(
        advanceCombinedSelection(combinedState, nextPhase, releaseBook, release, destinationKey),
      );
    },
    [combinedState, getCombinedSelectionPhases, releaseBook],
  );

  const handleCombinedBack = useCallback(
    (audiobookRelease: Release | null, destinationKey?: string) => {
      setCombinedState((prev) =>
        prev ? retreatCombinedSelection(prev, audiobookRelease, destinationKey) : null,
      );
    },
    [],
  );
```

5. In `handleCombinedDownload`, replace:

```ts
      const nextCombinedState: CombinedSelectionState =
        combinedState.phase === 'ebook'
          ? {
              ...combinedState,
              stagedEbook: release ? { book: releaseBook, release } : undefined,
              destinationKey,
            }
          : {
              ...combinedState,
              stagedAudiobook: release ?? undefined,
              destinationKey,
            };
```

with:

```ts
      const nextCombinedState = completeCombinedSelection(
        combinedState,
        releaseBook,
        release,
        destinationKey,
      );
```

6. In the `<ReleaseModal combinedMode={...}>` object, replace:

```tsx
                      stagedAudiobookRelease: effectiveCombinedState.stagedAudiobook ?? null,
```

with:

```tsx
                      stagedAudiobookRelease: effectiveCombinedState.stagedAudiobook ?? null,
                      stagedEbookDestinationKey: effectiveCombinedState.ebookDestinationKey,
                      stagedAudiobookDestinationKey: effectiveCombinedState.audiobookDestinationKey,
```

`CombinedSelectionState` stays referenced in App (`PendingOnBehalfDownload`, `useState<CombinedSelectionState | null>`, `executeCombinedAction`'s parameter), now from the import.

**`src/components/ReleaseModal.tsx`** — four edits.

1. In `interface CombinedModeConfig`, replace:

```ts
  onNext?: (release: Release | null) => void;
  onBack?: (audiobookRelease: Release | null) => void;
```

with:

```ts
  // The library each phase's picker last held, restored on Next/Back (fork-only).
  stagedEbookDestinationKey?: string;
  stagedAudiobookDestinationKey?: string;
  // Each carries the library picked on the phase being left.
  onNext?: (release: Release | null, destinationKey?: string) => void;
  onBack?: (audiobookRelease: Release | null, destinationKey?: string) => void;
```

2. In `ReleaseModalSession`, directly after `const stagedAudiobookRelease = combinedMode?.stagedAudiobookRelease ?? null;`, add:

```ts
  const stagedEbookDestinationKey = combinedMode?.stagedEbookDestinationKey ?? '';
  const stagedAudiobookDestinationKey = combinedMode?.stagedAudiobookDestinationKey ?? '';
```

3. In the combined footer's **Back** button, replace the `onClick` body:

```tsx
                        const picked = selectedRelease;
                        setSelectedRelease(stagedEbookRelease);
                        onCombinedBack(picked);
```

with:

```tsx
                        const picked = selectedRelease;
                        const leavingKey = chosenDestinationKey;
                        setSelectedRelease(stagedEbookRelease);
                        setDestinationKey(stagedEbookDestinationKey);
                        onCombinedBack(picked, leavingKey);
```

4. In the **Next** button (`combinedPhase === 'ebook' && onCombinedNext`), replace the `onClick` body:

```tsx
                        if (selectedRelease) {
                          const picked = selectedRelease;
                          setSelectedRelease(stagedAudiobookRelease);
                          onCombinedNext(picked);
                        } else {
                          setSelectedRelease(stagedAudiobookRelease);
                          onCombinedNext(null);
                        }
```

with:

```tsx
                        const leavingKey = chosenDestinationKey;
                        setDestinationKey(stagedAudiobookDestinationKey);
                        if (selectedRelease) {
                          const picked = selectedRelease;
                          setSelectedRelease(stagedAudiobookRelease);
                          onCombinedNext(picked, leavingKey);
                        } else {
                          setSelectedRelease(stagedAudiobookRelease);
                          onCombinedNext(null, leavingKey);
                        }
```

(The modal session is keyed by book, not phase, so `destinationKey` state survives a phase switch; these two `setDestinationKey` calls are what restore each phase's own value. The Download button already passes `chosenDestinationKey`, now stored on the current phase by `completeCombinedSelection`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/frontend && npx vitest run src/tests/combinedSelection.test.ts && npm run test:unit`
Expected: PASS (7 new tests; whole suite green).

- [ ] **Step 5: Typecheck, lint, format, commit**

```bash
cd src/frontend && npm run typecheck && npm run lint && npm run format && npm run format:check
cd ../.. && git add src/frontend/src/utils/combinedSelection.ts src/frontend/src/tests/combinedSelection.test.ts \
  src/frontend/src/App.tsx src/frontend/src/components/ReleaseModal.tsx
git commit -m "feat(frontend): per-phase destination keys in combined mode

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Generic destination picker — one endpoint, both formats, neutral ebook label

**Files:**
- Rename + rewrite: `src/frontend/src/utils/audiobookDestinations.ts` → `src/frontend/src/utils/downloadDestinations.ts`
- Rename + rewrite: `src/frontend/src/hooks/useAudiobookDestinations.ts` → `src/frontend/src/hooks/useDownloadDestinations.ts`
- Rename + rewrite: `src/frontend/src/tests/audiobookDestinations.test.ts` → `src/frontend/src/tests/downloadDestinations.test.ts`
- Create: `src/frontend/src/components/activity/reviewApproval.ts`, `src/frontend/src/tests/reviewApproval.test.ts`
- Modify: `src/frontend/src/services/api.ts` (imports; `getAudiobookDestinations` → `getDownloadDestinations`; `destination_key` comment), `src/frontend/src/utils/releasePayload.ts` (import path; doc comment), `src/frontend/src/components/ReleaseModal.tsx`, `src/frontend/src/components/activity/ActivityCard.tsx`

All paths below are relative to `src/frontend/`.

**Interfaces:**
- Consumes: `GET /api/download-destinations?content_type=` (Task 3).
- Produces (in `src/utils/downloadDestinations.ts`): `interface DownloadDestination { key; name }`; `interface DownloadDestinationList { destinations: DownloadDestination[]; defaultName: string }`; `EMPTY_DESTINATION_LIST`; `shouldShowDestinationPicker(contentType: string | null | undefined, destinations, selectedKey?) -> boolean` (ebook or audiobook with more than one destination, or an ebook with a nonblank `selectedKey`); `destinationDefaultLabel(contentType: ContentType, defaultName: string) -> string`; `resolveDefaultDestinationKey` and `withDestinationKey` (unchanged behaviour); `resolveSelectedDestinationKey(contentType, currentKey, destinations) -> string` (ebook: the trimmed pick, never blanked; audiobook: `resolveDefaultDestinationKey`); `destinationKeyToSend(contentType, currentKey, destinations) -> string | undefined` (ebook: any nonblank pick; audiobook: a listed key, only while the picker shows); `pickerDestinations(contentType, selectedKey, destinations) -> DownloadDestination[]` (adds `"<key> (not in the current list)"` for an unlisted ebook pick); `createDestinationLoader(fetch) -> (contentType) => Promise<DownloadDestinationList>` (per-type cache; an error resolves to `EMPTY_DESTINATION_LIST`; neither an error nor an empty list is cached). In `src/components/activity/reviewApproval.ts`: `interface RequestApproveOptions { browseOnly?; manualApproval?; destinationKey? }` and `reviewApproveOptions(action: 'approve' | 'browse' | 'manual', destinationKey?: string) -> RequestApproveOptions`. `getDownloadDestinations(contentType: ContentType): Promise<DownloadDestinationList>` in `services/api.ts`. `useDownloadDestinations(contentType: ContentType | null): DownloadDestinationList`.

- [ ] **Step 1: Write the failing test**

```bash
cd src/frontend
git mv src/tests/audiobookDestinations.test.ts src/tests/downloadDestinations.test.ts
```

Replace the whole content of `src/tests/downloadDestinations.test.ts` with:

```ts
import { describe, it, expect } from 'vitest';

import { buildFulfilAdminRequestBody } from '../services/requestApiHelpers';
import {
  createDestinationLoader,
  destinationDefaultLabel,
  destinationKeyToSend,
  type DownloadDestinationList,
  pickerDestinations,
  resolveDefaultDestinationKey,
  resolveSelectedDestinationKey,
  shouldShowDestinationPicker,
  withDestinationKey,
} from '../utils/downloadDestinations';

const DESTINATIONS = [
  { key: 'lib-fiction', name: 'Fiction' },
  { key: 'lib-kids', name: 'Kids' },
];

const EBOOK_DESTINATIONS = [
  { key: 'grimmory:3:3', name: 'Fiction' },
  { key: 'grimmory:5:8', name: 'Light Novels' },
];

describe('fulfil payload with a destination key', () => {
  it('includes the chosen library', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      destination_key: 'lib-kids',
    });

    expect(body.destination_key).toBe('lib-kids');
  });

  it('carries an ebook library key the same way', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      destination_key: 'grimmory:5:8',
    });

    expect(body.destination_key).toBe('grimmory:5:8');
  });

  it('omits the key entirely when no library was chosen', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
    });

    expect('destination_key' in body).toBe(false);
  });
});

describe('shouldShowDestinationPicker', () => {
  it('shows the picker for audiobooks with more than one destination', () => {
    expect(shouldShowDestinationPicker('audiobook', DESTINATIONS)).toBe(true);
  });

  it('shows the picker for ebooks with more than one destination', () => {
    expect(shouldShowDestinationPicker('ebook', EBOOK_DESTINATIONS)).toBe(true);
  });

  it('hides the picker when only one destination is configured', () => {
    expect(shouldShowDestinationPicker('audiobook', [DESTINATIONS[0]])).toBe(false);
    expect(shouldShowDestinationPicker('ebook', [EBOOK_DESTINATIONS[0]])).toBe(false);
  });

  it('hides the picker when nothing is configured', () => {
    expect(shouldShowDestinationPicker('ebook', [])).toBe(false);
  });

  it('hides the picker for an unknown content type', () => {
    expect(shouldShowDestinationPicker(null, DESTINATIONS)).toBe(false);
    expect(shouldShowDestinationPicker('magazine', DESTINATIONS)).toBe(false);
  });
});

describe('destinationDefaultLabel', () => {
  it('names the default when the server knows it', () => {
    expect(destinationDefaultLabel('audiobook', 'Fiction')).toBe('Default (Fiction)');
  });

  it('keeps the existing audiobook label when the name is unknown', () => {
    expect(destinationDefaultLabel('audiobook', '')).toBe('Default audiobook destination');
  });

  it('labels the ebook default neutrally', () => {
    // The library a blank choice lands in depends on the target user's overrides.
    expect(destinationDefaultLabel('ebook', '')).toBe('Default ebook destination');
    expect(destinationDefaultLabel('ebook', '   ')).toBe('Default ebook destination');
  });
});

describe('resolveDefaultDestinationKey', () => {
  it('keeps the previously chosen library when it still exists', () => {
    expect(resolveDefaultDestinationKey('lib-kids', DESTINATIONS)).toBe('lib-kids');
  });

  it('falls back to no choice when the library is gone', () => {
    expect(resolveDefaultDestinationKey('lib-deleted', DESTINATIONS)).toBe('');
  });

  it('drops a key from the other format', () => {
    expect(resolveDefaultDestinationKey('lib-kids', EBOOK_DESTINATIONS)).toBe('');
  });

  it('defaults to no choice, which routes to the default destination', () => {
    expect(resolveDefaultDestinationKey(null, DESTINATIONS)).toBe('');
  });
});

describe('withDestinationKey', () => {
  it('adds the chosen library to a direct-download payload', () => {
    expect(withDestinationKey({ source: 'prowlarr' }, 'lib-kids')).toEqual({
      source: 'prowlarr',
      destination_key: 'lib-kids',
    });
  });

  it('omits the key when no library was chosen', () => {
    expect('destination_key' in withDestinationKey({ source: 'prowlarr' }, undefined)).toBe(false);
  });

  it('omits the key for a blank choice rather than sending an empty one', () => {
    // '' is the picker's own value for "use the default destination", so
    // forwarding it would put a key on the wire that means nothing.
    expect('destination_key' in withDestinationKey({ source: 'prowlarr' }, '   ')).toBe(false);
  });

  it('leaves the payload it was given untouched', () => {
    const payload = { source: 'prowlarr' };

    withDestinationKey(payload, 'lib-kids');

    expect('destination_key' in payload).toBe(false);
  });
});

const list = (name: string): DownloadDestinationList => ({
  destinations: [{ key: name, name }],
  defaultName: '',
});

describe('createDestinationLoader', () => {
  it('fetches each content type once and caches it separately', async () => {
    const calls: string[] = [];
    const load = createDestinationLoader(async (contentType) => {
      calls.push(contentType);
      return list(contentType);
    });

    expect(await load('ebook')).toEqual(list('ebook'));
    expect(await load('audiobook')).toEqual(list('audiobook'));
    expect(await load('ebook')).toEqual(list('ebook'));
    expect(calls).toEqual(['ebook', 'audiobook']);
  });

  it('never caches an empty list, so a Grimmory outage clears up on its own', async () => {
    // The server answers 200 [] while Grimmory is unreachable.
    const responses = [{ destinations: [], defaultName: '' }, list('ebook')];
    const load = createDestinationLoader(async () => responses.shift() ?? list('late'));

    expect(await load('ebook')).toEqual({ destinations: [], defaultName: '' });
    expect(await load('ebook')).toEqual(list('ebook'));
    expect(await load('ebook')).toEqual(list('ebook'));
    expect(responses).toEqual([]);
  });

  it('yields an empty list on error and retries next time', async () => {
    let attempts = 0;
    const load = createDestinationLoader(async (contentType) => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error('offline');
      }
      return list(contentType);
    });

    expect(await load('ebook')).toEqual({ destinations: [], defaultName: '' });
    expect(await load('ebook')).toEqual(list('ebook'));
  });
});

// An explicit ebook pick is never erased in the browser: the list may still be
// loading, empty because Grimmory is down, or stale. The server verifies the key
// against a fresh read and fails closed, so the browser sends it unchanged.
describe('an explicit ebook pick survives the display list', () => {
  const loading: typeof EBOOK_DESTINATIONS = [];

  it('is kept while the list is still loading', () => {
    expect(resolveSelectedDestinationKey('ebook', 'grimmory:5:8', loading)).toBe('grimmory:5:8');
    expect(destinationKeyToSend('ebook', 'grimmory:5:8', loading)).toBe('grimmory:5:8');
  });

  it('is kept when the loaded list no longer has it', () => {
    expect(destinationKeyToSend('ebook', 'grimmory:9:9', EBOOK_DESTINATIONS)).toBe('grimmory:9:9');
  });

  it('keeps the picker visible so the admin can see and clear it', () => {
    expect(shouldShowDestinationPicker('ebook', loading, 'grimmory:5:8')).toBe(true);
    expect(shouldShowDestinationPicker('ebook', loading, '')).toBe(false);
  });

  it('lists an unlisted pick as its own option', () => {
    expect(pickerDestinations('ebook', 'grimmory:9:9', EBOOK_DESTINATIONS)).toEqual([
      ...EBOOK_DESTINATIONS,
      { key: 'grimmory:9:9', name: 'grimmory:9:9 (not in the current list)' },
    ]);
    expect(pickerDestinations('ebook', 'grimmory:5:8', EBOOK_DESTINATIONS)).toEqual(
      EBOOK_DESTINATIONS,
    );
    expect(pickerDestinations('ebook', '', EBOOK_DESTINATIONS)).toEqual(EBOOK_DESTINATIONS);
  });

  it('a blank ebook choice sends nothing', () => {
    expect(destinationKeyToSend('ebook', '  ', EBOOK_DESTINATIONS)).toBeUndefined();
    expect(destinationKeyToSend('ebook', undefined, EBOOK_DESTINATIONS)).toBeUndefined();
  });
});

describe('audiobook picks keep the existing fallback', () => {
  it('drops a key the list no longer has', () => {
    expect(resolveSelectedDestinationKey('audiobook', 'lib-gone', DESTINATIONS)).toBe('');
    expect(destinationKeyToSend('audiobook', 'lib-gone', DESTINATIONS)).toBeUndefined();
  });

  it('sends nothing while the picker is hidden', () => {
    expect(destinationKeyToSend('audiobook', 'lib-kids', [])).toBeUndefined();
    expect(destinationKeyToSend('audiobook', 'lib-kids', [DESTINATIONS[1]])).toBeUndefined();
    expect(shouldShowDestinationPicker('audiobook', [], 'lib-kids')).toBe(false);
  });

  it('sends a listed key', () => {
    expect(destinationKeyToSend('audiobook', 'lib-kids', DESTINATIONS)).toBe('lib-kids');
  });

  it('never lists an unknown audiobook key', () => {
    expect(pickerDestinations('audiobook', 'lib-gone', DESTINATIONS)).toEqual(DESTINATIONS);
  });
});
```

Create `src/tests/reviewApproval.test.ts`:

```ts
import { describe, it, expect } from 'vitest';

import { reviewApproveOptions } from '../components/activity/reviewApproval';
import { buildFulfilAdminRequestBody } from '../services/requestApiHelpers';
import { destinationKeyToSend } from '../utils/downloadDestinations';

describe('reviewApproveOptions', () => {
  it('approving keeps the library pick', () => {
    expect(reviewApproveOptions('approve', 'grimmory:5:8')).toEqual({
      destinationKey: 'grimmory:5:8',
    });
  });

  it('browsing (before approve or for alternatives) keeps the library pick', () => {
    // The browse modal hides its own picker, so this is the only carrier.
    expect(reviewApproveOptions('browse', 'grimmory:5:8')).toEqual({
      browseOnly: true,
      destinationKey: 'grimmory:5:8',
    });
  });

  it('manual approval downloads nothing, so it carries no library', () => {
    expect(reviewApproveOptions('manual', 'grimmory:5:8')).toEqual({ manualApproval: true });
  });

  it('select → browse alternatives → fulfil sends the chosen library', () => {
    // Picked while the ebook list was still loading: the pick still travels.
    const options = reviewApproveOptions(
      'browse',
      destinationKeyToSend('ebook', 'grimmory:5:8', []),
    );

    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      destination_key: options.destinationKey,
    });

    expect(body.destination_key).toBe('grimmory:5:8');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/tests/downloadDestinations.test.ts src/tests/reviewApproval.test.ts`
Expected: FAIL — `Error: Cannot find module '../utils/downloadDestinations'` and `Error: Cannot find module '../components/activity/reviewApproval'` (both files fail to load).

- [ ] **Step 3: Implement**

```bash
git mv src/utils/audiobookDestinations.ts src/utils/downloadDestinations.ts
git mv src/hooks/useAudiobookDestinations.ts src/hooks/useDownloadDestinations.ts
```

Replace the whole content of **`src/utils/downloadDestinations.ts`** with:

```ts
import type { ContentType } from '../types';

/** One place an admin can route a download to (fork-only). */
export interface DownloadDestination {
  key: string;
  name: string;
}

/** `GET /api/download-destinations` for one content type. */
export interface DownloadDestinationList {
  destinations: DownloadDestination[];
  /** Name of the library a blank choice lands in, or '' when it cannot be known. */
  defaultName: string;
}

export const EMPTY_DESTINATION_LIST: DownloadDestinationList = {
  destinations: [],
  defaultName: '',
};

/**
 * Whether a library picker should be offered.
 *
 * Ebooks pick a Grimmory library, audiobooks an Audiobookshelf one. A single
 * destination is not a choice — the picker would be a control with one option —
 * unless an ebook already carries an explicit pick, which stays visible (and
 * clearable) whatever the list currently holds.
 */
export const shouldShowDestinationPicker = (
  contentType: string | null | undefined,
  destinations: DownloadDestination[],
  selectedKey?: string | null,
): boolean => {
  if (contentType !== 'ebook' && contentType !== 'audiobook') {
    return false;
  }
  if (destinations.length > 1) {
    return true;
  }
  return contentType === 'ebook' && Boolean((selectedKey ?? '').trim());
};

/**
 * Label for the picker's blank option. Ebooks always get the neutral label: a
 * fulfilled request runs as the requester, whose own settings decide the default.
 */
export const destinationDefaultLabel = (contentType: ContentType, defaultName: string): string => {
  const name = defaultName.trim();
  return name ? `Default (${name})` : `Default ${contentType} destination`;
};

/**
 * Pick the initially selected key, dropping one that no longer exists.
 *
 * An empty string means "no explicit choice", which routes to the default
 * destination rather than to a library that has since been removed.
 */
export const resolveDefaultDestinationKey = (
  currentKey: string | null | undefined,
  destinations: DownloadDestination[],
): string => {
  if (!currentKey) {
    return '';
  }
  return destinations.some((destination) => destination.key === currentKey) ? currentKey : '';
};

/**
 * The picker's current value.
 *
 * An explicit ebook pick is never blanked by the display list: the list may be
 * loading, empty while Grimmory is down, or stale, and the server verifies the
 * key against a fresh read and fails closed. Audiobooks keep the fallback of
 * `resolveDefaultDestinationKey`.
 */
export const resolveSelectedDestinationKey = (
  contentType: string | null | undefined,
  currentKey: string | null | undefined,
  destinations: DownloadDestination[],
): string => {
  if (contentType === 'ebook') {
    return (currentKey ?? '').trim();
  }
  return resolveDefaultDestinationKey(currentKey, destinations);
};

/**
 * The key a download or approval sends, or undefined for "use the default".
 *
 * Ebooks send any nonblank pick unchanged (see `resolveSelectedDestinationKey`);
 * audiobooks send only a listed key, and only while the picker is shown.
 */
export const destinationKeyToSend = (
  contentType: string | null | undefined,
  currentKey: string | null | undefined,
  destinations: DownloadDestination[],
): string | undefined => {
  if (contentType === 'ebook') {
    return resolveSelectedDestinationKey(contentType, currentKey, destinations) || undefined;
  }
  if (!shouldShowDestinationPicker(contentType, destinations)) {
    return undefined;
  }
  return resolveDefaultDestinationKey(currentKey, destinations) || undefined;
};

/** The picker's options: an ebook pick missing from the list is shown as its own option. */
export const pickerDestinations = (
  contentType: string | null | undefined,
  selectedKey: string,
  destinations: DownloadDestination[],
): DownloadDestination[] => {
  if (
    contentType !== 'ebook' ||
    !selectedKey ||
    destinations.some((destination) => destination.key === selectedKey)
  ) {
    return destinations;
  }
  return [...destinations, { key: selectedKey, name: `${selectedKey} (not in the current list)` }];
};

/**
 * Attach an admin's library choice to a direct-download payload.
 *
 * Copied rather than mutated, and omitted rather than blanked: '' is the
 * picker's own value for "use the default destination", so forwarding it would
 * put a key on the wire that means nothing. The server strips the key from a
 * non-admin's payload regardless, so this is presentation, not enforcement.
 */
export const withDestinationKey = <T extends object>(
  payload: T,
  destinationKey: string | null | undefined,
): T & { destination_key?: string } => {
  const key = (destinationKey ?? '').trim();
  return key ? { ...payload, destination_key: key } : { ...payload };
};

/**
 * Build a loader that fetches each content type's destinations once.
 *
 * Shared across every mounted picker: the lists change only when an admin
 * edits settings or Grimmory libraries. A failed lookup, and an empty list
 * (what the server answers while Grimmory is unreachable), are not cached —
 * the next caller retries. Both hide the picker instead of blocking the
 * download.
 */
export const createDestinationLoader = (
  fetchDestinations: (contentType: ContentType) => Promise<DownloadDestinationList>,
): ((contentType: ContentType) => Promise<DownloadDestinationList>) => {
  const cache = new Map<ContentType, Promise<DownloadDestinationList>>();
  return (contentType) => {
    let request = cache.get(contentType);
    if (!request) {
      request = fetchDestinations(contentType).then(
        (list) => {
          if (list.destinations.length === 0) {
            cache.delete(contentType);
          }
          return list;
        },
        () => {
          cache.delete(contentType);
          return EMPTY_DESTINATION_LIST;
        },
      );
      cache.set(contentType, request);
    }
    return request;
  };
};
```

Replace the whole content of **`src/hooks/useDownloadDestinations.ts`** with:

```ts
import { useState } from 'react';

import { getDownloadDestinations } from '../services/api';
import type { ContentType } from '../types';
import {
  createDestinationLoader,
  type DownloadDestinationList,
  EMPTY_DESTINATION_LIST,
} from '../utils/downloadDestinations';
import { useDependencyEffect } from './useMountEffect';

const loadDestinations = createDestinationLoader(getDownloadDestinations);

/**
 * The destinations an admin can route a download of `contentType` to.
 *
 * Pass `null` to skip the lookup (the viewer cannot choose). A list loaded for
 * the other format — combined mode just switched phase — is never returned.
 */
export const useDownloadDestinations = (
  contentType: ContentType | null,
): DownloadDestinationList => {
  const [loaded, setLoaded] = useState<{
    contentType: ContentType;
    list: DownloadDestinationList;
  } | null>(null);

  useDependencyEffect(() => {
    if (!contentType) {
      return undefined;
    }

    let cancelled = false;
    void loadDestinations(contentType).then((list) => {
      if (!cancelled) {
        setLoaded({ contentType, list });
      }
    });

    return () => {
      cancelled = true;
    };
  }, [contentType]);

  return loaded && loaded.contentType === contentType ? loaded.list : EMPTY_DESTINATION_LIST;
};
```

**`src/services/api.ts`** — four edits.

1. In the first `import type { … } from '../types';` block, add `ContentType,` after `Book,`.
2. Delete `import type { AudiobookDestination } from '../utils/audiobookDestinations';` and add, directly after the `} from '../utils/bookTransformers';` line that closes the `transformMetadataToBook` import:

```ts
import type { DownloadDestination, DownloadDestinationList } from '../utils/downloadDestinations';
```

3. Replace `getAudiobookDestinations`:

```ts
export const getAudiobookDestinations = async (): Promise<AudiobookDestination[]> => {
  const response = await fetchJSON<{ destinations?: AudiobookDestination[] }>(
    `${API_BASE}/audiobook-destinations`,
  );
  return response.destinations ?? [];
};
```

with:

```ts
export const getDownloadDestinations = async (
  contentType: ContentType,
): Promise<DownloadDestinationList> => {
  const response = await fetchJSON<{
    destinations?: DownloadDestination[];
    default_name?: string;
  }>(`${API_BASE}/download-destinations?content_type=${encodeURIComponent(contentType)}`);
  return { destinations: response.destinations ?? [], defaultName: response.default_name ?? '' };
};
```

4. In `DownloadReleasePayload`, replace:

```ts
  // Audiobook library chosen by an admin in the release modal. Absent unless
  // one was picked, and stripped server-side from a non-admin's payload.
  destination_key?: string;
```

with:

```ts
  // Library chosen by an admin in the release modal: an Audiobookshelf key for
  // an audiobook, `grimmory:<lib>:<path>` for an ebook. Absent unless one was
  // picked, and stripped server-side from a non-admin's payload.
  destination_key?: string;
```

**`src/utils/releasePayload.ts`** — replace `import { withDestinationKey } from './audiobookDestinations';` with `import { withDestinationKey } from './downloadDestinations';`, and replace the `destinationKey` doc comment:

```ts
  /**
   * Audiobookshelf library an admin picked for this download (fork-only). Omitted
   * from the payload entirely when blank, so the server keeps its own default.
   */
```

with:

```ts
  /**
   * Library an admin picked for this download (fork-only): Audiobookshelf for an
   * audiobook, Grimmory for an ebook. Omitted from the payload entirely when
   * blank, so the server keeps its own default.
   */
```

**`src/components/ReleaseModal.tsx`** — six edits.

1. Delete `import { useAudiobookDestinations } from '../hooks/useAudiobookDestinations';` and add, directly after `import { useBodyScrollLock } from '../hooks/useBodyScrollLock';`:

```ts
import { useDownloadDestinations } from '../hooks/useDownloadDestinations';
```

2. Delete:

```ts
import {
  resolveDefaultDestinationKey,
  shouldShowDestinationPicker,
} from '../utils/audiobookDestinations';
```

and add, directly after `import { coverObjectPositionClass, isSquareCover } from '../utils/coverAspect';`:

```ts
import {
  destinationDefaultLabel,
  destinationKeyToSend,
  pickerDestinations,
  resolveSelectedDestinationKey,
  shouldShowDestinationPicker,
} from '../utils/downloadDestinations';
```

3. In `ReleaseModalProps`, replace:

```ts
  // Whether this viewer may route an audiobook to a specific library. Admin-only
  // — the server strips the key from anyone else's download payload.
```

with:

```ts
  // Whether this viewer may route a download to a specific library (Grimmory for
  // ebooks, Audiobookshelf for audiobooks). Admin-only — the server strips the
  // key from anyone else's download payload.
```

4. Replace the destination block:

```ts
  // In combined mode `contentType` tracks the current phase, so the picker
  // appears on the audiobook step and stays out of the way on the ebook one.
  const destinations = useAudiobookDestinations(
    canChooseDestination && contentType === 'audiobook',
  );
  const showDestinationPicker =
    canChooseDestination && shouldShowDestinationPicker(contentType, destinations);
  // Drop a selection whose library disappeared from settings while the modal
  // was open, so a download can never carry a key that routes nowhere.
  const selectedDestinationKey = resolveDefaultDestinationKey(destinationKey, destinations);
  // Only a download consumes it; a release the admin can merely request goes
  // through the normal approve flow, which asks for the library separately.
  const chosenDestinationKey = showDestinationPicker
    ? selectedDestinationKey || undefined
    : undefined;
```

with:

```ts
  // In combined mode `contentType` tracks the current phase, so each step lists
  // the libraries for its own format.
  const { destinations, defaultName: destinationDefaultName } = useDownloadDestinations(
    canChooseDestination ? contentType : null,
  );
  const showDestinationPicker =
    canChooseDestination && shouldShowDestinationPicker(contentType, destinations, destinationKey);
  // An audiobook selection whose library disappeared while the modal was open is
  // dropped. An ebook pick is kept even while the list loads or lacks it: the
  // server verifies it fresh and fails closed rather than re-routing.
  const selectedDestinationKey = resolveSelectedDestinationKey(
    contentType,
    destinationKey,
    destinations,
  );
  const destinationOptions = pickerDestinations(contentType, selectedDestinationKey, destinations);
  // Only a download consumes it; a release the admin can merely request goes
  // through the normal approve flow, which asks for the library separately.
  const chosenDestinationKey = canChooseDestination
    ? destinationKeyToSend(contentType, destinationKey, destinations)
    : undefined;
```

(The combined Next/Back buttons from Task 8 pass `chosenDestinationKey`, so a restored ebook key now survives a phase switch even before that phase's list has loaded.)

5. Replace `{/* Library picker — where this audiobook lands once downloaded */}` with `{/* Library picker — where this download lands */}`.
6. In that picker, replace:

```tsx
                  <option value="">Default audiobook destination</option>
                  {destinations.map((destination) => (
```

with:

```tsx
                  <option value="">
                    {destinationDefaultLabel(contentType, destinationDefaultName)}
                  </option>
                  {destinationOptions.map((destination) => (
```

**`src/components/activity/reviewApproval.ts`** (new):

```ts
/** Options the approve panel hands to the request-approve handler. */
export interface RequestApproveOptions {
  browseOnly?: boolean;
  manualApproval?: boolean;
  destinationKey?: string;
}

/**
 * Options for one approve-panel action.
 *
 * Every action that ends in a download keeps the admin's library pick —
 * including browsing for an alternative release, whose modal hides its own
 * picker. Manual approval downloads nothing, so it carries none.
 */
export const reviewApproveOptions = (
  action: 'approve' | 'browse' | 'manual',
  destinationKey?: string,
): RequestApproveOptions => {
  if (action === 'manual') {
    return { manualApproval: true };
  }
  return action === 'browse' ? { browseOnly: true, destinationKey } : { destinationKey };
};
```

**`src/components/activity/ActivityCard.tsx`** — eight edits.

1. Replace `import { useAudiobookDestinations } from '../../hooks/useAudiobookDestinations';` with `import { useDownloadDestinations } from '../../hooks/useDownloadDestinations';`.
2. Delete:

```ts
import {
  resolveDefaultDestinationKey,
  shouldShowDestinationPicker,
} from '../../utils/audiobookDestinations';
```

and add, directly after `import { coverObjectPositionClass, isSquareCover } from '../../utils/coverAspect';`:

```ts
import {
  destinationDefaultLabel,
  destinationKeyToSend,
  pickerDestinations,
  resolveSelectedDestinationKey,
  shouldShowDestinationPicker,
} from '../../utils/downloadDestinations';
```

3. Replace the local options interface (and the blank line before it) —

```ts
import type { ActivityItem } from './activityTypes';

interface RequestApproveOptions {
  browseOnly?: boolean;
  manualApproval?: boolean;
  destinationKey?: string;
}
```

— with the shared one:

```ts
import type { ActivityItem } from './activityTypes';
import { type RequestApproveOptions, reviewApproveOptions } from './reviewApproval';
```

4. In `ReviewInlinePanel`, replace:

```ts
  const destinations = useAudiobookDestinations(reviewRecord.content_type === 'audiobook');
  const showDestinationPicker = shouldShowDestinationPicker(
    reviewRecord.content_type,
    destinations,
  );
  // Drop a selection whose library disappeared from settings mid-review, so an
  // approval can never carry a key that no longer routes anywhere.
  const selectedDestinationKey = resolveDefaultDestinationKey(destinationKey, destinations);
```

with:

```ts
  const { destinations, defaultName: destinationDefaultName } = useDownloadDestinations(
    reviewRecord.content_type,
  );
  const showDestinationPicker = shouldShowDestinationPicker(
    reviewRecord.content_type,
    destinations,
    destinationKey,
  );
  // An audiobook selection whose library disappeared mid-review is dropped; an
  // ebook pick is kept and verified by the server, which fails closed.
  const selectedDestinationKey = resolveSelectedDestinationKey(
    reviewRecord.content_type,
    destinationKey,
    destinations,
  );
  const approvalDestinationKey = destinationKeyToSend(
    reviewRecord.content_type,
    destinationKey,
    destinations,
  );
  const destinationOptions = pickerDestinations(
    reviewRecord.content_type,
    selectedDestinationKey,
    destinations,
  );
```

5. In `handleReviewApprove`, replace:

```ts
      if (requiresBrowseBeforeApprove) {
        await reviewApproveHandler(reviewRecord.id, reviewRecord, {
          browseOnly: true,
          destinationKey: selectedDestinationKey || undefined,
        });
        return;
      }

      await reviewApproveHandler(reviewRecord.id, reviewRecord, {
        destinationKey: selectedDestinationKey || undefined,
      });
```

with:

```ts
      await reviewApproveHandler(
        reviewRecord.id,
        reviewRecord,
        reviewApproveOptions(
          requiresBrowseBeforeApprove ? 'browse' : 'approve',
          approvalDestinationKey,
        ),
      );
```

6. In `handleReviewBrowseAlternatives` — the review finding: it sent only `{ browseOnly: true }`, so a library picked on the panel was lost on the browse detour (the browse modal hides its own picker) and the fulfilment used the default — replace:

```ts
      await reviewApproveHandler(reviewRecord.id, reviewRecord, { browseOnly: true });
```

with:

```ts
      await reviewApproveHandler(
        reviewRecord.id,
        reviewRecord,
        reviewApproveOptions('browse', approvalDestinationKey),
      );
```

7. In `handleReviewManualApproval`, replace:

```ts
      await reviewApproveHandler(reviewRecord.id, reviewRecord, { manualApproval: true });
```

with:

```ts
      await reviewApproveHandler(reviewRecord.id, reviewRecord, reviewApproveOptions('manual'));
```

8. In the picker, replace:

```tsx
            <option value="">Default audiobook destination</option>
            {destinations.map((destination) => (
```

with:

```tsx
            <option value="">
              {destinationDefaultLabel(reviewRecord.content_type, destinationDefaultName)}
            </option>
            {destinationOptions.map((destination) => (
```

(`App.handleRequestApprove` already stores `options.destinationKey` on `fulfillingRequest` for any browse, and `handleBrowseFulfilDownload` passes it to the fulfil call; `ReleaseModal` still hides its own picker in that mode.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/tests/downloadDestinations.test.ts src/tests/reviewApproval.test.ts && npm run test:unit`
Expected: PASS (31 tests in `downloadDestinations.test.ts`, 4 in `reviewApproval.test.ts`; whole suite green).

- [ ] **Step 5: Typecheck, lint, format, knip, commit**

```bash
npm run typecheck && npm run lint && npm run format && npm run format:check
npm run knip   # must list nothing beyond the findings `main` already has (src/types/settings.ts, src/services/api.ts Admin* types)
cd ../.. && git add -A src/frontend/src
git commit -m "feat(frontend): generic download destination picker for ebooks and audiobooks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 10: Every frontend payload builder carries the book identity

**Files:**
- Create: `src/frontend/src/utils/bookIdentity.ts`
- Modify: `src/frontend/src/services/api.ts` (`DownloadReleasePayload`), `src/frontend/src/utils/releasePayload.ts`, `src/frontend/src/utils/requestPayload.ts`, `src/frontend/src/utils/requestFulfil.ts`
- Test: `src/frontend/src/tests/releasePayload.test.ts`, `src/frontend/src/tests/requestPayload.test.ts`, `src/frontend/src/tests/requestFulfil.test.ts`

All paths below are relative to `src/frontend/`.

**Interfaces:**
- Consumes: `combinedLegOptions`, `completeCombinedSelection` (Task 8); `downloadRelease(payload, onBehalfOfUserId?)` from `services/api.ts`.
- Produces: `interface BookIdentityFields { provider?; provider_id?; isbn_13?; asin? }` and `bookIdentityFields(book: Book): BookIdentityFields` (`{}` for `provider === 'manual'`; `isbn_13: book.isbn_13 ?? book.isbn_10`). `DownloadReleasePayload` gains the four optional fields. `buildReleaseDownloadPayload` and `buildReleaseDataFromMetadataRelease` spread `bookIdentityFields(book)`; `buildMetadataBookRequestData` also stores `isbn_13` and `isbn_10`; `bookFromRequestData` keeps `isbn_13`, `isbn_10`, `asin`.

- [ ] **Step 1: Write the failing tests**

**`src/tests/releasePayload.test.ts`** — replace the imports:

```ts
import { describe, it, expect } from 'vitest';

import type { Book, Release } from '../types';
import { buildReleaseDownloadPayload } from '../utils/releasePayload';
```

with:

```ts
import { describe, it, expect, vi, afterEach } from 'vitest';

import { downloadRelease } from '../services/api';
import type { Book, Release } from '../types';
import { combinedLegOptions, completeCombinedSelection } from '../utils/combinedSelection';
import { buildReleaseDownloadPayload } from '../utils/releasePayload';
```

and append to the end of the file:

```ts
// Fork-only: the book identity a post-upload hook tags the book with.
describe('buildReleaseDownloadPayload book identity', () => {
  const identified: Book = {
    ...book,
    provider: 'hardcover',
    provider_id: '886465',
    isbn_13: '9780316005142',
    isbn_10: '0316005142',
    asin: 'B0BSHZ1234',
  };
  const ebookRelease: Release = { source: 'prowlarr', source_id: 'ebook-1', title: 'Drive.epub' };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('carries provider, provider id, ISBN and ASIN from the book', () => {
    expect(buildReleaseDownloadPayload(identified, ebookRelease, 'ebook')).toMatchObject({
      provider: 'hardcover',
      provider_id: '886465',
      isbn_13: '9780316005142',
      asin: 'B0BSHZ1234',
    });
  });

  it('sends the ISBN-10 when the book has no ISBN-13', () => {
    const isbn10Only: Book = { ...identified, isbn_13: undefined };

    expect(buildReleaseDownloadPayload(isbn10Only, ebookRelease, 'ebook').isbn_13).toBe(
      '0316005142',
    );
  });

  it('sends no identity for a manual book', () => {
    const manual: Book = { ...identified, provider: 'manual' };
    const payload = buildReleaseDownloadPayload(manual, ebookRelease, 'ebook');

    for (const field of ['provider', 'provider_id', 'isbn_13', 'asin'] as const) {
      expect(payload[field]).toBeUndefined();
    }
  });

  it('carries identity and the ebook library on a combined-mode leg', () => {
    const state = completeCombinedSelection(
      { phase: 'ebook', ebookMode: 'download', audiobookMode: 'request_book' },
      identified,
      ebookRelease,
      'grimmory:5:8',
    );

    const payload = buildReleaseDownloadPayload(
      identified,
      ebookRelease,
      'ebook',
      combinedLegOptions(state, 'ebook'),
    );

    expect(payload).toMatchObject({
      provider: 'hardcover',
      provider_id: '886465',
      destination_key: 'grimmory:5:8',
    });
  });

  it('keeps identity and the library on an on-behalf download', async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response('{"status":"queued"}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await downloadRelease(
      buildReleaseDownloadPayload(identified, ebookRelease, 'ebook', {
        destinationKey: 'grimmory:5:8',
      }),
      42,
    );

    const sent = fetchMock.mock.calls[0][1]?.body;
    const body: unknown = typeof sent === 'string' ? JSON.parse(sent) : null;
    expect(body).toMatchObject({
      on_behalf_of_user_id: 42,
      provider: 'hardcover',
      provider_id: '886465',
      isbn_13: '9780316005142',
      asin: 'B0BSHZ1234',
      destination_key: 'grimmory:5:8',
    });
  });
});
```

**`src/tests/requestPayload.test.ts`** — insert directly above `  it('resolves browse source from source-backed or provider-backed books', () => {`:

```ts
  // Fork-only: identity a post-upload hook tags the book with.
  it('stores both ISBNs in the request book data', () => {
    const bookData = buildMetadataBookRequestData(
      { ...baseBook, isbn_13: '9780316005142', isbn_10: '0316005142' },
      'ebook',
    );

    expect(bookData.isbn_13).toBe('9780316005142');
    expect(bookData.isbn_10).toBe('0316005142');
  });

  it('puts the book identity on release request data', () => {
    const releaseData = buildReleaseDataFromMetadataRelease(
      {
        ...baseBook,
        provider: 'hardcover',
        provider_id: '886465',
        isbn_13: '9780316005142',
        asin: 'B0X',
      },
      baseRelease,
      'ebook',
    );

    expect(releaseData).toMatchObject({
      provider: 'hardcover',
      provider_id: '886465',
      isbn_13: '9780316005142',
      asin: 'B0X',
    });
  });

  it('puts no identity on release request data for a manual book', () => {
    const releaseData = buildReleaseDataFromMetadataRelease(
      { ...baseBook, provider: 'manual', provider_id: 'manual-1', isbn_13: '9780316005142' },
      baseRelease,
      'ebook',
    );

    expect(releaseData.provider).toBeUndefined();
    expect(releaseData.provider_id).toBeUndefined();
    expect(releaseData.isbn_13).toBeUndefined();
  });
```

**`src/tests/requestFulfil.test.ts`** — replace the import line `import { bookFromRequestData } from '../utils/requestFulfil';` with:

```ts
import type { Book, Release } from '../types';
import { bookFromRequestData } from '../utils/requestFulfil';
import {
  buildMetadataBookRequestData,
  buildReleaseDataFromMetadataRelease,
} from '../utils/requestPayload';
```

and insert these tests inside the `describe('requestFulfil.bookFromRequestData', …)` block, just before its closing `});` (after `provides safe fallbacks when request payload fields are missing`):

```ts
  // Fork-only: browse-before-approve rebuilds the Book from the request, and the
  // release it then builds must still carry the identity the requester saw.
  it('keeps the ASIN and both ISBNs', () => {
    const book = bookFromRequestData({
      title: 'Overlord',
      asin: 'B0BSHZ1234',
      isbn_13: '9780316005142',
      isbn_10: '0316005142',
    });

    expect(book.asin).toBe('B0BSHZ1234');
    expect(book.isbn_13).toBe('9780316005142');
    expect(book.isbn_10).toBe('0316005142');
  });

  it('round-trips identity from request to approved release', () => {
    const requested: Book = {
      id: '886465',
      title: 'Overlord',
      author: 'Kugane Maruyama',
      provider: 'hardcover',
      provider_id: '886465',
      isbn_13: '9780316005142',
      asin: 'B0BSHZ1234',
    };
    const release: Release = { source: 'prowlarr', source_id: 'r-1', title: 'Overlord.epub' };

    const rebuilt = bookFromRequestData(buildMetadataBookRequestData(requested, 'ebook'));
    const releaseData = buildReleaseDataFromMetadataRelease(rebuilt, release, 'ebook');

    expect(releaseData).toMatchObject({
      provider: 'hardcover',
      provider_id: '886465',
      isbn_13: '9780316005142',
      asin: 'B0BSHZ1234',
    });
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/tests/releasePayload.test.ts src/tests/requestPayload.test.ts src/tests/requestFulfil.test.ts`
Expected: FAIL — 8 tests fail on missing identity (e.g. `expected { …(17) } to match object { provider: 'hardcover', provider_id: '886465', … }`, `expected undefined to be '9780316005142'`); the two "manual book" tests pass already.

- [ ] **Step 3: Implement**

**`src/utils/bookIdentity.ts`** (new):

```ts
import type { Book } from '../types';

/**
 * The metadata identity a download carries (fork-only), so a post-upload hook
 * can tag the book it lands as. The server pairs provider/provider_id, and
 * canonicalizes the ISBN (an ISBN-10 becomes ISBN-13).
 */
export interface BookIdentityFields {
  provider?: string;
  provider_id?: string;
  isbn_13?: string;
  asin?: string;
}

/** Identity fields for a metadata book; a manual book has none. */
export const bookIdentityFields = (book: Book): BookIdentityFields => {
  if (book.provider === 'manual') {
    return {};
  }
  return {
    provider: book.provider,
    provider_id: book.provider_id,
    isbn_13: book.isbn_13 ?? book.isbn_10,
    asin: book.asin,
  };
};
```

**`src/services/api.ts`** — in `DownloadReleasePayload`, replace:

```ts
  search_author?: string;
  search_mode?: 'direct' | 'universal';
```

with:

```ts
  search_author?: string;
  search_mode?: 'direct' | 'universal';
  // Metadata identity of the book (fork-only), for the post-upload tagging hook.
  provider?: string;
  provider_id?: string;
  isbn_13?: string;
  asin?: string;
```

**`src/utils/releasePayload.ts`** — add `import { bookIdentityFields } from './bookIdentity';` directly after `import type { Book, ContentType, PackBook, Release } from '../types';`, and replace:

```ts
      language: release.language ?? undefined,
    },
    options.destinationKey,
```

with:

```ts
      language: release.language ?? undefined,
      ...bookIdentityFields(book),
    },
    options.destinationKey,
```

**`src/utils/requestPayload.ts`** — add `import { bookIdentityFields } from './bookIdentity';` directly after `import type { Book, ContentType, CreateRequestPayload, Release } from '../types';`. In `buildMetadataBookRequestData`, replace:

```ts
    asin: book.asin,
    year: book.year,
```

with:

```ts
    asin: book.asin,
    // Fork-only: kept so an approved request can still tell the tagging hook
    // which edition was asked for.
    isbn_13: book.isbn_13,
    isbn_10: book.isbn_10,
    year: book.year,
```

In `buildReleaseDataFromMetadataRelease`, replace:

```ts
    language: release.language,
    ...(isSourceBackedReleaseContext ? { search_mode: 'direct' as const } : {}),
```

with:

```ts
    language: release.language,
    ...bookIdentityFields(book),
    ...(isSourceBackedReleaseContext ? { search_mode: 'direct' as const } : {}),
```

**`src/utils/requestFulfil.ts`** — in `bookFromRequestData`, replace:

```ts
    provider_id: providerId,
    preview: toOptionalText(row.preview),
```

with:

```ts
    provider_id: providerId,
    // Fork-only: the identity the release built from this book carries on.
    isbn_13: toOptionalText(row.isbn_13),
    isbn_10: toOptionalText(row.isbn_10),
    asin: toOptionalText(row.asin),
    preview: toOptionalText(row.preview),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/tests/releasePayload.test.ts src/tests/requestPayload.test.ts src/tests/requestFulfil.test.ts && npm run test:unit`
Expected: PASS (10 new tests across the three files; whole suite green).

- [ ] **Step 5: Typecheck, lint, format, knip, commit**

```bash
npm run typecheck && npm run lint && npm run format && npm run format:check
npm run knip   # nothing beyond the findings `main` already has
cd ../.. && git add src/frontend/src
git commit -m "feat(frontend): carry book identity in every download and request payload

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: Full gates

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything above.
- Produces: a green branch ready for review and `scripts/release-local.sh`.

- [ ] **Step 1: Backend gates**

```bash
uv run pytest tests/ -q -m "not integration and not e2e"
uv run ruff check shelfmark tests
uv run ruff format --check shelfmark tests
uv run basedpyright
uv run vulture shelfmark
```

Expected: pytest reports only the 9 known failures in `tests/config/test_entrypoint_permissions.py` (macOS; none elsewhere — about 106 more passing tests than `main`: 3591 passed on the dry run); ruff clean; BasedPyright shows only the 4 known errors at `shelfmark/main.py:2305-2308`; vulture prints nothing.

- [ ] **Step 2: Frontend gates**

```bash
cd src/frontend
npm run typecheck && npm run lint && npm run format:check && npm run test:unit
npm run knip
```

Expected: typecheck, lint, format clean; vitest all green (about 38 more tests than `main`: 357 on the dry run); knip lists exactly the findings `main` already has.

- [ ] **Step 3: Contract spot-check against Plan B**

```bash
grep -n '"uploaded_files"\|"upload_started_at"\|"upload_finished_at"' shelfmark/download/outputs/booklore.py
grep -n '"provider"\|"provider_id"\|"isbn_13"\|"asin"' shelfmark/download/postprocess/custom_script.py
grep -rn "audiobook-destinations\|useAudiobookDestinations\|audiobookDestinations" shelfmark src/frontend/src
```

Expected: the first two each print their lines (field names exactly as in Global Constraints, `"version": 1` untouched); the third prints nothing.

- [ ] **Step 4: Repo-level make targets (as CI runs them)**

Run: `make python-checks python-test frontend-checks frontend-test`
Expected: on macOS `python-test` stops at the known `tests/config/test_entrypoint_permissions.py` failures (it runs with `-x`); everything else passes. On Linux CI all four pass.
