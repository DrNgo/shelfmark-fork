# Grimmory Library Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index Grimmory's ebook library so Shelfmark flags results you already own, and make the existing in-library badge format-aware so ebook and audiobook holdings stop being conflated.

**Architecture:** Extract the source-agnostic half of the Audiobookshelf integration into a shared `shelfmark/library/` core — one SQLite index keyed by `(source, item_id)`, one scheduler, one lookup — and reduce each integration to a provider that answers "fetch your items". Grimmory joins as a second provider reading `GET /api/v1/books/page`. Matching gains ISBN as a third exact key alongside title+author and ASIN.

**Tech Stack:** Python 3.14, Flask, SQLite (stdlib `sqlite3`), `requests`, pytest; React 19 + TypeScript + Vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-08-02-grimmory-library-index-design.md`

## Global Constraints

- Config keys keep the `BOOKLORE_` prefix; only user-facing labels say "Grimmory". No key is renamed.
- Grimmory display name is the constant `BOOKLORE_DISPLAY_NAME = "Grimmory"` (`shelfmark/download/outputs/booklore.py:49`). Use it in user-facing strings, never a literal.
- The upload path must keep working untouched: `build_booklore_config` reads credentials directly and must never consult `BOOKLORE_ENABLED`.
- Matching is exact after normalization — no scoring, no edit distance. A missed match costs a badge; a false match talks a user out of a book they do not own. When in doubt, reject.
- A sync failure records why and leaves the previous index in place. Nothing in the sync path raises at the caller.
- `media_type` values are exactly `'ebook'` and `'audiobook'`. `source` values are exactly `'audiobookshelf'` and `'grimmory'`.
- Python: `ruff check` and `ruff format` must pass. Frontend: `npm run typecheck`, `npm run lint`, `npm run test:unit` must pass.
- Run Python tests with `pytest`; run frontend tests from `src/frontend/` with `npm run test:unit`.

## File Structure

**Created**

| Path | Responsibility |
|---|---|
| `shelfmark/library/__init__.py` | Package marker + public re-exports |
| `shelfmark/library/matching.py` | Normalization and match-key construction (moved, + ISBN) |
| `shelfmark/library/index.py` | `LibraryIndexDB` — source-scoped SQLite cache (moved, + columns) |
| `shelfmark/library/lookup.py` | Format-aware batch lookup (moved, + format split) |
| `shelfmark/library/scheduler.py` | Staleness, per-source sync, background loop (moved) |
| `shelfmark/library/routes.py` | `POST /api/library-matches` |
| `shelfmark/library/providers/__init__.py` | `LibraryProvider` protocol + registry |
| `shelfmark/library/providers/audiobookshelf.py` | ABS item extraction (moved) |
| `shelfmark/library/providers/grimmory.py` | Grimmory item extraction |
| `shelfmark/grimmory/__init__.py` | Package marker |
| `shelfmark/grimmory/client.py` | Grimmory connection primitives + `list_books` |
| `shelfmark/grimmory/settings.py` | Grimmory settings tab |
| `tests/library/…` | Tests for the shared core (some moved from `tests/audiobookshelf/`) |

**Deleted after their contents move**

`shelfmark/audiobookshelf/matching.py`, `library_index.py`, `library_lookup.py`, `library_sync.py`.

**Modified**

`shelfmark/audiobookshelf/routes.py`, `shelfmark/audiobookshelf/settings.py`, `shelfmark/download/outputs/booklore.py`, `shelfmark/config/booklore_settings.py`, `shelfmark/config/settings.py`, `shelfmark/core/settings_registry.py`, `shelfmark/main.py`, and the frontend files listed in Tasks 11–12.

---

### Task 1: Move matching to the shared core

Pure file move, no behaviour change. Keeping it separate from the ISBN work means a reviewer can diff Task 2 without a rename obscuring it.

**Files:**
- Move: `shelfmark/audiobookshelf/matching.py` → `shelfmark/library/matching.py`
- Move: `tests/audiobookshelf/test_matching.py` → `tests/library/test_matching.py`
- Create: `shelfmark/library/__init__.py`, `shelfmark/library/providers/__init__.py`, `tests/library/__init__.py`
- Modify: `shelfmark/audiobookshelf/library_index.py:18`, `shelfmark/audiobookshelf/library_lookup.py:16`

**Interfaces:**
- Consumes: nothing.
- Produces: `shelfmark.library.matching` exporting `build_match_keys`, `normalize_title`, `normalize_author`, `title_match_keys`, `author_match_keys`, `normalize_asin`, `asin_match_key`, `KEY_SEPARATOR`, `ASIN_KEY_PREFIX` — all unchanged signatures.

- [ ] **Step 1: Create the package directories**

```bash
mkdir -p shelfmark/library/providers tests/library
printf '"""Shared library index: what your connected libraries already hold."""\n' > shelfmark/library/__init__.py
printf '"""Per-source providers feeding the shared library index."""\n' > shelfmark/library/providers/__init__.py
printf '"""Tests for the shared library index."""\n' > tests/library/__init__.py
```

- [ ] **Step 2: Move the module and its tests**

```bash
git mv shelfmark/audiobookshelf/matching.py shelfmark/library/matching.py
git mv tests/audiobookshelf/test_matching.py tests/library/test_matching.py
```

- [ ] **Step 3: Update the importers — two production modules and the moved test**

In `shelfmark/audiobookshelf/library_index.py`, `shelfmark/audiobookshelf/library_lookup.py`, and `tests/library/test_matching.py`, replace `shelfmark.audiobookshelf.matching` with `shelfmark.library.matching`.

```bash
grep -rl "shelfmark.audiobookshelf.matching" shelfmark tests \
  | xargs sed -i '' 's/shelfmark\.audiobookshelf\.matching/shelfmark.library.matching/g'
```

- [ ] **Step 4: Verify nothing still points at the old path**

Run: `grep -rn "audiobookshelf.matching" shelfmark tests`
Expected: no output.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/library tests/audiobookshelf -q`
Expected: PASS, same count as before the move.

- [ ] **Step 6: Commit**

```bash
git add -A shelfmark/library shelfmark/audiobookshelf tests/library tests/audiobookshelf
git commit -m "refactor: move matching into a shared library package"
```

---

### Task 2: Add ISBN match keys

**Files:**
- Modify: `shelfmark/library/matching.py`
- Test: `tests/library/test_matching.py`

**Interfaces:**
- Consumes: `shelfmark.library.matching` from Task 1.
- Produces:
  - `ISBN_KEY_PREFIX: str = "isbn:"`
  - `normalize_isbn(value: object) -> str` — returns a 13-digit ISBN-13 or `""`
  - `isbn_match_key(value: object) -> str` — returns `"isbn:<13 digits>"` or `""`
  - `build_match_keys(title, author, subtitle=None, asin=None, isbn=None) -> set[str]` — `isbn` is new and keyword-optional, so every existing caller keeps working.

- [ ] **Step 1: Write the failing tests**

Append to `tests/library/test_matching.py`:

```python
from shelfmark.library.matching import ISBN_KEY_PREFIX, isbn_match_key, normalize_isbn


class TestNormalizeIsbn:
    def test_keeps_a_valid_isbn13(self):
        assert normalize_isbn("9780593135204") == "9780593135204"

    def test_strips_hyphens_and_spaces(self):
        assert normalize_isbn("978-0-593-13520-4") == "9780593135204"
        assert normalize_isbn("  9780593135204 ") == "9780593135204"

    def test_converts_isbn10_to_isbn13(self):
        assert normalize_isbn("0306406152") == "9780306406157"

    def test_accepts_an_x_check_digit(self):
        assert normalize_isbn("080442957X") == "9780804429573"

    def test_rejects_a_bad_isbn13_check_digit(self):
        assert normalize_isbn("9780593135205") == ""

    def test_rejects_a_bad_isbn10_check_digit(self):
        assert normalize_isbn("0306406153") == ""

    def test_passes_through_a_979_isbn13(self):
        # 979 ISBNs have no ISBN-10 equivalent and must survive untouched.
        assert normalize_isbn("9791234567896") == "9791234567896"

    def test_rejects_junk(self):
        for junk in ["", "N/A", "none", "97805931352", "97805931352049", None, 12345]:
            assert normalize_isbn(junk) == ""

    def test_rejects_zero_filled_placeholders(self):
        # Both zero-filled forms pass their own check-digit arithmetic, so they
        # need rejecting by hand or every placeholder row matches every other.
        assert normalize_isbn("0000000000") == ""
        assert normalize_isbn("0000000000000") == ""


class TestIsbnMatchKey:
    def test_namespaces_the_key(self):
        assert isbn_match_key("0306406152") == f"{ISBN_KEY_PREFIX}9780306406157"

    def test_returns_empty_for_junk(self):
        assert isbn_match_key("N/A") == ""


class TestBuildMatchKeysWithIsbn:
    def test_an_isbn_is_enough_on_its_own(self):
        keys = build_match_keys(None, None, isbn="9780593135204")
        assert keys == {f"{ISBN_KEY_PREFIX}9780593135204"}

    def test_isbn10_and_isbn13_produce_the_same_key(self):
        assert build_match_keys(None, None, isbn="0306406152") == build_match_keys(
            None, None, isbn="9780306406157"
        )

    def test_isbn_adds_to_title_author_keys_rather_than_replacing_them(self):
        keys = build_match_keys("The Housemaid", "Freida McFadden", isbn="9780593135204")
        assert f"{ISBN_KEY_PREFIX}9780593135204" in keys
        assert any(KEY_SEPARATOR in key for key in keys)

    def test_a_bad_isbn_contributes_no_key(self):
        assert build_match_keys(None, None, isbn="9780593135205") == set()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/library/test_matching.py -q`
Expected: FAIL with `ImportError: cannot import name 'ISBN_KEY_PREFIX'`

- [ ] **Step 3: Implement**

Add to `shelfmark/library/matching.py`, after the ASIN block:

```python
# ISBNs are edition-specific — paperback, hardcover and ebook each get their
# own — so a hit is a strong yes while a miss proves nothing. Title+author
# keeps carrying the general case, exactly as it does around ASIN.
_ISBN_SEPARATORS = re.compile(r"[-\s]")
_ISBN10_SHAPE = re.compile(r"^[0-9]{9}[0-9X]$")
_ISBN13_SHAPE = re.compile(r"^[0-9]{13}$")
ISBN_KEY_PREFIX = "isbn:"


def _isbn13_check_digit(body: str) -> str:
    """Return the check digit for the first twelve digits of an ISBN-13."""
    total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(body[:12]))
    return str((10 - total % 10) % 10)


def _isbn13_is_valid(candidate: str) -> bool:
    return _isbn13_check_digit(candidate) == candidate[12]


def _isbn10_is_valid(candidate: str) -> bool:
    total = sum(
        (10 if char == "X" else int(char)) * (10 - index) for index, char in enumerate(candidate)
    )
    return total % 11 == 0


def normalize_isbn(value: object) -> str:
    """Normalize an ISBN to its ISBN-13 form, returning "" for anything unusable.

    ISBN-10 is converted rather than stored alongside: the mapping is lossless
    and deterministic, so canonicalizing means an ISBN-10 on one side and an
    ISBN-13 on the other still meet. Check digits are verified because metadata
    fields routinely carry "N/A" or zero-filled placeholders, and an exact match
    on junk is still an exact match.
    """
    if not isinstance(value, str):
        return ""

    candidate = _ISBN_SEPARATORS.sub("", value).upper()

    # Zero-filled placeholders satisfy their own check-digit arithmetic, so they
    # have to be turned away by hand.
    if not candidate or set(candidate) == {"0"}:
        return ""

    if _ISBN13_SHAPE.match(candidate):
        return candidate if _isbn13_is_valid(candidate) else ""

    if _ISBN10_SHAPE.match(candidate):
        if not _isbn10_is_valid(candidate):
            return ""
        body = f"978{candidate[:9]}"
        return f"{body}{_isbn13_check_digit(body)}"

    return ""


def isbn_match_key(value: object) -> str:
    """Build the namespaced key for an ISBN, or "" if it is unusable."""
    normalized = normalize_isbn(value)
    return f"{ISBN_KEY_PREFIX}{normalized}" if normalized else ""
```

Then extend `build_match_keys` — replace the existing definition with:

```python
def build_match_keys(
    title: str | None,
    author: str | None,
    subtitle: str | None = None,
    asin: object = None,
    isbn: object = None,
) -> set[str]:
    """Build the match keys for one book.

    Title keys are every title variant × every author variant; without both
    halves none are emitted, since half a key would match every other half-key.
    A valid ASIN or ISBN adds one more key on top, and either is enough on its
    own — both are complete identities where a bare title is not.
    """
    keys: set[str] = set()

    asin_key = asin_match_key(asin)
    if asin_key:
        keys.add(asin_key)

    isbn_key = isbn_match_key(isbn)
    if isbn_key:
        keys.add(isbn_key)

    titles = title_match_keys(title, subtitle)
    authors = author_match_keys(author)
    if titles and authors:
        keys.update(f"{t}{KEY_SEPARATOR}{a}" for t in titles for a in authors)

    return keys
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/library/test_matching.py -q && ruff check shelfmark/library && ruff format --check shelfmark/library`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add shelfmark/library/matching.py tests/library/test_matching.py
git commit -m "feat: match library items by ISBN as well as ASIN and title+author"
```

---

### Task 3: Make the index source-aware

**Files:**
- Move: `shelfmark/audiobookshelf/library_index.py` → `shelfmark/library/index.py`
- Move: `tests/audiobookshelf/test_library_index.py` → `tests/library/test_index.py`
- Modify: `shelfmark/audiobookshelf/library_lookup.py`, `shelfmark/audiobookshelf/library_sync.py` (import paths only)

**Interfaces:**
- Consumes: `build_match_keys` from Task 2.
- Produces, from `shelfmark.library.index`:
  - `SOURCE_AUDIOBOOKSHELF = "audiobookshelf"`, `SOURCE_GRIMMORY = "grimmory"`
  - `MEDIA_TYPE_EBOOK = "ebook"`, `MEDIA_TYPE_AUDIOBOOK = "audiobook"`
  - `LibraryItem(source, item_id, library_id, library_name, media_type, title, subtitle, author, asin, isbn13)` — frozen dataclass, all `str`
  - `LibraryMatch(source, item_id, library_id, library_name, media_type, title, author, asin, isbn13)` — frozen dataclass, all `str`
  - `IndexState(last_sync_at: str | None, last_error: str | None, item_count: int)` — unchanged
  - `LibraryIndexDB.replace_items(source: str, items: list[LibraryItem]) -> int`
  - `LibraryIndexDB.find_matches(match_keys: set[str]) -> list[LibraryMatch]`
  - `LibraryIndexDB.record_failure(source: str, message: str) -> None`
  - `LibraryIndexDB.get_state(source: str) -> IndexState`
  - `get_library_index() -> LibraryIndexDB`, `get_index_db_path(config_dir: str | None = None) -> str`

- [ ] **Step 1: Move the files**

```bash
git mv shelfmark/audiobookshelf/library_index.py shelfmark/library/index.py
git mv tests/audiobookshelf/test_library_index.py tests/library/test_index.py
grep -rl "shelfmark.audiobookshelf.library_index" shelfmark tests \
  | xargs sed -i '' 's/shelfmark\.audiobookshelf\.library_index/shelfmark.library.index/g'
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/library/test_index.py`:

```python
from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_EBOOK,
    SOURCE_AUDIOBOOKSHELF,
    SOURCE_GRIMMORY,
)


def _sourced_item(source, item_id, media_type, title="The Housemaid", **overrides):
    fields = {
        "source": source,
        "item_id": item_id,
        "library_id": "lib_1",
        "library_name": "Library",
        "media_type": media_type,
        "title": title,
        "subtitle": "",
        "author": "Freida McFadden",
        "asin": "",
        "isbn13": "",
    }
    fields.update(overrides)
    return LibraryItem(**fields)


class TestSourceScoping:
    def test_replacing_one_source_leaves_the_other_intact(self, index):
        abs_item = _sourced_item(SOURCE_AUDIOBOOKSHELF, "abs_1", MEDIA_TYPE_AUDIOBOOK)
        grim_item = _sourced_item(SOURCE_GRIMMORY, "1", MEDIA_TYPE_EBOOK)
        index.replace_items(SOURCE_AUDIOBOOKSHELF, [abs_item])
        index.replace_items(SOURCE_GRIMMORY, [grim_item])

        index.replace_items(SOURCE_GRIMMORY, [])

        keys = build_match_keys("The Housemaid", "Freida McFadden")
        sources = {match.source for match in index.find_matches(keys)}
        assert sources == {SOURCE_AUDIOBOOKSHELF}

    def test_the_same_item_id_in_two_sources_does_not_collide(self, index):
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, [_sourced_item(SOURCE_AUDIOBOOKSHELF, "1", MEDIA_TYPE_AUDIOBOOK)]
        )
        index.replace_items(
            SOURCE_GRIMMORY, [_sourced_item(SOURCE_GRIMMORY, "1", MEDIA_TYPE_EBOOK)]
        )

        matches = index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))
        assert len(matches) == 2
        assert {m.media_type for m in matches} == {MEDIA_TYPE_EBOOK, MEDIA_TYPE_AUDIOBOOK}

    def test_indexes_an_item_by_its_isbn(self, index):
        item = _sourced_item(SOURCE_GRIMMORY, "1", MEDIA_TYPE_EBOOK, isbn13="9780593135204")
        index.replace_items(SOURCE_GRIMMORY, [item])

        matches = index.find_matches(build_match_keys(None, None, isbn="0593135202"))
        assert [m.item_id for m in matches] == ["1"]


class TestPerSourceState:
    def test_state_is_tracked_per_source(self, index):
        index.replace_items(SOURCE_GRIMMORY, [_sourced_item(SOURCE_GRIMMORY, "1", MEDIA_TYPE_EBOOK)])

        assert index.get_state(SOURCE_GRIMMORY).last_sync_at is not None
        assert index.get_state(SOURCE_AUDIOBOOKSHELF).last_sync_at is None

    def test_a_failure_in_one_source_does_not_mark_the_other(self, index):
        index.record_failure(SOURCE_GRIMMORY, "Grimmory is down")

        assert index.get_state(SOURCE_GRIMMORY).last_error == "Grimmory is down"
        assert index.get_state(SOURCE_AUDIOBOOKSHELF).last_error is None

    def test_item_count_is_scoped_to_the_source(self, index):
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF,
            [
                _sourced_item(SOURCE_AUDIOBOOKSHELF, "a", MEDIA_TYPE_AUDIOBOOK, title="One Title"),
                _sourced_item(SOURCE_AUDIOBOOKSHELF, "b", MEDIA_TYPE_AUDIOBOOK, title="Two Title"),
            ],
        )
        index.replace_items(SOURCE_GRIMMORY, [_sourced_item(SOURCE_GRIMMORY, "1", MEDIA_TYPE_EBOOK)])

        assert index.get_state(SOURCE_AUDIOBOOKSHELF).item_count == 2
        assert index.get_state(SOURCE_GRIMMORY).item_count == 1
```

Update every pre-existing test in the file to the new signatures: `_item(...)` gains `source=SOURCE_AUDIOBOOKSHELF`, `media_type=MEDIA_TYPE_AUDIOBOOK` and `isbn13=""`; `replace_items(items)` becomes `replace_items(SOURCE_AUDIOBOOKSHELF, items)`; `get_state()` becomes `get_state(SOURCE_AUDIOBOOKSHELF)`; `record_failure(msg)` becomes `record_failure(SOURCE_AUDIOBOOKSHELF, msg)`. Their assertions must not change — they are the regression net for this refactor.

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/library/test_index.py -q`
Expected: FAIL with `ImportError: cannot import name 'SOURCE_GRIMMORY'`

- [ ] **Step 4: Rewrite the schema and dataclasses**

In `shelfmark/library/index.py`, replace `_CREATE_TABLES_SQL` and the two dataclasses:

```python
SOURCE_AUDIOBOOKSHELF = "audiobookshelf"
SOURCE_GRIMMORY = "grimmory"

MEDIA_TYPE_EBOOK = "ebook"
MEDIA_TYPE_AUDIOBOOK = "audiobook"

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS library_items (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    library_id TEXT NOT NULL,
    library_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    author TEXT NOT NULL,
    asin TEXT,
    isbn13 TEXT,
    PRIMARY KEY (source, item_id)
);

CREATE TABLE IF NOT EXISTS library_item_keys (
    match_key TEXT NOT NULL,
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (match_key, source, item_id)
);

CREATE INDEX IF NOT EXISTS idx_library_item_keys_key ON library_item_keys (match_key);

CREATE TABLE IF NOT EXISTS index_state (
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (source, key)
);
"""


@dataclass(frozen=True)
class LibraryItem:
    """One indexed item, flattened to the fields matching needs."""

    source: str
    item_id: str
    library_id: str
    library_name: str
    media_type: str
    title: str
    subtitle: str
    author: str
    asin: str
    isbn13: str


@dataclass(frozen=True)
class LibraryMatch:
    """An indexed item that matched a book, with enough detail to name it."""

    source: str
    item_id: str
    library_id: str
    library_name: str
    media_type: str
    title: str
    author: str
    asin: str
    isbn13: str
```

Change `get_index_db_path` to return `library_index.db`, and have `initialize()` remove the superseded file:

```python
def get_index_db_path(config_dir: str | None = None) -> str:
    """Return the path of the library index database."""
    root = config_dir or os.environ.get("CONFIG_DIR", "/config")
    return str(Path(root) / "library_index.db")


_LEGACY_DB_NAME = "audiobookshelf_index.db"
```

In `initialize()`, after the `executescript` block:

```python
        self._remove_legacy_index()

    def _remove_legacy_index(self) -> None:
        """Delete the superseded single-source index file, best effort.

        The index is a cache that rebuilds on the next sync, so the old file is
        dead weight rather than data. Failing to remove it must never stop the
        new index coming up.

        The -wal and -shm sidecars go too: the old index ran in WAL mode, so
        removing only the main file would leave two orphans behind and defeat
        the point of cleaning up at all.
        """
        base = Path(self._db_path).with_name(_LEGACY_DB_NAME)
        for legacy in (base, base.with_name(f"{base.name}-wal"), base.with_name(f"{base.name}-shm")):
            with suppress(OSError):
                if legacy.exists():
                    legacy.unlink()
                    logger.info("Removed superseded index file %s", legacy)
```

Add `from contextlib import suppress` to the imports.

- [ ] **Step 5: Rewrite the methods for source scoping**

```python
    def replace_items(self, source: str, items: list[LibraryItem]) -> int:
        """Replace this source's slice of the index, returning how many were stored.

        Scoped to one source so a Grimmory sync can never drop Audiobookshelf
        rows. Items that cannot produce a match key are dropped: they could
        never answer a lookup, so keeping them would only inflate the count the
        UI reports.
        """
        rows: list[tuple[str, ...]] = []
        key_rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        for item in items:
            if not item.item_id or item.item_id in seen:
                continue
            keys = build_match_keys(
                item.title, item.author, item.subtitle, asin=item.asin, isbn=item.isbn13
            )
            if not keys:
                continue

            seen.add(item.item_id)
            rows.append(
                (
                    source,
                    item.item_id,
                    item.library_id,
                    item.library_name,
                    item.media_type,
                    item.title,
                    item.subtitle,
                    item.author,
                    item.asin,
                    item.isbn13,
                )
            )
            key_rows.extend((key, source, item.item_id) for key in keys)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM library_items WHERE source = ?", (source,))
                conn.execute("DELETE FROM library_item_keys WHERE source = ?", (source,))
                conn.executemany(
                    """
                    INSERT INTO library_items
                        (source, item_id, library_id, library_name, media_type,
                         title, subtitle, author, asin, isbn13)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO library_item_keys (match_key, source, item_id) "
                    "VALUES (?, ?, ?)",
                    key_rows,
                )
                self._set_state(conn, source, _STATE_LAST_SYNC, datetime.now(UTC).isoformat())
                self._set_state(conn, source, _STATE_LAST_ERROR, None)
                conn.commit()
            finally:
                conn.close()

        return len(rows)

    def find_matches(self, match_keys: set[str]) -> list[LibraryMatch]:
        """Return every indexed item matching any of `match_keys`, across all sources."""
        keys = [key for key in match_keys if key]
        if not keys:
            return []

        matches: dict[tuple[str, str], LibraryMatch] = {}
        with self._lock:
            conn = self._connect()
            try:
                for start in range(0, len(keys), _MAX_QUERY_KEYS):
                    chunk = keys[start : start + _MAX_QUERY_KEYS]
                    placeholders = ",".join("?" * len(chunk))
                    cursor = conn.execute(
                        f"""
                        SELECT DISTINCT i.source, i.item_id, i.library_id, i.library_name,
                               i.media_type, i.title, i.author, i.asin, i.isbn13
                        FROM library_items i
                        JOIN library_item_keys k
                          ON k.item_id = i.item_id AND k.source = i.source
                        WHERE k.match_key IN ({placeholders})
                        """,  # noqa: S608 - placeholders only, keys are bound
                        chunk,
                    )
                    for row in cursor.fetchall():
                        match = LibraryMatch(
                            source=str(row["source"]),
                            item_id=str(row["item_id"]),
                            library_id=str(row["library_id"]),
                            library_name=str(row["library_name"]),
                            media_type=str(row["media_type"]),
                            title=str(row["title"]),
                            author=str(row["author"]),
                            asin=str(row["asin"] or ""),
                            isbn13=str(row["isbn13"] or ""),
                        )
                        matches[(match.source, match.item_id)] = match
            finally:
                conn.close()

        return list(matches.values())

    def record_failure(self, source: str, message: str) -> None:
        """Record that a sync attempt failed, leaving the existing index intact."""
        with self._lock:
            conn = self._connect()
            try:
                self._set_state(conn, source, _STATE_LAST_ERROR, message)
                conn.commit()
            finally:
                conn.close()

    def get_state(self, source: str) -> IndexState:
        """Return this source's index freshness and last failure, if any."""
        with self._lock:
            conn = self._connect()
            try:
                state = {
                    str(row["key"]): row["value"]
                    for row in conn.execute(
                        "SELECT key, value FROM index_state WHERE source = ?", (source,)
                    ).fetchall()
                }
                count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM library_items WHERE source = ?", (source,)
                ).fetchone()
            finally:
                conn.close()

        last_error = state.get(_STATE_LAST_ERROR)
        return IndexState(
            last_sync_at=state.get(_STATE_LAST_SYNC) or None,
            last_error=str(last_error) if last_error else None,
            item_count=int(count_row["n"]) if count_row else 0,
        )

    def _set_state(
        self, conn: sqlite3.Connection, source: str, key: str, value: str | None
    ) -> None:
        conn.execute(
            "INSERT INTO index_state (source, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(source, key) DO UPDATE SET value = excluded.value",
            (source, key, value),
        )
```

Update the module docstring to describe a multi-source index rather than an Audiobookshelf one.

- [ ] **Step 6: Patch the two remaining callers so the suite runs**

`shelfmark/audiobookshelf/library_sync.py` — `index.replace_items(items)` → `index.replace_items(SOURCE_AUDIOBOOKSHELF, items)`, `index.record_failure(msg)` → `index.record_failure(SOURCE_AUDIOBOOKSHELF, msg)`, `get_library_index().get_state()` → `.get_state(SOURCE_AUDIOBOOKSHELF)`, and give `extract_library_items` `source=SOURCE_AUDIOBOOKSHELF`, `media_type=MEDIA_TYPE_AUDIOBOOK`, `isbn13=""` when building each `LibraryItem`. `shelfmark/audiobookshelf/library_lookup.py` — `library_index.get_state()` → `.get_state(SOURCE_AUDIOBOOKSHELF)`. These files are replaced in Tasks 4 and 7; this keeps the tree green in between.

- [ ] **Step 7: Run the suite**

Run: `pytest tests/library tests/audiobookshelf -q && ruff check shelfmark/library`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A shelfmark tests
git commit -m "feat: key the library index by source and media type"
```

---

### Task 4: Provider protocol and generalized scheduler

**Files:**
- Move: `shelfmark/audiobookshelf/library_sync.py` → `shelfmark/library/scheduler.py`
- Move: `tests/audiobookshelf/test_library_sync.py` → `tests/library/test_scheduler.py`
- Create: `shelfmark/library/providers/audiobookshelf.py`
- Modify: `shelfmark/main.py:26`, `shelfmark/audiobookshelf/settings.py`

**Interfaces:**
- Consumes: `LibraryIndexDB`, `LibraryItem`, `SOURCE_*`, `MEDIA_TYPE_*` from Task 3.
- Produces:
  - `shelfmark.library.providers.LibraryProvider` — Protocol with `source: str`, `is_enabled() -> bool`, `interval_hours() -> float`, `fetch_items() -> list[LibraryItem]`
  - `shelfmark.library.providers.get_providers() -> list[LibraryProvider]`
  - `shelfmark.library.scheduler.SyncResult(success: bool, item_count: int, message: str)`
  - `shelfmark.library.scheduler.sync_provider(provider, index) -> SyncResult`
  - `shelfmark.library.scheduler.run_sync_now(source: str) -> SyncResult`
  - `shelfmark.library.scheduler.is_index_stale(last_sync_at, *, interval_hours) -> bool`
  - `shelfmark.library.scheduler.start_library_index_sync() -> None`
  - `shelfmark.library.providers.audiobookshelf.AudiobookshelfProvider`
  - `shelfmark.library.providers.audiobookshelf.extract_library_items(raw_items, library) -> list[LibraryItem]`

- [ ] **Step 1: Write the failing provider test**

Create `tests/library/test_providers_audiobookshelf.py`:

```python
"""Tests for the Audiobookshelf provider feeding the shared index."""

from shelfmark.audiobookshelf.client import AudiobookshelfLibrary
from shelfmark.library.index import MEDIA_TYPE_AUDIOBOOK, SOURCE_AUDIOBOOKSHELF
from shelfmark.library.providers.audiobookshelf import extract_library_items

LIBRARY = AudiobookshelfLibrary(id="lib_1", name="Audiobooks", media_type="book")


def _raw(item_id="li_1", title="The Housemaid", **metadata):
    return {"id": item_id, "media": {"metadata": {"title": title, **metadata}}}


class TestExtractLibraryItems:
    def test_tags_every_item_with_its_source_and_media_type(self):
        items = extract_library_items([_raw(authorName="Freida McFadden")], LIBRARY)

        assert [i.source for i in items] == [SOURCE_AUDIOBOOKSHELF]
        assert [i.media_type for i in items] == [MEDIA_TYPE_AUDIOBOOK]

    def test_carries_no_isbn(self):
        # Audiobookshelf exposes no ISBN; the column must stay empty rather than
        # pick up a stray value that would key against ebook holdings.
        items = extract_library_items([_raw(authorName="Freida McFadden")], LIBRARY)

        assert items[0].isbn13 == ""

    def test_prefers_the_authors_list_over_the_joined_name(self):
        items = extract_library_items(
            [_raw(authors=[{"name": "Freida McFadden"}], authorName="McFadden, Freida & Other")],
            LIBRARY,
        )

        assert items[0].author == "Freida McFadden"

    def test_skips_items_with_no_id_or_title(self):
        assert extract_library_items([{"id": "", "media": {"metadata": {"title": "X"}}}], LIBRARY) == []
        assert extract_library_items([_raw(item_id="li_2", title="")], LIBRARY) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/library/test_providers_audiobookshelf.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfmark.library.providers.audiobookshelf'`

- [ ] **Step 3: Define the provider protocol**

Replace `shelfmark/library/providers/__init__.py`:

```python
"""Per-source providers feeding the shared library index.

A provider answers three questions: whether it is switched on, how often it
should refresh, and what its library currently holds. Everything else —
storage, staleness, scheduling, failure handling — belongs to the shared core,
so adding a library source means writing one small module rather than a second
copy of the index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from shelfmark.library.index import LibraryItem


class LibraryProvider(Protocol):
    """One library Shelfmark can ask what it already holds."""

    source: str

    def is_enabled(self) -> bool:
        """Whether this source should be indexed and consulted."""
        ...

    def interval_hours(self) -> float:
        """How often this source's slice of the index should be rebuilt."""
        ...

    def fetch_items(self) -> list[LibraryItem]:
        """Return everything this library holds. Raises on transport failure."""
        ...


def get_providers() -> list[LibraryProvider]:
    """Return every registered provider.

    Imported lazily: the providers pull in settings modules, which import config,
    which imports this package.
    """
    from shelfmark.library.providers.audiobookshelf import AudiobookshelfProvider
    from shelfmark.library.providers.grimmory import GrimmoryProvider

    return [AudiobookshelfProvider(), GrimmoryProvider()]
```

- [ ] **Step 4: Write the ABS provider**

Create `shelfmark/library/providers/audiobookshelf.py`, moving `_metadata`, `_primary_author` and `extract_library_items` out of the old `library_sync.py`:

```python
"""Audiobookshelf as a source for the shared library index.

Every book-type library is indexed, not just the ones mapped as download
destinations: a book sitting in an unmapped library is still a book you own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shelfmark.library.index import MEDIA_TYPE_AUDIOBOOK, SOURCE_AUDIOBOOKSHELF, LibraryItem

if TYPE_CHECKING:
    from shelfmark.audiobookshelf.client import AudiobookshelfLibrary

LIBRARY_INDEX_ENABLED_KEY = "AUDIOBOOKSHELF_LIBRARY_INDEX_ENABLED"
LIBRARY_INDEX_INTERVAL_KEY = "AUDIOBOOKSHELF_INDEX_INTERVAL_HOURS"

_DEFAULT_INTERVAL_HOURS = 1.0
_MIN_INTERVAL_HOURS = 1.0


def _metadata(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    media = raw.get("media")
    if not isinstance(media, dict):
        return None
    metadata = media.get("metadata")
    return metadata if isinstance(metadata, dict) else None


def _primary_author(metadata: dict[str, Any]) -> str:
    """Return the first author's name.

    `authors` is preferred because `authorName` joins co-authors with commas,
    which the matcher would otherwise read as an inverted "Last, First" name.
    """
    authors = metadata.get("authors")
    if isinstance(authors, list):
        for author in authors:
            if isinstance(author, dict):
                name = str(author.get("name") or "").strip()
                if name:
                    return name

    return str(metadata.get("authorName") or "").strip()


def extract_library_items(
    raw_items: list[Any],
    library: AudiobookshelfLibrary,
) -> list[LibraryItem]:
    """Flatten Audiobookshelf's nested item payloads into index rows."""
    items: list[LibraryItem] = []

    for raw in raw_items:
        metadata = _metadata(raw)
        if metadata is None or not isinstance(raw, dict):
            continue

        item_id = str(raw.get("id") or "").strip()
        title = str(metadata.get("title") or "").strip()
        if not item_id or not title:
            continue

        items.append(
            LibraryItem(
                source=SOURCE_AUDIOBOOKSHELF,
                item_id=item_id,
                library_id=library.id,
                library_name=library.name,
                media_type=MEDIA_TYPE_AUDIOBOOK,
                title=title,
                subtitle=str(metadata.get("subtitle") or "").strip(),
                author=_primary_author(metadata),
                asin=str(metadata.get("asin") or "").strip(),
                # Audiobookshelf exposes no ISBN. Leaving this empty keeps
                # audiobook rows from keying against ebook holdings.
                isbn13="",
            )
        )

    return items


class AudiobookshelfProvider:
    """Indexes every book-type Audiobookshelf library."""

    source = SOURCE_AUDIOBOOKSHELF

    def is_enabled(self) -> bool:
        """Report whether the Audiobookshelf slice should be maintained."""
        from shelfmark.core.config import config

        return bool(config.get("AUDIOBOOKSHELF_ENABLED", False)) and bool(
            config.get(LIBRARY_INDEX_ENABLED_KEY, True)
        )

    def interval_hours(self) -> float:
        """Return the configured refresh interval, floored at one hour."""
        from shelfmark.core.config import config

        raw = config.get(LIBRARY_INDEX_INTERVAL_KEY, _DEFAULT_INTERVAL_HOURS)
        try:
            interval = float(raw)  # pyright: ignore[reportArgumentType] - config values are untyped
        except (TypeError, ValueError):
            return _DEFAULT_INTERVAL_HOURS

        return max(interval, _MIN_INTERVAL_HOURS)

    def fetch_items(self) -> list[LibraryItem]:
        """Walk every book-type library and flatten it into index rows."""
        from shelfmark.audiobookshelf.settings import build_client_from_config

        client = build_client_from_config()
        if client is None:
            msg = "Audiobookshelf is not configured"
            raise RuntimeError(msg)

        items: list[LibraryItem] = []
        for library in client.get_book_libraries():
            items.extend(extract_library_items(client.get_library_items(library.id), library))
        return items
```

- [ ] **Step 5: Run the provider test**

Run: `pytest tests/library/test_providers_audiobookshelf.py -q`
Expected: PASS.

- [ ] **Step 6: Generalize the scheduler**

```bash
git mv shelfmark/audiobookshelf/library_sync.py shelfmark/library/scheduler.py
git mv tests/audiobookshelf/test_library_sync.py tests/library/test_scheduler.py
```

Rewrite `shelfmark/library/scheduler.py`, keeping `is_index_stale` byte-for-byte and replacing the ABS-specific sync with a provider loop:

```python
"""Refreshing the shared library index from every registered provider.

Nothing here raises at the caller. A sync that fails records why and leaves the
previous index in place, so an outage costs badge freshness rather than the
badges themselves — and one source being down never blanks another.
"""

from __future__ import annotations

import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from shelfmark.core.logger import setup_logger
from shelfmark.library.index import LibraryIndexDB, get_library_index
from shelfmark.library.providers import LibraryProvider, get_providers

logger = setup_logger(__name__)

# The scheduler wakes far more often than it syncs so an interval change takes
# effect without a restart; staleness, not the sleep, decides when to sync.
_SCHEDULER_POLL_SECONDS = 300

# Anything a provider's transport can throw. Providers must not leak these.
PROVIDER_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True)
class SyncResult:
    """The outcome of one index refresh."""

    success: bool
    item_count: int
    message: str


def sync_provider(provider: LibraryProvider, index: LibraryIndexDB) -> SyncResult:
    """Rebuild one source's slice of the index, or explain why it could not be.

    The storage write is inside the guard, not just the fetch. A full disk or a
    locked database is exactly as much a sync failure as an unreachable server,
    and letting it escape would take down the caller — the settings action
    button and the scheduler cycle both call straight through here.
    """
    try:
        items = provider.fetch_items()
        stored = index.replace_items(provider.source, items)
    except PROVIDER_ERRORS as e:
        message = f"Library sync failed: {e!s}"
        logger.warning("%s (%s)", message, provider.source)
        # Best effort: if the index itself is what failed, recording the failure
        # may fail too, and that must not escape either.
        with suppress(Exception):
            index.record_failure(provider.source, message)
        return SyncResult(success=False, item_count=0, message=message)

    logger.info("Indexed %d items from %s", stored, provider.source)
    return SyncResult(
        success=True,
        item_count=stored,
        message=f"Indexed {stored} items from {provider.source}",
    )


def run_sync_now(source: str) -> SyncResult:
    """Refresh one source immediately, by name."""
    for provider in get_providers():
        if provider.source == source:
            return sync_provider(provider, get_library_index())

    return SyncResult(success=False, item_count=0, message=f"Unknown library source {source!r}")


def is_index_stale(last_sync_at: str | None, *, interval_hours: float) -> bool:
    """Report whether the index is older than the refresh interval.

    An unreadable timestamp counts as stale: re-syncing costs one API walk,
    while trusting it could pin a broken index in place forever.
    """
    if not last_sync_at:
        return True

    try:
        synced = datetime.fromisoformat(last_sync_at)
    except ValueError:
        logger.warning("Unreadable library index timestamp %r; treating as stale", last_sync_at)
        return True

    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=UTC)

    age_hours = (datetime.now(UTC) - synced).total_seconds() / 3600
    return age_hours >= interval_hours


def _scheduler_loop() -> None:
    while True:
        try:
            index = get_library_index()
            for provider in get_providers():
                if not provider.is_enabled():
                    continue
                state = index.get_state(provider.source)
                if is_index_stale(state.last_sync_at, interval_hours=provider.interval_hours()):
                    sync_provider(provider, index)
        except Exception:
            # A scheduler that dies on one bad cycle stops refreshing forever.
            logger.exception("Library index sync cycle failed")

        time.sleep(_SCHEDULER_POLL_SECONDS)


_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()


def start_library_index_sync() -> None:
    """Start the background index refresher. Safe to call multiple times."""
    global _scheduler_thread

    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return

        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            daemon=True,
            name="LibraryIndex",
        )
        _scheduler_thread.start()

    logger.info("Library index refresher started")
```

- [ ] **Step 7: Rework the scheduler tests**

In `tests/library/test_scheduler.py`, keep every `is_index_stale` test unchanged. Replace the `sync_library_index` tests with `sync_provider` equivalents driven by a stub:

```python
import pytest

from shelfmark.library.index import MEDIA_TYPE_EBOOK, SOURCE_GRIMMORY, LibraryItem
from shelfmark.library.scheduler import SyncResult, sync_provider


class _StubProvider:
    source = SOURCE_GRIMMORY

    def __init__(self, items=None, error=None):
        self._items = items or []
        self._error = error

    def is_enabled(self):
        return True

    def interval_hours(self):
        return 1.0

    def fetch_items(self):
        if self._error is not None:
            raise self._error
        return self._items


def _item(item_id="1", title="The Housemaid"):
    return LibraryItem(
        source=SOURCE_GRIMMORY,
        item_id=item_id,
        library_id="lib_1",
        library_name="Ebooks",
        media_type=MEDIA_TYPE_EBOOK,
        title=title,
        subtitle="",
        author="Freida McFadden",
        asin="",
        isbn13="",
    )


class TestSyncProvider:
    def test_stores_what_the_provider_returns(self, index):
        result = sync_provider(_StubProvider([_item()]), index)

        assert result == SyncResult(
            success=True, item_count=1, message="Indexed 1 items from grimmory"
        )
        assert index.get_state(SOURCE_GRIMMORY).item_count == 1

    def test_a_failure_leaves_the_previous_index_standing(self, index):
        sync_provider(_StubProvider([_item()]), index)

        result = sync_provider(_StubProvider(error=OSError("connection refused")), index)

        assert result.success is False
        assert index.get_state(SOURCE_GRIMMORY).item_count == 1
        assert "connection refused" in index.get_state(SOURCE_GRIMMORY).last_error

    def test_a_storage_failure_is_reported_rather_than_raised(self, index, monkeypatch):
        # The settings "Sync Library Now" button and the scheduler both call
        # straight through here, so a locked or full database has to come back
        # as a result, not as an exception.
        def boom(*args, **kwargs):
            raise OSError("database is locked")

        monkeypatch.setattr(index, "replace_items", boom)

        result = sync_provider(_StubProvider([_item()]), index)

        assert result.success is False
        assert "database is locked" in result.message
```

Add the shared `index` fixture to `tests/library/conftest.py`:

```python
"""Shared fixtures for the library index tests."""

import pytest

from shelfmark.library.index import LibraryIndexDB


@pytest.fixture
def index(tmp_path):
    """A freshly initialized index backed by a temporary file."""
    db = LibraryIndexDB(str(tmp_path / "library_index.db"))
    db.initialize()
    return db
```

- [ ] **Step 8: Rewire the callers**

- `shelfmark/main.py:26` — `from shelfmark.audiobookshelf.library_sync import start_library_index_sync` becomes `from shelfmark.library.scheduler import start_library_index_sync`.
- `shelfmark/audiobookshelf/settings.py` — `sync_library_index_now` calls `run_sync_now(SOURCE_AUDIOBOOKSHELF)`; import `LIBRARY_INDEX_ENABLED_KEY` and `LIBRARY_INDEX_INTERVAL_KEY` from `shelfmark.library.providers.audiobookshelf`.

  `tests/audiobookshelf/test_library_index_settings.py:58` already asserts that an unexpected `OSError("disk full")` comes back as a structured failure response rather than escaping this callback. Keep that test passing unchanged — it is the contract the `sync_provider` guard above exists to satisfy, and the new Grimmory callback in Task 9 inherits the same guarantee through `run_sync_now`.
- `shelfmark/audiobookshelf/library_lookup.py` — import `is_index_stale` from `shelfmark.library.scheduler`, and replace `library_index_enabled()` / `get_interval_hours()` with `AudiobookshelfProvider()` calls. This file is replaced in Task 7.

- [ ] **Step 9: Run the suite**

Run: `pytest tests/library tests/audiobookshelf -q && ruff check shelfmark`
Expected: PASS. `GrimmoryProvider` does not exist yet, so temporarily have `get_providers()` return only `AudiobookshelfProvider()`; Task 6 restores the second entry.

- [ ] **Step 10: Commit**

```bash
git add -A shelfmark tests
git commit -m "refactor: drive the library index from pluggable providers"
```

---

### Task 5: Grimmory client

**Files:**
- Create: `shelfmark/grimmory/__init__.py`, `shelfmark/grimmory/client.py`
- Modify: `shelfmark/download/outputs/booklore.py`, `shelfmark/config/booklore_settings.py`
- Test: `tests/grimmory/test_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, from `shelfmark.grimmory.client`:
  - `BookloreError`, `BookloreConfig`, `booklore_login(config) -> str`, `booklore_list_libraries(config, token) -> list[dict]` — all moved verbatim
  - `BOOKLORE_DISPLAY_NAME = "Grimmory"`
  - `list_books(config: BookloreConfig, token: str, *, page: int, size: int) -> tuple[list[dict[str, Any]], int]` — returns `(books, total_pages)`

- [ ] **Step 1: Write the failing test**

Create `tests/grimmory/__init__.py` and `tests/grimmory/test_client.py`:

```python
"""Tests for the Grimmory API client."""

import pytest
import requests

from shelfmark.grimmory.client import BookloreConfig, BookloreError, list_books

CONFIG = BookloreConfig(
    base_url="http://grimmory:6060",
    username="shelfmark",
    password="secret",
    library_id=0,
    path_id=0,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


class TestListBooks:
    def test_returns_the_page_content_and_total_pages(self, monkeypatch):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return _Response({"content": [{"id": 1}], "totalPages": 3})

        monkeypatch.setattr(requests, "get", fake_get)

        books, total_pages = list_books(CONFIG, "token", page=0, size=500)

        assert books == [{"id": 1}]
        assert total_pages == 3
        assert captured["url"] == "http://grimmory:6060/api/v1/books/page"
        assert captured["params"] == {"page": 0, "size": 500}

    def test_treats_a_missing_total_pages_as_one(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response({"content": []}))

        assert list_books(CONFIG, "token", page=0, size=500) == ([], 1)

    def test_raises_a_booklore_error_on_transport_failure(self, monkeypatch):
        def boom(url, **kwargs):
            raise requests.exceptions.ConnectionError

        monkeypatch.setattr(requests, "get", boom)

        with pytest.raises(BookloreError, match="Grimmory"):
            list_books(CONFIG, "token", page=0, size=500)

    def test_raises_on_an_unexpected_payload_shape(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(["not", "an", "object"]))

        with pytest.raises(BookloreError):
            list_books(CONFIG, "token", page=0, size=500)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/grimmory/test_client.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfmark.grimmory'`

- [ ] **Step 3: Create the client by moving the connection primitives**

Create `shelfmark/grimmory/__init__.py`:

```python
"""Grimmory integration: an API client shared by uploads and the library index."""
```

Create `shelfmark/grimmory/client.py` and move `BookloreError`, `BookloreConfig`, `_parse_int`, `_parse_destination`, `booklore_login`, `booklore_list_libraries`, `BOOKLORE_DISPLAY_NAME`, `BOOKLORE_DESTINATION_LIBRARY` and `BOOKLORE_DESTINATION_BOOKDROP` into it verbatim from `shelfmark/download/outputs/booklore.py`. Then add:

```python
_BOOKS_PAGE_ENDPOINT = "/api/v1/books/page"


def list_books(
    booklore_config: BookloreConfig,
    token: str,
    *,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch one page of books, returning the rows and the total page count.

    Uses the legacy page/size mode — supplying no sort, facet or query keeps the
    endpoint on plain offset pagination, which needs no cursor to resume.

    What comes back is scoped to the authenticated user: an admin sees every
    library, anyone else only their assigned ones. The account in
    BOOKLORE_USERNAME therefore decides how much of the library gets indexed.
    """
    url = f"{booklore_config.base_url}{_BOOKS_PAGE_ENDPOINT}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(
            url,
            headers=headers,
            params={"page": page, "size": size},
            timeout=60,
            verify=booklore_config.verify_tls,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {BOOKLORE_DISPLAY_NAME}"
        raise BookloreError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} book listing timed out"
        raise BookloreError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"Failed to fetch {BOOKLORE_DISPLAY_NAME} books: {exc}"
        raise BookloreError(msg) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        msg = f"Invalid {BOOKLORE_DISPLAY_NAME} book listing response"
        raise BookloreError(msg) from exc

    if not isinstance(payload, dict):
        msg = f"Unexpected {BOOKLORE_DISPLAY_NAME} book listing payload"
        raise BookloreError(msg)

    content = payload.get("content")
    books = [row for row in content if isinstance(row, dict)] if isinstance(content, list) else []

    raw_total = payload.get("totalPages")
    total_pages = raw_total if isinstance(raw_total, int) and raw_total > 0 else 1

    return books, total_pages
```

Add `from typing import Any` and `import requests` to the imports.

- [ ] **Step 4: Report the visible book count from Test Connection**

The spec requires Test Connection to show how many books the account can actually see, because `GET /api/v1/books` is user-scoped and a non-admin service account silently indexes only its assigned libraries — a failure that otherwise shows up as mysteriously missing badges. `check_booklore_connection` currently reports only the library count (`shelfmark/config/booklore_settings.py:195-204`).

Add to `tests/grimmory/test_client.py`:

```python
class TestConnectionMessage:
    def test_reports_both_library_and_book_counts(self, monkeypatch):
        from shelfmark.config import booklore_settings

        monkeypatch.setattr(
            booklore_settings,
            "_get_booklore_select_options",
            lambda *a, **kw: ([{"value": "1", "label": "Ebooks"}], []),
        )
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(booklore_settings, "list_books", lambda *a, **kw: ([{"id": 1}], 3))

        result = booklore_settings.check_booklore_connection(
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
            }
        )

        assert result["success"] is True
        assert "1 libraries" in result["message"]
        assert "books" in result["message"]
```

Then extend the success branch of `check_booklore_connection`. One page is enough — `totalPages × size` bounds the count without walking the library:

```python
    else:
        message = f"Connected to {BOOKLORE_DISPLAY_NAME}"
        if library_options:
            message = f"Connected to {BOOKLORE_DISPLAY_NAME} ({len(library_options)} libraries)"

        # One page bounds the total without walking the whole library. The count
        # is what makes a too-narrow service account visible here rather than as
        # absent badges hours later.
        with suppress(BookloreError):
            token = booklore_login(_config_from(base_url, username, password))
            books, total_pages = list_books(
                _config_from(base_url, username, password), token, page=0, size=1
            )
            if books or total_pages:
                message = f"{message[:-1]}, {total_pages} books)" if message.endswith(")") else (
                    f"{message} ({total_pages} books)"
                )

        return {"success": True, "message": message}
```

Add a small `_config_from(base_url, username, password) -> BookloreConfig` helper beside it rather than repeating the construction. Wrapping in `suppress` is deliberate: the connection genuinely succeeded, so a book-count hiccup should cost the extra detail, not turn a passing test into a failure.

- [ ] **Step 5: Re-point the two existing importers**

In `shelfmark/download/outputs/booklore.py`, delete the moved definitions and import them instead:

```python
from shelfmark.grimmory.client import (
    BOOKLORE_DESTINATION_BOOKDROP,
    BOOKLORE_DESTINATION_LIBRARY,
    BOOKLORE_DISPLAY_NAME,
    BookloreConfig,
    BookloreError,
    _parse_destination,
    _parse_int,
    booklore_login,
)
```

Rename `_parse_destination` and `_parse_int` to `parse_destination` and `parse_int` in `client.py` as part of the move, since they are now cross-module, and import those names.

In `shelfmark/config/booklore_settings.py`, change the import block to read from `shelfmark.grimmory.client`. That removes a config module's dependency on `shelfmark.download.outputs`.

- [ ] **Step 6: Run the suite**

Run: `pytest tests/grimmory tests/core/test_processing_integration.py tests/config -q && ruff check shelfmark`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A shelfmark tests
git commit -m "refactor: extract a shared Grimmory client and add book listing"
```

---

### Task 6: Grimmory provider

**Files:**
- Create: `shelfmark/library/providers/grimmory.py`
- Modify: `shelfmark/library/providers/__init__.py`
- Test: `tests/library/test_providers_grimmory.py`

**Interfaces:**
- Consumes: `list_books`, `booklore_login`, `BookloreConfig` from Task 5; `LibraryItem`, `SOURCE_GRIMMORY`, `MEDIA_TYPE_*` from Task 3.
- Produces:
  - `shelfmark.library.providers.grimmory.GrimmoryProvider`
  - `shelfmark.library.providers.grimmory.extract_library_items(raw_books: list[Any]) -> list[LibraryItem]`
  - `LIBRARY_INDEX_ENABLED_KEY = "BOOKLORE_LIBRARY_INDEX_ENABLED"`, `LIBRARY_INDEX_INTERVAL_KEY = "BOOKLORE_INDEX_INTERVAL_HOURS"`

- [ ] **Step 1: Write the failing tests**

Create `tests/library/test_providers_grimmory.py`:

```python
"""Tests for the Grimmory provider feeding the shared index."""

from shelfmark.library.index import MEDIA_TYPE_AUDIOBOOK, MEDIA_TYPE_EBOOK, SOURCE_GRIMMORY
from shelfmark.library.providers.grimmory import extract_library_items


def _book(book_id=1, file_type="EPUB", alternatives=None, **metadata):
    base = {
        "title": "The Housemaid",
        "authors": ["Freida McFadden"],
    }
    base.update(metadata)
    return {
        "id": book_id,
        "libraryId": 7,
        "libraryName": "Ebooks",
        "primaryFile": {"bookType": file_type},
        "alternativeFormats": alternatives or [],
        "metadata": base,
    }


class TestExtractLibraryItems:
    def test_flattens_a_book_into_an_index_row(self):
        items = extract_library_items([_book(isbn13="9780593135204", asin="B09XYZ1234")])

        assert len(items) == 1
        item = items[0]
        assert item.source == SOURCE_GRIMMORY
        assert item.item_id == "1"
        assert item.library_id == "7"
        assert item.library_name == "Ebooks"
        assert item.title == "The Housemaid"
        assert item.author == "Freida McFadden"
        assert item.isbn13 == "9780593135204"
        assert item.asin == "B09XYZ1234"

    def test_canonicalizes_an_isbn10_into_isbn13(self):
        items = extract_library_items([_book(isbn10="0306406152")])

        assert items[0].isbn13 == "9780306406157"

    def test_prefers_isbn13_when_both_are_present(self):
        items = extract_library_items([_book(isbn13="9780593135204", isbn10="0306406152")])

        assert items[0].isbn13 == "9780593135204"

    def test_takes_the_first_author(self):
        items = extract_library_items([_book(authors=["Freida McFadden", "Someone Else"])])

        assert items[0].author == "Freida McFadden"

    def test_falls_back_to_the_top_level_title(self):
        raw = _book()
        raw["metadata"].pop("title")
        raw["title"] = "The Housemaid"

        assert extract_library_items([raw])[0].title == "The Housemaid"

    def test_skips_a_book_with_no_id_or_title(self):
        assert extract_library_items([_book(book_id=None)]) == []
        no_title = _book()
        no_title["metadata"]["title"] = ""
        no_title["title"] = ""
        assert extract_library_items([no_title]) == []


class TestMediaTypeDerivation:
    def test_an_epub_is_an_ebook(self):
        assert extract_library_items([_book(file_type="EPUB")])[0].media_type == MEDIA_TYPE_EBOOK

    def test_an_audiobook_only_entry_is_an_audiobook(self):
        items = extract_library_items([_book(file_type="AUDIOBOOK")])

        assert items[0].media_type == MEDIA_TYPE_AUDIOBOOK

    def test_a_book_with_both_an_epub_and_an_audiobook_is_an_ebook(self):
        # Owning the M4B alongside the EPUB must not demote the ebook holding,
        # or the ebook badge silently disappears for dual-format books.
        items = extract_library_items(
            [_book(file_type="EPUB", alternatives=[{"bookType": "AUDIOBOOK"}])]
        )

        assert items[0].media_type == MEDIA_TYPE_EBOOK

    def test_an_unknown_file_type_defaults_to_ebook(self):
        raw = _book()
        raw["primaryFile"] = None
        raw["alternativeFormats"] = []

        assert extract_library_items([raw])[0].media_type == MEDIA_TYPE_EBOOK


class TestPagination:
    def test_walks_every_page(self, monkeypatch):
        from shelfmark.library.providers import grimmory as provider_module

        pages = {0: ([_book(book_id=1)], 3), 1: ([_book(book_id=2)], 3), 2: ([_book(book_id=3)], 3)}
        monkeypatch.setattr(provider_module, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(
            provider_module, "list_books", lambda cfg, token, *, page, size: pages[page]
        )
        monkeypatch.setattr(
            provider_module.GrimmoryProvider, "is_enabled", lambda self: True
        )

        items = provider_module.GrimmoryProvider().fetch_items()

        assert [item.item_id for item in items] == ["1", "2", "3"]

    def test_stops_at_the_page_cap(self, monkeypatch):
        # A server reporting an absurd totalPages must not spin forever.
        from shelfmark.library.providers import grimmory as provider_module

        calls = {"n": 0}

        def endless(cfg, token, *, page, size):
            calls["n"] += 1
            return ([_book(book_id=page)], 10_000)

        monkeypatch.setattr(provider_module, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(provider_module, "list_books", endless)
        monkeypatch.setattr(
            provider_module.GrimmoryProvider, "is_enabled", lambda self: True
        )

        provider_module.GrimmoryProvider().fetch_items()

        assert calls["n"] == provider_module._MAX_PAGES

    def test_a_single_page_library_makes_one_call(self, monkeypatch):
        from shelfmark.library.providers import grimmory as provider_module

        calls = {"n": 0}

        def one_page(cfg, token, *, page, size):
            calls["n"] += 1
            return ([_book(book_id=1)], 1)

        monkeypatch.setattr(provider_module, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(provider_module, "list_books", one_page)
        monkeypatch.setattr(
            provider_module.GrimmoryProvider, "is_enabled", lambda self: True
        )

        provider_module.GrimmoryProvider().fetch_items()

        assert calls["n"] == 1
```

`fetch_items` reads credentials from config, so these tests need `BOOKLORE_HOST`/`USERNAME`/`PASSWORD` present or the `BookloreConfig` construction yields empty strings. Since `booklore_login` is stubbed, empty values are harmless — but if `BookloreConfig` ever validates in `__post_init__`, patch config instead.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/library/test_providers_grimmory.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfmark.library.providers.grimmory'`

- [ ] **Step 3: Implement the provider**

Create `shelfmark/library/providers/grimmory.py`:

```python
"""Grimmory as a source for the shared library index.

Grimmory is the ebook library, but it can hold audiobooks too, so each item's
real format is recorded rather than assumed from the source. A stray audiobook
import must not be able to badge an ebook search result.
"""

from __future__ import annotations

from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.grimmory.client import BookloreConfig, BookloreError, booklore_login, list_books
from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_EBOOK,
    SOURCE_GRIMMORY,
    LibraryItem,
)
from shelfmark.library.matching import normalize_isbn

logger = setup_logger(__name__)

LIBRARY_INDEX_ENABLED_KEY = "BOOKLORE_LIBRARY_INDEX_ENABLED"
LIBRARY_INDEX_INTERVAL_KEY = "BOOKLORE_INDEX_INTERVAL_HOURS"

_DEFAULT_INTERVAL_HOURS = 1.0
_MIN_INTERVAL_HOURS = 1.0
_PAGE_SIZE = 500
_MAX_PAGES = 1000

_AUDIOBOOK_FILE_TYPE = "AUDIOBOOK"


def _file_types(raw: dict[str, Any]) -> list[str]:
    types: list[str] = []

    primary = raw.get("primaryFile")
    if isinstance(primary, dict):
        types.append(str(primary.get("bookType") or "").strip().upper())

    alternatives = raw.get("alternativeFormats")
    if isinstance(alternatives, list):
        types.extend(
            str(alt.get("bookType") or "").strip().upper()
            for alt in alternatives
            if isinstance(alt, dict)
        )

    return [file_type for file_type in types if file_type]


def _media_type(raw: dict[str, Any]) -> str:
    """Classify a book by the formats it actually holds.

    Only an entry whose every known file is an audiobook counts as one. A book
    holding an EPUB and an M4B is still an ebook you own, and defaulting the
    unknown case to ebook matches what Grimmory is for.
    """
    types = _file_types(raw)
    if types and all(file_type == _AUDIOBOOK_FILE_TYPE for file_type in types):
        return MEDIA_TYPE_AUDIOBOOK
    return MEDIA_TYPE_EBOOK


def _first_author(metadata: dict[str, Any]) -> str:
    authors = metadata.get("authors")
    if isinstance(authors, list):
        for author in authors:
            name = str(author or "").strip()
            if name:
                return name
    return ""


def extract_library_items(raw_books: list[Any]) -> list[LibraryItem]:
    """Flatten Grimmory's book payloads into index rows."""
    items: list[LibraryItem] = []

    for raw in raw_books:
        if not isinstance(raw, dict):
            continue

        metadata = raw.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}

        book_id = raw.get("id")
        item_id = "" if book_id is None else str(book_id).strip()
        title = str(metadata.get("title") or raw.get("title") or "").strip()
        if not item_id or not title:
            continue

        library_id = raw.get("libraryId")
        items.append(
            LibraryItem(
                source=SOURCE_GRIMMORY,
                item_id=item_id,
                library_id="" if library_id is None else str(library_id),
                library_name=str(raw.get("libraryName") or "").strip(),
                media_type=_media_type(raw),
                title=title,
                subtitle=str(metadata.get("subtitle") or "").strip(),
                author=_first_author(metadata),
                asin=str(metadata.get("asin") or "").strip(),
                isbn13=normalize_isbn(metadata.get("isbn13"))
                or normalize_isbn(metadata.get("isbn10")),
            )
        )

    return items


class GrimmoryProvider:
    """Indexes every book the configured Grimmory account can see."""

    source = SOURCE_GRIMMORY

    def is_enabled(self) -> bool:
        """Report whether the Grimmory slice should be maintained.

        Credentials are part of the gate: a half-configured connection would
        otherwise fail on every scheduler cycle and log noise forever.
        """
        from shelfmark.core.config import config

        if not bool(config.get("BOOKLORE_ENABLED", False)):
            return False
        if not bool(config.get(LIBRARY_INDEX_ENABLED_KEY, True)):
            return False

        return all(
            str(config.get(key, "") or "").strip()
            for key in ("BOOKLORE_HOST", "BOOKLORE_USERNAME", "BOOKLORE_PASSWORD")
        )

    def interval_hours(self) -> float:
        """Return the configured refresh interval, floored at one hour."""
        from shelfmark.core.config import config

        raw = config.get(LIBRARY_INDEX_INTERVAL_KEY, _DEFAULT_INTERVAL_HOURS)
        try:
            interval = float(raw)  # pyright: ignore[reportArgumentType] - config values are untyped
        except (TypeError, ValueError):
            return _DEFAULT_INTERVAL_HOURS

        return max(interval, _MIN_INTERVAL_HOURS)

    def fetch_items(self) -> list[LibraryItem]:
        """Page through every book the configured account can see."""
        from shelfmark.core.config import config

        booklore_config = BookloreConfig(
            base_url=str(config.get("BOOKLORE_HOST", "") or "").strip().rstrip("/"),
            username=str(config.get("BOOKLORE_USERNAME", "") or "").strip(),
            password=str(config.get("BOOKLORE_PASSWORD", "") or ""),
            library_id=0,
            path_id=0,
        )

        token = booklore_login(booklore_config)

        items: list[LibraryItem] = []
        page = 0
        total_pages = 1
        while page < total_pages and page < _MAX_PAGES:
            books, total_pages = list_books(
                booklore_config, token, page=page, size=_PAGE_SIZE
            )
            items.extend(extract_library_items(books))
            page += 1

        if page >= _MAX_PAGES:
            logger.warning("Stopped indexing Grimmory at the %d page cap", _MAX_PAGES)

        return items
```

`BookloreError` subclasses `Exception`, not one of `PROVIDER_ERRORS`. Add it to that tuple in `shelfmark/library/scheduler.py`:

```python
from shelfmark.grimmory.client import BookloreError

PROVIDER_ERRORS = (BookloreError, OSError, RuntimeError, TypeError, ValueError)
```

- [ ] **Step 4: Restore the second provider**

In `shelfmark/library/providers/__init__.py`, `get_providers()` returns `[AudiobookshelfProvider(), GrimmoryProvider()]`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/library -q && ruff check shelfmark`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A shelfmark tests
git commit -m "feat: index Grimmory as an ebook library source"
```

---

### Task 7: Format-aware lookup

**Files:**
- Move: `shelfmark/audiobookshelf/library_lookup.py` → `shelfmark/library/lookup.py`
- Move: `tests/audiobookshelf/test_library_lookup.py` → `tests/library/test_lookup.py`

**Interfaces:**
- Consumes: everything from Tasks 3, 4 and 6.
- Produces: `shelfmark.library.lookup.lookup_books(books: list[Any], *, index: LibraryIndexDB | None = None) -> dict[str, Any]` and `MAX_LOOKUP_BOOKS = 200`.

- [ ] **Step 1: Write the failing tests**

```bash
git mv shelfmark/audiobookshelf/library_lookup.py shelfmark/library/lookup.py
git mv tests/audiobookshelf/test_library_lookup.py tests/library/test_lookup.py
```

Append to `tests/library/test_lookup.py`:

```python
from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_EBOOK,
    SOURCE_AUDIOBOOKSHELF,
    SOURCE_GRIMMORY,
    LibraryItem,
)
from shelfmark.library.lookup import lookup_books


def _stored(source, media_type, item_id="1", **overrides):
    fields = {
        "source": source,
        "item_id": item_id,
        "library_id": "lib_1",
        "library_name": "Library",
        "media_type": media_type,
        "title": "The Housemaid",
        "subtitle": "",
        "author": "Freida McFadden",
        "asin": "",
        "isbn13": "",
    }
    fields.update(overrides)
    return LibraryItem(**fields)


@pytest.fixture
def both_formats(index, enabled_providers):
    index.replace_items(SOURCE_GRIMMORY, [_stored(SOURCE_GRIMMORY, MEDIA_TYPE_EBOOK)])
    index.replace_items(
        SOURCE_AUDIOBOOKSHELF, [_stored(SOURCE_AUDIOBOOKSHELF, MEDIA_TYPE_AUDIOBOOK, "abs_1")]
    )
    return index


def _book(content_type):
    return {
        "id": "b1",
        "title": "The Housemaid",
        "author": "Freida McFadden",
        "content_type": content_type,
    }


class TestFormatScoping:
    def test_an_ebook_matches_the_grimmory_holding(self, both_formats):
        result = lookup_books([_book("ebook")], index=both_formats)

        match = result["matches"]["b1"]
        assert [i["source"] for i in match["items"]] == [SOURCE_GRIMMORY]

    def test_an_ebook_reports_the_audiobook_as_another_format(self, both_formats):
        result = lookup_books([_book("ebook")], index=both_formats)

        match = result["matches"]["b1"]
        assert [i["source"] for i in match["other_formats"]] == [SOURCE_AUDIOBOOKSHELF]

    def test_an_audiobook_matches_the_other_way_round(self, both_formats):
        result = lookup_books([_book("audiobook")], index=both_formats)

        match = result["matches"]["b1"]
        assert [i["source"] for i in match["items"]] == [SOURCE_AUDIOBOOKSHELF]
        assert [i["source"] for i in match["other_formats"]] == [SOURCE_GRIMMORY]

    def test_an_audio_only_holding_leaves_an_ebook_unowned(self, index, enabled_providers):
        # The whole point of the format split: owning the audiobook must not
        # lock the ebook, so `items` has to come back empty.
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, [_stored(SOURCE_AUDIOBOOKSHELF, MEDIA_TYPE_AUDIOBOOK)]
        )

        match = lookup_books([_book("ebook")], index=index)["matches"]["b1"]

        assert match["items"] == []
        assert len(match["other_formats"]) == 1

    def test_a_book_held_in_neither_format_is_absent(self, both_formats):
        book = {"id": "b2", "title": "Something Else", "author": "Nobody", "content_type": "ebook"}

        assert lookup_books([book], index=both_formats)["matches"] == {}

    def test_matches_by_isbn(self, index, enabled_providers):
        index.replace_items(
            SOURCE_GRIMMORY,
            [_stored(SOURCE_GRIMMORY, MEDIA_TYPE_EBOOK, isbn13="9780593135204")],
        )

        book = {"id": "b1", "isbn_10": "0593135202", "content_type": "ebook"}
        result = lookup_books([book], index=index)

        assert len(result["matches"]["b1"]["items"]) == 1


class TestSourceReporting:
    def test_reports_each_source_separately(self, both_formats):
        result = lookup_books([_book("ebook")], index=both_formats)

        assert set(result["sources"]) == {SOURCE_GRIMMORY, SOURCE_AUDIOBOOKSHELF}
        assert result["sources"][SOURCE_GRIMMORY]["item_count"] == 1

    def test_enabled_is_true_when_any_source_is_on(self, both_formats):
        assert lookup_books([_book("ebook")], index=both_formats)["enabled"] is True
```

Add the `enabled_providers` fixture to `tests/library/conftest.py`:

```python
@pytest.fixture
def enabled_providers(monkeypatch):
    """Force both providers on, so lookup tests exercise matching not config."""
    from shelfmark.library.providers.audiobookshelf import AudiobookshelfProvider
    from shelfmark.library.providers.grimmory import GrimmoryProvider

    monkeypatch.setattr(AudiobookshelfProvider, "is_enabled", lambda self: True)
    monkeypatch.setattr(GrimmoryProvider, "is_enabled", lambda self: True)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/library/test_lookup.py -q`
Expected: FAIL — `lookup_books` returns no `other_formats` key.

- [ ] **Step 3: Rewrite the lookup**

Replace `shelfmark/library/lookup.py`:

```python
"""Batch "do I already own this?" lookups for the in-library badge.

The frontend asks once per rendered page of results rather than once per card,
and the answer comes entirely from the local index — a search must never wait on
a library server.

Matches are split by format. Owning the audiobook is worth telling someone about
but must not stop them acquiring the ebook, so a cross-format holding lands in
`other_formats`, which the badge reports and the acquire button ignores.
"""

from __future__ import annotations

from typing import Any

from shelfmark.core.utils import is_audiobook
from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_EBOOK,
    LibraryIndexDB,
    LibraryMatch,
    get_library_index,
)
from shelfmark.library.matching import build_match_keys
from shelfmark.library.providers import get_providers
from shelfmark.library.scheduler import is_index_stale

# A page of search results is dozens of books; anything past this is either a
# bug or someone using the endpoint as a bulk library query.
MAX_LOOKUP_BOOKS = 200


def _item_payload(match: LibraryMatch) -> dict[str, Any]:
    return {
        "source": match.source,
        "media_type": match.media_type,
        "item_id": match.item_id,
        "library_id": match.library_id,
        "library_name": match.library_name,
        "title": match.title,
        "author": match.author,
        "asin": match.asin,
        "isbn13": match.isbn13,
    }


def _match_payload(matches: list[LibraryMatch], requested_media_type: str) -> dict[str, Any]:
    """Split matches into same-format holdings and everything else.

    "In library" is not "same edition" — a 2021 rip and a 2024 re-recording are
    both *The Locked Door* — so each entry carries its own title and identifiers
    rather than collapsing to a boolean.
    """
    ordered = sorted(matches, key=lambda m: (m.library_name, m.title))
    same = [m for m in ordered if m.media_type == requested_media_type]
    other = [m for m in ordered if m.media_type != requested_media_type]

    return {
        "libraries": sorted({m.library_name for m in same}),
        "items": [_item_payload(m) for m in same],
        "other_formats": [_item_payload(m) for m in other],
    }


def _requested_media_type(book: dict[str, Any]) -> str:
    return MEDIA_TYPE_AUDIOBOOK if is_audiobook(book.get("content_type")) else MEDIA_TYPE_EBOOK


def _source_states(library_index: LibraryIndexDB) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for provider in get_providers():
        if not provider.is_enabled():
            continue
        state = library_index.get_state(provider.source)
        states[provider.source] = {
            "enabled": True,
            "stale": is_index_stale(
                state.last_sync_at, interval_hours=provider.interval_hours()
            ),
            "last_sync_at": state.last_sync_at,
            "item_count": state.item_count,
        }
    return states


def _oldest_sync(states: dict[str, dict[str, Any]]) -> str | None:
    """Return the oldest sync time, or None if any enabled source never synced.

    Answers "everything is current as of at least this time", which is the only
    reading of a single timestamp that is not misleading across two sources.
    """
    stamps = [state["last_sync_at"] for state in states.values()]
    if not stamps or any(stamp is None for stamp in stamps):
        return None
    return min(stamps)


def lookup_books(books: list[Any], *, index: LibraryIndexDB | None = None) -> dict[str, Any]:
    """Look up which of `books` are already held, in their own format or another.

    Books with no usable key are skipped rather than matched loosely: matching on
    a title alone would mark all four *Housemaid* titles as owned the moment any
    one of them was.
    """
    library_index = index if index is not None else get_library_index()
    states = _source_states(library_index)

    result: dict[str, Any] = {
        "enabled": bool(states),
        "stale": any(state["stale"] for state in states.values()),
        "last_sync_at": _oldest_sync(states),
        "sources": states,
        "matches": {},
    }

    if not states or not isinstance(books, list):
        return result

    matches: dict[str, Any] = {}
    for book in books[:MAX_LOOKUP_BOOKS]:
        if not isinstance(book, dict):
            continue

        book_id = str(book.get("id") or "").strip()
        if not book_id or book_id in matches:
            continue

        keys = build_match_keys(
            book.get("title"),
            book.get("author"),
            asin=book.get("asin"),
            isbn=book.get("isbn_13") or book.get("isbn_10"),
        )
        if not keys:
            continue

        found = library_index.find_matches(keys)
        if found:
            matches[book_id] = _match_payload(found, _requested_media_type(book))

    result["matches"] = matches
    return result
```

Update the pre-existing tests in `tests/library/test_lookup.py` to the new item shape, adding `content_type` to their books and reading `["items"]`. Their assertions about *which* books match must not change.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/library -q && ruff check shelfmark`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A shelfmark tests
git commit -m "feat: split library matches by format so ebooks stop inheriting audio badges"
```

---

### Task 8: Move the lookup route

**Files:**
- Create: `shelfmark/library/routes.py`
- Modify: `shelfmark/audiobookshelf/routes.py`, `shelfmark/main.py:528-532`
- Test: `tests/library/test_routes.py` (extracted from `tests/audiobookshelf/test_routes.py`)

**Interfaces:**
- Consumes: `lookup_books` from Task 7.
- Produces: `shelfmark.library.routes.register_library_routes(app, *, resolve_auth_mode=None) -> None`.

- [ ] **Step 1: Create the route module**

`/api/library-matches` is no longer Audiobookshelf-specific. Move it, keeping the auth helpers and their reasoning verbatim:

```python
"""HTTP routes for the shared library index."""

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, session

from shelfmark.library.lookup import lookup_books

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue


def register_library_routes(
    app: Flask,
    *,
    resolve_auth_mode: Callable[[], str] | None = None,
) -> None:
    """Register the library-index endpoints on the Flask app.

    `resolve_auth_mode` is resolved per request rather than captured, so a
    runtime auth-mode change takes effect without re-registering routes.
    """

    def require_login() -> ResponseReturnValue | None:
        if resolve_auth_mode is not None and resolve_auth_mode() == "none":
            return None
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return None

    @app.route("/api/library-matches", methods=["POST"])
    def api_library_matches() -> ResponseReturnValue:
        """Report which of the posted books are already in a connected library.

        Open to every signed-in user, not just admins: the whole point is that a
        requester sees "you already have this" before asking for it.
        """
        unauthorized = require_login()
        if unauthorized is not None:
            return unauthorized

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object"}), 400

        books = payload.get("books", [])
        return jsonify(lookup_books(books if isinstance(books, list) else []))
```

- [ ] **Step 2: Strip the moved route from the ABS routes**

Delete `api_library_matches`, `require_login` and the `lookup_books` import from `shelfmark/audiobookshelf/routes.py`. It keeps `api_audiobook_destinations` and `require_admin`.

- [ ] **Step 3: Register it**

In `shelfmark/main.py`, beside the existing `register_audiobookshelf_routes` call:

```python
        from shelfmark.library.routes import register_library_routes

        register_library_routes(app, resolve_auth_mode=_resolve_auth_mode_for_routes)
```

- [ ] **Step 4: Move the route tests**

Move the `api_library_matches` test class out of `tests/audiobookshelf/test_routes.py` into a new `tests/library/test_routes.py`, changing only the import and the registration call.

- [ ] **Step 5: Run the suite**

Run: `pytest tests/library tests/audiobookshelf -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A shelfmark tests
git commit -m "refactor: move the library-matches route out of the ABS integration"
```

---

### Task 9: Grimmory settings tab

**Files:**
- Create: `shelfmark/grimmory/settings.py`
- Modify: `shelfmark/config/settings.py:1065-1092`, `shelfmark/config/booklore_settings.py`, `shelfmark/core/config.py:101-113`
- Test: `tests/grimmory/test_settings.py`, `tests/config/test_download_settings.py`

**Interfaces:**
- Consumes: `run_sync_now` from Task 4, `GrimmoryProvider` keys from Task 6.
- Produces: settings tab `grimmory`, declaring `BOOKLORE_ENABLED`, `BOOKLORE_HOST`, `BOOKLORE_USERNAME`, `BOOKLORE_PASSWORD`, `BOOKLORE_LIBRARY_INDEX_ENABLED`, `BOOKLORE_INDEX_INTERVAL_HOURS`.

- [ ] **Step 1: Write the failing test**

Create `tests/grimmory/test_settings.py`:

```python
"""Tests for the Grimmory settings tab."""

from shelfmark.core import settings_registry


def _fields(tab_name):
    tab = settings_registry.get_settings_tab(tab_name)
    assert tab is not None
    return {field.key: field for field in settings_registry.iter_value_fields(tab)}


class TestGrimmoryTab:
    def test_owns_the_connection_fields(self):
        import shelfmark.grimmory.settings  # noqa: F401

        keys = _fields("grimmory")
        assert {"BOOKLORE_HOST", "BOOKLORE_USERNAME", "BOOKLORE_PASSWORD"} <= set(keys)

    def test_downloads_no_longer_declares_them(self):
        import shelfmark.config.settings  # noqa: F401

        keys = _fields("downloads")
        assert "BOOKLORE_HOST" not in keys
        assert "BOOKLORE_USERNAME" not in keys
        assert "BOOKLORE_PASSWORD" not in keys

    def test_downloads_keeps_the_upload_destination_fields(self):
        import shelfmark.config.settings  # noqa: F401

        keys = _fields("downloads")
        assert {"BOOKLORE_DESTINATION", "BOOKLORE_LIBRARY_ID", "BOOKLORE_PATH_ID"} <= set(keys)

    def test_the_index_is_off_by_default_on_a_fresh_install(self):
        import shelfmark.grimmory.settings  # noqa: F401

        assert _fields("grimmory")["BOOKLORE_ENABLED"].default is False
        assert _fields("grimmory")["BOOKLORE_LIBRARY_INDEX_ENABLED"].default is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/grimmory/test_settings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shelfmark.grimmory.settings'`

- [ ] **Step 3: Write the tab**

Create `shelfmark/grimmory/settings.py`, mirroring `shelfmark/audiobookshelf/settings.py:290-410`:

```python
"""Grimmory connection and library-index settings."""

from typing import Any

from shelfmark.config.booklore_settings import check_booklore_connection
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)
from shelfmark.grimmory.client import BOOKLORE_DISPLAY_NAME
from shelfmark.library.index import SOURCE_GRIMMORY
from shelfmark.library.providers.grimmory import (
    LIBRARY_INDEX_ENABLED_KEY,
    LIBRARY_INDEX_INTERVAL_KEY,
)
from shelfmark.library.scheduler import run_sync_now


def sync_grimmory_library_index(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild the Grimmory slice of the index on demand."""
    result = run_sync_now(SOURCE_GRIMMORY)
    return {"success": result.success, "message": result.message}


@register_settings(
    name="grimmory",
    display_name=BOOKLORE_DISPLAY_NAME,
    icon="book-open",
    order=61,
)
def grimmory_settings() -> list[SettingsField]:
    """Grimmory connection settings."""
    return [
        HeadingField(
            key="grimmory_heading",
            title=f"{BOOKLORE_DISPLAY_NAME} Integration",
            description=(
                f"Connection to your {BOOKLORE_DISPLAY_NAME} server (formerly BookLore). "
                "Used to flag ebooks you already own, and to upload downloads when the "
                "book output mode is set to API upload."
            ),
            link_url="https://github.com/grimmory-tools/grimmory",
            link_text="grimmory-tools/grimmory",
        ),
        CheckboxField(
            key="BOOKLORE_ENABLED",
            label=f"Enable {BOOKLORE_DISPLAY_NAME} integration",
            default=False,
            description="Turn on duplicate detection against your ebook library.",
        ),
        TextField(
            key="BOOKLORE_HOST",
            label=f"{BOOKLORE_DISPLAY_NAME} URL",
            description=f"Base URL of your {BOOKLORE_DISPLAY_NAME} instance",
            placeholder="http://grimmory:6060",
            required=True,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        TextField(
            key="BOOKLORE_USERNAME",
            label="Username",
            description=(
                f"{BOOKLORE_DISPLAY_NAME} account username. What this account can see is "
                "what gets indexed — a non-admin only sees its assigned libraries."
            ),
            required=True,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        PasswordField(
            key="BOOKLORE_PASSWORD",
            label="Password",
            description=f"{BOOKLORE_DISPLAY_NAME} account password",
            required=True,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_grimmory",
            label="Test Connection",
            description="Verify the URL and credentials, and report what this account can see",
            style="primary",
            callback=check_booklore_connection,
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        HeadingField(
            key="grimmory_library_index_heading",
            title="Already In Library",
            description=(
                f"Shelfmark keeps a local index of your {BOOKLORE_DISPLAY_NAME} library and "
                "flags ebook search results you already own. The flag is advisory — "
                "re-acquiring a better edition is still one click away."
            ),
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        CheckboxField(
            key=LIBRARY_INDEX_ENABLED_KEY,
            label="Flag ebooks already in your library",
            default=True,
            description="Turn off to stop indexing and hide the badges.",
            show_when={"field": "BOOKLORE_ENABLED", "value": True},
        ),
        NumberField(
            key=LIBRARY_INDEX_INTERVAL_KEY,
            label="Refresh interval (hours)",
            default=1,
            min_value=1,
            max_value=168,
            description=(
                f"How often the index is rebuilt. Books added to {BOOKLORE_DISPLAY_NAME} "
                "since the last refresh will not be flagged yet."
            ),
            show_when={"field": LIBRARY_INDEX_ENABLED_KEY, "value": True},
        ),
        ActionButton(
            key="sync_grimmory_library_index",
            label="Sync Library Now",
            description="Rebuild the index immediately instead of waiting for the next refresh",
            callback=sync_grimmory_library_index,
            show_when={"field": LIBRARY_INDEX_ENABLED_KEY, "value": True},
        ),
    ]
```

Field classes come from `shelfmark.core.settings_registry`, the same module as `register_settings` — verified against `shelfmark/audiobookshelf/settings.py:12-23`. There is no separate `settings_fields` module.

- [ ] **Step 4: Remove the three fields from the Downloads tab**

Delete the `BOOKLORE_HOST`, `BOOKLORE_USERNAME` and `BOOKLORE_PASSWORD` fields from `shelfmark/config/settings.py` (lines ~1071-1092). Leave `BOOKLORE_DESTINATION`, `BOOKLORE_LIBRARY_ID`, `BOOKLORE_PATH_ID` and `test_booklore` in place, and reword the `booklore_heading` description:

```python
        HeadingField(
            key="booklore_heading",
            title=BOOKLORE_DISPLAY_NAME,
            description=(
                f"Upload books directly to {BOOKLORE_DISPLAY_NAME} (formerly BookLore) via "
                f"API. Set the server URL and credentials under Settings → "
                f"{BOOKLORE_DISPLAY_NAME}. Audiobooks always use folder mode."
            ),
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        ),
```

- [ ] **Step 5: Register the new settings module for loading**

Add `import_module("shelfmark.grimmory.settings")` to the block in `shelfmark/core/config.py:105-113`, so config resolves the moved keys even when nothing has imported the tab yet.

- [ ] **Step 6: Update the affected download-settings test**

`tests/config/test_download_settings.py:69-89` asserts the Downloads tab carries `BOOKLORE_HOST` with label "Grimmory URL". Move those assertions into `tests/grimmory/test_settings.py` against the `grimmory` tab. Keep the assertion that the `booklore` output-mode option is labelled "Grimmory (API)" on the Downloads tab.

- [ ] **Step 7: Run the suite**

Run: `pytest tests/grimmory tests/config -q && ruff check shelfmark`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A shelfmark tests
git commit -m "feat: give Grimmory its own settings tab, decoupled from output mode"
```

---

### Task 10: Config migrations

**Files:**
- Modify: `shelfmark/core/settings_registry.py:558-565`
- Test: `tests/config/test_grimmory_migration.py`

**Interfaces:**
- Consumes: the `grimmory` tab from Task 9.
- Produces: `migrate_grimmory_connection_tab() -> None` and `migrate_grimmory_enablement() -> None`, both called from `sync_env_to_config()`, in that order.

- [ ] **Step 1: Write the failing tests**

Create `tests/config/test_grimmory_migration.py`, following the fixture style of `tests/config/test_download_legacy_migration.py`:

```python
"""Tests for the Grimmory tab-move and enablement migrations."""

import json

import pytest

from shelfmark.core import settings_registry


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings_registry, "_get_config_dir", lambda: tmp_path)
    return tmp_path


# Non-core settings tabs live in {CONFIG_DIR}/plugins/<tab>.json, NOT settings/ —
# see _get_config_file_path (settings_registry.py:392). Only "general" and
# "search_mode" share the top-level settings.json.
def _write(config_dir, tab, values):
    (config_dir / "plugins").mkdir(parents=True, exist_ok=True)
    (config_dir / "plugins" / f"{tab}.json").write_text(json.dumps(values))


def _read(config_dir, tab):
    path = config_dir / "plugins" / f"{tab}.json"
    return json.loads(path.read_text()) if path.exists() else {}


class TestConnectionTabMove:
    def test_moves_the_three_connection_keys(self, config_dir):
        _write(
            config_dir,
            "downloads",
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
                "BOOKLORE_LIBRARY_ID": "7",
            },
        )

        settings_registry.migrate_grimmory_connection_tab()

        grimmory = _read(config_dir, "grimmory")
        assert grimmory["BOOKLORE_HOST"] == "http://grimmory:6060"
        assert grimmory["BOOKLORE_USERNAME"] == "shelfmark"
        assert grimmory["BOOKLORE_PASSWORD"] == "secret"

    def test_drops_them_from_downloads(self, config_dir):
        _write(config_dir, "downloads", {"BOOKLORE_HOST": "http://grimmory:6060"})

        settings_registry.migrate_grimmory_connection_tab()

        assert "BOOKLORE_HOST" not in _read(config_dir, "downloads")

    def test_leaves_the_upload_destination_keys_alone(self, config_dir):
        _write(
            config_dir,
            "downloads",
            {"BOOKLORE_LIBRARY_ID": "7", "BOOKLORE_PATH_ID": "3", "BOOKLORE_DESTINATION": "library"},
        )

        settings_registry.migrate_grimmory_connection_tab()

        downloads = _read(config_dir, "downloads")
        assert downloads["BOOKLORE_LIBRARY_ID"] == "7"
        assert downloads["BOOKLORE_PATH_ID"] == "3"
        assert downloads["BOOKLORE_DESTINATION"] == "library"

    def test_does_not_clobber_a_value_already_on_the_grimmory_tab(self, config_dir):
        _write(config_dir, "downloads", {"BOOKLORE_HOST": "http://old:6060"})
        _write(config_dir, "grimmory", {"BOOKLORE_HOST": "http://new:6060"})

        settings_registry.migrate_grimmory_connection_tab()

        assert _read(config_dir, "grimmory")["BOOKLORE_HOST"] == "http://new:6060"

    def test_is_a_no_op_when_nothing_is_configured(self, config_dir):
        settings_registry.migrate_grimmory_connection_tab()

        assert _read(config_dir, "grimmory") == {}


class TestEnablement:
    def test_switches_on_when_credentials_are_present(self, config_dir):
        _write(
            config_dir,
            "grimmory",
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
            },
        )

        settings_registry.migrate_grimmory_enablement()

        assert _read(config_dir, "grimmory")["BOOKLORE_ENABLED"] is True

    def test_stays_off_when_credentials_are_incomplete(self, config_dir):
        _write(config_dir, "grimmory", {"BOOKLORE_HOST": "http://grimmory:6060"})

        settings_registry.migrate_grimmory_enablement()

        assert "BOOKLORE_ENABLED" not in _read(config_dir, "grimmory")

    def test_does_not_re_enable_after_the_user_turns_it_off(self, config_dir):
        # Keyed off whether the value was ever persisted, not off its value —
        # otherwise unticking the box only lasts until the next restart.
        _write(
            config_dir,
            "grimmory",
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
                "BOOKLORE_ENABLED": False,
            },
        )

        settings_registry.migrate_grimmory_enablement()

        assert _read(config_dir, "grimmory")["BOOKLORE_ENABLED"] is False


class TestMigrationOrdering:
    def test_the_real_chain_moves_credentials_and_enables(self, config_dir):
        """The end-to-end guard on ordering.

        Testing the two migrations directly cannot catch this: run through the
        real sync_env_to_config(), initialize_default_configs() will have written
        BOOKLORE_ENABLED into a fresh grimmory.json before the migration looks,
        and enablement silently never happens.
        """
        _write(
            config_dir,
            "downloads",
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
            },
        )

        settings_registry.sync_env_to_config()

        grimmory = _read(config_dir, "grimmory")
        assert grimmory["BOOKLORE_HOST"] == "http://grimmory:6060"
        assert grimmory["BOOKLORE_ENABLED"] is True
        assert "BOOKLORE_HOST" not in _read(config_dir, "downloads")

    def test_a_fresh_install_stays_disabled(self, config_dir):
        settings_registry.sync_env_to_config()

        assert _read(config_dir, "grimmory").get("BOOKLORE_ENABLED") is not True
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/config/test_grimmory_migration.py -q`
Expected: FAIL with `AttributeError: module has no attribute 'migrate_grimmory_connection_tab'`

- [ ] **Step 3: Implement both migrations**

Add to `shelfmark/core/settings_registry.py`, beside the other `migrate_*` functions:

```python
_GRIMMORY_CONNECTION_KEYS = ("BOOKLORE_HOST", "BOOKLORE_USERNAME", "BOOKLORE_PASSWORD")


def migrate_grimmory_connection_tab() -> None:
    """Move the Grimmory connection keys from the downloads tab to its own.

    Settings are persisted per tab, so relocating these fields relocates where
    their values are read from. Without this an upgraded install would find them
    empty and lose both uploads and indexing. The destination keys
    (BOOKLORE_DESTINATION, BOOKLORE_LIBRARY_ID, BOOKLORE_PATH_ID) configure the
    upload target and stay on the downloads tab.
    """
    downloads = load_config_file("downloads")
    present = [key for key in _GRIMMORY_CONNECTION_KEYS if key in downloads]
    if not present:
        return

    grimmory = load_config_file("grimmory")
    moved = {key: downloads[key] for key in present if key not in grimmory}

    try:
        if moved:
            save_config_file("grimmory", moved)

        remaining = {k: v for k, v in downloads.items() if k not in _GRIMMORY_CONNECTION_KEYS}
        _ensure_config_dir("downloads")
        with _get_config_file_path("downloads").open("w") as f:
            json.dump(remaining, f, indent=2)

        logger.info("Moved %d Grimmory connection settings to their own tab", len(present))
    except Exception:
        logger.exception("Failed to move Grimmory connection settings")


def migrate_grimmory_enablement() -> None:
    """Switch the Grimmory integration on where it is already configured.

    Anyone who has filled in a host, username and password has said what they
    want; making them find a checkbox to get badges hides the feature behind a
    setting nobody knows to look for.

    Keyed off whether BOOKLORE_ENABLED was ever persisted rather than off its
    value — keying off the value would produce a setting that silently turns
    itself back on at every boot.

    This only works if the migration runs BEFORE initialize_default_configs(),
    which writes every field default into a missing tab file. Called after it,
    "was it ever persisted?" is always true and this function does nothing.
    """
    grimmory = load_config_file("grimmory")
    if "BOOKLORE_ENABLED" in grimmory:
        return

    if not all(str(grimmory.get(key, "") or "").strip() for key in _GRIMMORY_CONNECTION_KEYS):
        return

    try:
        save_config_file("grimmory", {"BOOKLORE_ENABLED": True})
        logger.info("Enabled the Grimmory integration for an already-configured install")
    except Exception:
        logger.exception("Failed to enable the Grimmory integration")
```

`save_config_file` merges into the existing file rather than replacing it (`shelfmark/core/settings_registry.py:436-445`), which is why the additive writes above are safe. The `downloads` rewrite deliberately bypasses it and writes the file directly, because merging cannot *remove* the keys being moved out.

- [ ] **Step 4: Wire them in — before `initialize_default_configs()`, not with the other migrations**

These two do **not** go in the migration block at the end of `sync_env_to_config()`. They must run *before* the `initialize_default_configs()` call at `shelfmark/core/settings_registry.py:533`, because that function creates any missing tab file populated with every non-`None` field default — including `BOOKLORE_ENABLED: false`. Run after it and the enablement check can no longer tell a fresh default from a user's decision, so it never fires.

In `sync_env_to_config()`, immediately before `initialize_default_configs()`:

```python
    # Ahead of initialize_default_configs(): it would create grimmory.json with
    # BOOKLORE_ENABLED already defaulted, and "never persisted" would stop being
    # a usable signal. Within this pair, the tab move runs first because the
    # enablement check reads the credentials it relocates.
    migrate_grimmory_connection_tab()
    migrate_grimmory_enablement()

    # Initialize default configs first (for fresh installs)
    initialize_default_configs()
```

A consequence worth knowing: on an upgrade the tab move creates `grimmory.json`, so `initialize_default_configs()` then skips that file and never writes `BOOKLORE_LIBRARY_INDEX_ENABLED` or `BOOKLORE_INDEX_INTERVAL_HOURS` into it. That is harmless — `get_setting_value` falls back to the field default when a key is absent — but do not "fix" it by reordering.

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/config -q && ruff check shelfmark`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A shelfmark tests
git commit -m "feat: migrate Grimmory settings to their own tab and enable where configured"
```

---

### Task 11: Frontend lookup payload

**Files:**
- Modify: `src/frontend/src/utils/libraryMatches.ts`
- Test: `src/frontend/src/tests/libraryMatches.test.ts`

**Interfaces:**
- Consumes: the response shape from Task 7.
- Produces:
  - `LibraryMatchItem` gains `source: string`, `media_type: string`, `isbn13: string`
  - `LibraryMatch` gains `other_formats: LibraryMatchItem[]`
  - `LibraryLookupBook` gains `isbn_10?`, `isbn_13?`, `content_type?`
  - `buildLibraryLookupPayload(books: Book[], defaultContentType?: string): LibraryLookupBook[]`
  - `singleBookLookup(id, title, author, asin?, isbn?): Book[]`
  - `isHeldInFormat(match: LibraryMatch | undefined): boolean`

- [ ] **Step 1: Write the failing tests**

Append to `src/frontend/src/tests/libraryMatches.test.ts`:

```typescript
describe('buildLibraryLookupPayload with ISBNs', () => {
  it('forwards both ISBN spellings and the content type', () => {
    const payload = buildLibraryLookupPayload([
      {
        id: 'b1',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        isbn_13: '9780593135204',
        content_type: 'ebook',
      },
    ]);

    expect(payload[0]).toEqual({
      id: 'b1',
      title: 'The Housemaid',
      author: 'Freida McFadden',
      isbn_13: '9780593135204',
      content_type: 'ebook',
    });
  });

  it('keeps a book that has only an ISBN', () => {
    const payload = buildLibraryLookupPayload([{ id: 'b1', isbn_10: '0306406152' }]);

    expect(payload).toHaveLength(1);
  });

  it('still drops a book with no title, author, ASIN or ISBN', () => {
    expect(buildLibraryLookupPayload([{ id: 'b1', title: 'Half a key' }])).toEqual([]);
  });

  it('falls back to the surface content type when a book carries none', () => {
    const payload = buildLibraryLookupPayload([{ id: 'b1', isbn_10: '0306406152' }], 'audiobook');

    expect(payload[0].content_type).toBe('audiobook');
  });
});

describe('booksLookupSignature', () => {
  it('changes when the content type changes, so a format switch refetches', () => {
    const book = { id: 'b1', title: 'T', author: 'A' };

    expect(booksLookupSignature([book], 'ebook')).not.toBe(
      booksLookupSignature([book], 'audiobook'),
    );
  });

  it('changes when an ISBN is added', () => {
    const base = { id: 'b1', title: 'T', author: 'A' };

    expect(booksLookupSignature([base])).not.toBe(
      booksLookupSignature([{ ...base, isbn_13: '9780593135204' }]),
    );
  });
});

describe('isHeldInFormat', () => {
  const held: LibraryMatch = {
    libraries: ['Ebooks'],
    items: [
      {
        source: 'grimmory',
        media_type: 'ebook',
        item_id: '1',
        library_id: '7',
        library_name: 'Ebooks',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        asin: '',
        isbn13: '9780593135204',
      },
    ],
    other_formats: [],
  };
  const otherOnly: LibraryMatch = { ...held, items: [], other_formats: held.items };

  it('is true when the same format is held', () => {
    expect(isHeldInFormat(held)).toBe(true);
  });

  it('is false when only another format is held', () => {
    expect(isHeldInFormat(otherOnly)).toBe(false);
  });

  it('is false when there is no match at all', () => {
    expect(isHeldInFormat(undefined)).toBe(false);
  });
});

describe('applyInLibraryLock', () => {
  it('does not lock acquisition when only another format is held', () => {
    const otherOnly: LibraryMatch = {
      libraries: [],
      items: [],
      other_formats: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: '',
          isbn13: '',
        },
      ],
    };

    expect(applyInLibraryLock({ state: 'download', text: 'Download' }, isHeldInFormat(otherOnly)))
      .toEqual({ state: 'download', text: 'Download' });
  });
});

describe('libraryMatchTooltip', () => {
  it('names the other format without implying ownership of this one', () => {
    const otherOnly: LibraryMatch = {
      libraries: [],
      items: [],
      other_formats: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: 'B09XYZ1234',
          isbn13: '',
        },
      ],
    };

    expect(libraryMatchTooltip(otherOnly)).toContain('as an audiobook');
    expect(libraryMatchTooltip(otherOnly)).not.toContain('Already in your library: ');
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run from `src/frontend/`: `npm run test:unit`
Expected: FAIL — `isHeldInFormat` is not exported.

- [ ] **Step 3: Implement**

Replace the relevant parts of `src/frontend/src/utils/libraryMatches.ts`:

```typescript
interface LibraryMatchItem {
  source: string;
  media_type: string;
  item_id: string;
  library_id: string;
  library_name: string;
  title: string;
  author: string;
  asin: string;
  isbn13: string;
}

export interface LibraryMatch {
  libraries: string[];
  /** Holdings in the format being browsed. These drive the badge and the lock. */
  items: LibraryMatchItem[];
  /** Holdings in another format. Worth mentioning, never worth blocking on. */
  other_formats: LibraryMatchItem[];
}

export interface LibraryMatchesResponse {
  enabled: boolean;
  stale: boolean;
  last_sync_at: string | null;
  sources: Record<
    string,
    { enabled: boolean; stale: boolean; last_sync_at: string | null; item_count: number }
  >;
  matches: Record<string, LibraryMatch>;
}

export interface LibraryLookupBook {
  id: string;
  title?: string;
  author?: string;
  asin?: string;
  isbn_10?: string;
  isbn_13?: string;
  content_type?: string;
}

/**
 * Reduce books to the fields the matcher uses, dropping any that cannot match.
 *
 * A book needs a title and an author, or an ASIN, or an ISBN. Anything less has
 * no key, so asking about it would only cost a round trip. An ASIN or ISBN alone
 * is enough because each is a complete identity, which half a title+author key
 * is not.
 *
 * `defaultContentType` fills in for books that carry no content type of their
 * own, so a surface showing one format classifies its results correctly even
 * when the metadata provider omits the field.
 */
export const buildLibraryLookupPayload = (
  books: Book[],
  defaultContentType?: string,
): LibraryLookupBook[] => {
  const seen = new Set<string>();
  const payload: LibraryLookupBook[] = [];

  for (const book of books) {
    const id = (book.id ?? '').trim();
    const title = (book.title ?? '').trim();
    const author = (book.author ?? '').trim();
    const asin = (book.asin ?? '').trim();
    const isbn13 = (book.isbn_13 ?? '').trim();
    const isbn10 = (book.isbn_10 ?? '').trim();
    const contentType = (book.content_type ?? defaultContentType ?? '').trim();
    if (!id || seen.has(id)) continue;
    if (!asin && !isbn13 && !isbn10 && (!title || !author)) continue;

    seen.add(id);
    const entry: LibraryLookupBook = { id };
    if (title) entry.title = title;
    if (author) entry.author = author;
    if (asin) entry.asin = asin;
    if (isbn13) entry.isbn_13 = isbn13;
    if (isbn10) entry.isbn_10 = isbn10;
    if (contentType) entry.content_type = contentType;
    payload.push(entry);
  }

  return payload;
};

/** A stable key for a book list, so scrolling a result set refetches only once. */
export const booksLookupSignature = (books: Book[], defaultContentType?: string): string =>
  buildLibraryLookupPayload(books, defaultContentType)
    .map((book) => [book.id, book.asin ?? '', book.isbn_13 ?? book.isbn_10 ?? '', book.content_type ?? ''].join('#'))
    .join(',');

/** Whether the book is held in the very format being browsed. */
export const isHeldInFormat = (match: LibraryMatch | undefined): boolean =>
  (match?.items.length ?? 0) > 0;

/**
 * Full tooltip text, one line per held edition.
 *
 * Names the edition but never the library holding it: which shelf a book sits on
 * is the operator's filing concern. Cross-format holdings are labelled as such,
 * so "you have the audiobook" never reads as "you have this".
 */
export const libraryMatchTooltip = (match: LibraryMatch): string => {
  const describe = (item: LibraryMatchItem) => {
    const asin = item.asin ? ` (ASIN ${item.asin})` : '';
    return `${item.title} — ${item.author}${asin}`;
  };

  const lines = match.items.map(
    (item, index) => `${index === 0 ? 'Already in your library: ' : ''}${describe(item)}`,
  );

  lines.push(
    ...match.other_formats.map(
      (item, index) =>
        `${index === 0 ? `Also in your library as ${item.media_type === 'audiobook' ? 'an audiobook' : 'an ebook'}: ` : ''}${describe(item)}`,
    ),
  );

  return lines.join('\n');
};
```

Replace `singleBookLookup` so the one-book surfaces can state their format. Every caller is updated in Task 12 Step 5:

```typescript
/**
 * Wrap a single book for the surfaces that ask about one — the details modal,
 * the request form and the approve panel.
 *
 * `contentType` is not optional in practice: the backend reads a missing content
 * type as "ebook", so an audiobook surface that omits it would match against the
 * ebook library and file its real audiobook holding under other_formats.
 *
 * The ISBN is stored as `isbn_13` regardless of which spelling it came in as —
 * the backend canonicalizes ISBN-10 to ISBN-13 anyway, so picking one field here
 * saves callers deciding which they hold.
 *
 * Returns a shared empty array when there is no usable key, so callers can pass
 * the result straight into the lookup hook without churning its dependency.
 */
export const singleBookLookup = (
  id: string,
  title: string | undefined,
  author: string | undefined,
  asin?: string,
  isbn?: string,
  contentType?: string,
): Book[] => {
  const trimmedTitle = (title ?? '').trim();
  const trimmedAuthor = (author ?? '').trim();
  const trimmedAsin = (asin ?? '').trim();
  const trimmedIsbn = (isbn ?? '').trim();
  if (!trimmedAsin && !trimmedIsbn && (!trimmedTitle || !trimmedAuthor)) return NO_BOOKS;

  return [
    {
      id,
      title: trimmedTitle,
      author: trimmedAuthor,
      asin: trimmedAsin || undefined,
      isbn_13: trimmedIsbn || undefined,
      content_type: contentType || undefined,
    },
  ];
};
```

Add a test that `singleBookLookup('x', 'T', 'A', undefined, undefined, 'audiobook')` produces a payload whose `content_type` is `'audiobook'`.

- [ ] **Step 4: Run to verify they pass**

Run from `src/frontend/`: `npm run test:unit && npm run typecheck && npm run lint`
Expected: PASS. Consumers still passing the old `applyInLibraryLock(state, Boolean(match))` will type-check; Task 12 switches them to `isHeldInFormat`.

- [ ] **Step 5: Commit**

```bash
git add src/frontend/src/utils/libraryMatches.ts src/frontend/src/tests/libraryMatches.test.ts
git commit -m "feat: carry ISBNs and format through the library lookup payload"
```

---

### Task 12: Frontend badge and wiring

**Files:**
- Modify: `src/frontend/src/components/shared/InLibraryBadge.tsx`
- Modify: `src/frontend/src/hooks/useLibraryMatches.ts`
- Modify: `src/frontend/src/components/ResultsSection.tsx:42-77`
- Modify: `src/frontend/src/App.tsx:2626`
- Modify: `src/frontend/src/components/DetailsModal.tsx`, `DiscoverSection.tsx`, `BookActionButton.tsx`, `RequestConfirmationModal.tsx`, `activity/ActivityCard.tsx`, `resultsViews/{CardView,CompactView,ListView}.tsx` — swap `Boolean(match)` for `isHeldInFormat(match)` at every `applyInLibraryLock` call site.

**Interfaces:**
- Consumes: `isHeldInFormat`, `LibraryMatch`, `buildLibraryLookupPayload` from Task 11.
- Produces: `useLibraryMatches(books: Book[], defaultContentType?: string)`; `ResultsSection` prop `defaultContentType?: string` replacing `showInLibraryBadges`.

- [ ] **Step 1: Give the badge a cross-format appearance**

`InLibraryBadge` already has a `variant` prop for placement (`'inline' | 'overlay'`), so the ownership state is derived from the match rather than passed — two props that must agree is a bug waiting to happen.

```typescript
export function InLibraryBadge({ match, className = '', variant = 'inline' }: InLibraryBadgeProps) {
  if (!match) return null;
  if (match.items.length === 0 && match.other_formats.length === 0) return null;

  const held = match.items.length > 0;
  const label = libraryMatchTooltip(match);

  // A cross-format holding is not the same claim as owning this edition, and it
  // does not lock the button, so it must not wear the same badge. Muted and
  // hollow reads as "related" where solid reads as "you have this".
  const palette = held
    ? variant === 'overlay'
      ? 'border-emerald-700 bg-emerald-600 text-white shadow-md'
      : 'border-emerald-600/40 bg-emerald-600/15 text-emerald-700 dark:text-emerald-300'
    : variant === 'overlay'
      ? 'border-slate-400 bg-slate-700/80 text-slate-100 shadow-md'
      : 'border-slate-400/40 bg-slate-400/15 text-slate-600 dark:text-slate-300';

  const icon = held ? (
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
  ) : (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2.5}
      d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
    />
  );

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full border p-1 ${palette} ${className}`}
      title={label}
      aria-label={label}
      role="img"
    >
      <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        {icon}
      </svg>
    </span>
  );
}
```

- [ ] **Step 2: Thread the default content type through the hook**

```typescript
export const useLibraryMatches = (
  books: Book[],
  defaultContentType?: string,
): Record<string, LibraryMatch> => {
  const [matches, setMatches] = useState<Record<string, LibraryMatch>>({});
  const signature = booksLookupSignature(books, defaultContentType);

  useDependencyEffect(() => {
    if (!signature) {
      setMatches({});
      return undefined;
    }

    let cancelled = false;
    void getLibraryMatches(buildLibraryLookupPayload(books, defaultContentType))
      .then((response) => {
        if (!cancelled) {
          setMatches(response.enabled ? response.matches : {});
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMatches({});
        }
      });

    return () => {
      cancelled = true;
    };
    // Keyed by the signature only: re-running on the array identity would
    // refetch on every parent render.
  }, [signature]);

  return matches;
};
```

- [ ] **Step 3: Replace the suppression flag**

In `ResultsSection.tsx`, delete `showInLibraryBadges` and `NO_LOOKUP_BOOKS`, and replace the prop and hook call:

```typescript
  /**
   * Content type to assume for books that carry none of their own. The index is
   * format-aware, so every surface can ask — an ebook result will not inherit a
   * badge from an audiobook-only holding.
   */
  defaultContentType?: string;
```

```typescript
  const libraryMatches = useLibraryMatches(books, defaultContentType);
```

In `App.tsx:2626`, replace the boolean with the content type:

```tsx
            defaultContentType={effectiveContentType}
```

- [ ] **Step 4: Switch every lock decision — there are seven, and only two are `applyInLibraryLock` calls**

Grepping for `applyInLibraryLock` finds only two sites and would miss five. The result views never call it; they pass a boolean into `BookActionButton`, which calls it for them. Every one of these must change, or a cross-format-only match still locks the button and the feature does nothing.

Direct calls (2):

- `src/frontend/src/components/DetailsModal.tsx:108` — `applyInLibraryLock(buttonState, Boolean(libraryMatch))` → `applyInLibraryLock(buttonState, isHeldInFormat(libraryMatch))`
- `src/frontend/src/components/BookActionButton.tsx:44` — takes `isInLibrary` as a prop; leave the call as-is and fix the callers below.

Boolean props into `BookActionButton` (5):

- `src/frontend/src/components/resultsViews/CompactView.tsx:301` and `:315` — `isInLibrary={Boolean(libraryMatch)}` → `isInLibrary={isHeldInFormat(libraryMatch)}`
- `src/frontend/src/components/resultsViews/CardView.tsx:259` and `:274` — same change
- `src/frontend/src/components/resultsViews/ListView.tsx:365` — `isInLibrary={Boolean(libraryMatches[book.id])}` → `isInLibrary={isHeldInFormat(libraryMatches[book.id])}`

Import `isHeldInFormat` from `../../utils/libraryMatches` in the result views and `../utils/libraryMatches` in `DetailsModal`.

Verify none were missed:

```bash
grep -rn "isInLibrary={Boolean\|applyInLibraryLock(.*Boolean(" src/frontend/src
```
Expected: no output.

- [ ] **Step 5: Give the single-book surfaces their format and ISBNs**

Three surfaces build their own one-book lookup and none currently pass a content type, so the backend would default them all to ebook — an audiobook request would lock against a Grimmory ebook and file the real Audiobookshelf holding under `other_formats`.

- `src/frontend/src/components/DetailsModal.tsx:78` — `singleBookLookup(\`details-${book?.id ?? ''}\`, book?.title, book?.author, book?.asin, book?.isbn_13 ?? book?.isbn_10, book?.content_type)`
- `src/frontend/src/components/RequestConfirmationModal.tsx:185` — pass `preview.isbn_13 ?? preview.isbn_10` and `preview.content_type`
- `src/frontend/src/components/activity/ActivityCard.tsx:313` — pass the record's ISBN and `content_type`

Add a test that an audiobook detail view reports the Audiobookshelf holding in `items` rather than `other_formats`.

- [ ] **Step 6: Verify**

Run from `src/frontend/`: `npm run test:unit && npm run typecheck && npm run lint`
Expected: PASS, and no remaining reference to `showInLibraryBadges`:

```bash
grep -rn "showInLibraryBadges" src/frontend/src
```
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add -A src/frontend/src
git commit -m "feat: badge ebooks from Grimmory and mark cross-format holdings"
```

---

### Task 13: Documentation and end-to-end verification

**Files:**
- Modify: `docs/environment-variables.md`, `readme.md`

- [ ] **Step 1: Document the new settings**

Add `BOOKLORE_ENABLED`, `BOOKLORE_LIBRARY_INDEX_ENABLED` and `BOOKLORE_INDEX_INTERVAL_HOURS` to `docs/environment-variables.md` in the same table style as the existing `BOOKLORE_*` rows, and note that `BOOKLORE_HOST`, `BOOKLORE_USERNAME` and `BOOKLORE_PASSWORD` now live under the Grimmory settings tab and apply to both uploads and library indexing.

In `readme.md`, extend the library-link bullet (line ~126) to mention that Grimmory can also flag ebooks already in your library.

- [ ] **Step 2: Run the whole suite**

```bash
pytest -q
cd src/frontend && npm run test:unit && npm run typecheck && npm run lint && npm run build
```
Expected: all PASS.

- [ ] **Step 3: Confirm no dead references remain**

```bash
grep -rn "audiobookshelf.library_index\|audiobookshelf.library_sync\|audiobookshelf.library_lookup\|audiobookshelf.matching" shelfmark tests
grep -rn "audiobookshelf_index.db" shelfmark tests | grep -v "_LEGACY_DB_NAME"
```
Expected: no output from either. The second grep excludes `_LEGACY_DB_NAME` in `shelfmark/library/index.py`, which is the one intentional remaining mention — it is what performs the cleanup.

- [ ] **Step 4: Manual smoke test**

Start the app with Grimmory credentials configured. Verify in order:

1. Settings → Grimmory shows the connection fields with the values that were previously under Downloads, and `BOOKLORE_ENABLED` is already ticked.
2. Test Connection reports the library and book counts.
3. Sync Library Now reports a non-zero indexed count.
4. An ebook search shows the solid badge on a book you hold in Grimmory, and its acquire button reads "In library".
5. A book you hold only as an audiobook shows the muted badge on an ebook surface, with a tooltip naming the audiobook, and its acquire button is still active.
6. `BOOKS_OUTPUT_MODE=booklore` uploads still complete.

- [ ] **Step 5: Commit**

```bash
git add docs readme.md
git commit -m "docs: document Grimmory library indexing"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: shared core (1, 3, 4, 7), ISBN matching (2), Grimmory client and coverage caveat (5), provider and `media_type` derivation (6), lookup payload and aggregates (7), route (8), settings decoupling (9), both migrations (10), frontend (11, 12), rollout and docs (13). The legacy-DB unlink lands in Task 3 Step 4.

**Naming consistency.** `replace_items(source, items)`, `get_state(source)`, `record_failure(source, message)` and `find_matches(keys)` are used identically in Tasks 3, 4, 6 and 7. `LibraryItem` and `LibraryMatch` field lists match between the dataclasses in Task 3 and every construction site. `isHeldInFormat` is defined in Task 11 and consumed in Task 12. `LIBRARY_INDEX_ENABLED_KEY` is defined per provider module, so the ABS and Grimmory constants never collide.

**Verified while writing.** Field classes are exported from `shelfmark.core.settings_registry` alongside `register_settings` (there is no `settings_fields` module), and `save_config_file` merges rather than replaces — both are now stated correctly in Tasks 9 and 10.

**Corrections from the Codex review (all verified against the codebase before applying).**

| Was wrong | Now |
|---|---|
| Enablement migration placed with the others at registry:558 | Moved ahead of `initialize_default_configs()` (registry:533), which writes `BOOKLORE_ENABLED: false` into a missing tab file and would make "never persisted" always true |
| Migration tests seeded `{CONFIG_DIR}/settings/` | Corrected to `{CONFIG_DIR}/plugins/<tab>.json`; only `general` and `search_mode` use the top-level `settings.json` |
| Task 12 said to grep for `applyInLibraryLock` | There are only two such calls; five more lock decisions ride on `isInLibrary={Boolean(...)}` props in the result views. All seven are now listed explicitly |
| `singleBookLookup` took no content type | Now takes `isbn` and `contentType`; its three callers are updated in Task 12 Step 5, without which every one-book surface would classify as ebook |
| `replace_items` sat outside the `try` in `sync_provider` | Inside it, so a locked or full database returns a result instead of taking down the settings button and the scheduler |
| Test Connection never gained a book count | Task 5 Step 4 adds it, with a test |
| No pagination coverage for `GrimmoryProvider` | Task 6 adds page-progression, page-cap and single-page tests |
| Task 13's legacy-DB grep contradicted Task 3 | Excludes the intentional `_LEGACY_DB_NAME` definition |
| Legacy cleanup left `-wal`/`-shm` orphans | Removes all three files |

Two review points were resolved as spec changes rather than plan changes: Grimmory `AUDIOBOOK` entries now legitimately match audiobook searches (the spec's out-of-scope line was too broad), and the badge derives its ownership state from the match rather than a second `variant` prop.

**One thing the implementer should watch.** Task 5 renames `_parse_int`/`_parse_destination` to public names during the move. If any test imports the underscored names, update it in the same commit.

**Known ordering constraint.** Task 4 Step 9 temporarily drops `GrimmoryProvider` from `get_providers()` so the tree stays green; Task 6 Step 4 restores it. Skipping Task 6 would silently leave Grimmory unindexed with no test failure, so the two tasks must not be separated.
