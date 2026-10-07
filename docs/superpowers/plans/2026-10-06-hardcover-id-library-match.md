# Hardcover-ID Library Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grimmory books carrying a verified Hardcover ID badge as "In library" against Hardcover search results, without new false "owned" claims.

**Architecture:** A `hardcover:<id>` match key joins the existing ASIN/ISBN/title keys in the generic `library_item_keys` table — indexed from Grimmory's per-book `hardcoverBookId`, looked up from a Hardcover result's `provider_id`. Author keys gain a reversed two-token ordering; medium labels stop demoting real holdings; a conflicting Hardcover ID vetoes a weaker match; combined mode locks only when both formats are held.

**Tech Stack:** Python 3.14 (Flask backend, SQLite index, `requests`), pytest; React + TypeScript frontend, vitest; Ruff 0.16.5, BasedPyright, oxlint/oxfmt.

**Spec:** `docs/superpowers/specs/2026-10-06-hardcover-id-library-match-design.md` — read it before any task.

## Global Constraints

- **A false "already in library" is worse than a missed badge** (`shelfmark/library/matching.py` module docstring) — every new key path must keep that.
- Only an all-ASCII-digit Hardcover ID makes a key; the key prefix is exactly `hardcover:`.
- Hardcover IDs are read from a lookup only when `provider` is Hardcover (compared case-insensitively); other providers' IDs never make a key.
- Author reversal: only for a normalized name of exactly two tokens whose raw text has no comma.
- Medium labels ignored by the other-edition check: exactly `light novel`, `novel`, `ln` (plus the existing `abridged`, `unabridged`). `manga`, `graphic novel`, dramatized/full-cast markers still demote.
- Grimmory sync: any per-book detail failure fails the whole sync (previous index kept); one re-login on a 401, a second 401 fails; reaching the page cap before the last page fails.
- `LibraryItem.hardcover_id` and `LibraryMatch.hardcover_id` are new **last** fields defaulting to `""`; no schema change, nothing added to the API response.
- Python: `bare except A, B:` (PEP 758) is valid here — do not "fix" it.
- Gates before any commit: `uv run pytest tests/library tests/grimmory -q` (backend tasks), `cd src/frontend && npm run test:unit` (frontend tasks); before release `make python-checks python-test frontend-checks frontend-test`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Provider spelled differently** (`"Hardcover"`, `"HARDCOVER"`) → still a Hardcover lookup; `"openlibrary"` with a numeric id → no Hardcover key. Test in Task 4.
2. **Non-digit or padded IDs** (`"hc-123"`, `" 886465 "`, `"12a"`, `True`, `0`-free unicode digits like `"²"`) → only clean ASCII digits (after strip) make a key. Test in Task 1.
3. **Empty Grimmory library** (`content: []`) → sync succeeds with zero items and makes no detail reads. Test in Task 3.
4. **The same book id listed twice across pages** → indexed once (no duplicate rows, no crash). Test in Task 3.
5. **Combined mode with nothing held, only one format held, or the other format held only as a different edition** → never locked. Test in Task 5.

## File Structure

| File | Change |
|---|---|
| `shelfmark/library/matching.py` | author reversal, medium labels, `hardcover_match_key`, `build_match_keys(hardcover_id=)` |
| `shelfmark/grimmory/client.py` | `BookloreAuthError`, `get_book`, nested `page.totalPages` |
| `shelfmark/library/providers/grimmory.py` | `hardcover_id` extraction; per-book detail reads; re-login once; page-cap failure |
| `shelfmark/library/index.py` | `LibraryItem.hardcover_id`, `LibraryMatch.hardcover_id`; key in `replace_items`; Hardcover key read back in `find_matches` |
| `shelfmark/library/lookup.py` | Hardcover key from `provider_id`; conflict veto |
| `src/frontend/src/utils/libraryMatches.ts` | payload/signature/`singleBookLookup` identity; `hardcoverIdentity`; `isLockedInLibrary` |
| `src/frontend/src/components/resultsViews/{CardView,CompactView,ListView}.tsx`, `ResultsSection.tsx`, `App.tsx` | `combinedMode` → `isLockedInLibrary` |
| `src/frontend/src/components/{DetailsModal,RequestConfirmationModal}.tsx`, `activity/ActivityCard.tsx` | pass identity to `singleBookLookup`; memo deps |
| Tests | `tests/library/test_matching.py`, `tests/grimmory/test_client.py`, `tests/library/test_providers_grimmory.py`, `tests/library/test_index.py`, `tests/library/test_lookup.py`, `src/frontend/src/tests/libraryMatches.test.ts` |

---
### Task 1: Matching rules — author reversal, medium labels, Hardcover key

**Files:**
- Modify: `shelfmark/library/matching.py` (`author_match_keys`, `_NON_EDITION_QUALIFIERS`, `build_match_keys`; new `HARDCOVER_KEY_PREFIX`, `hardcover_match_key`)
- Test: `tests/library/test_matching.py`

**Interfaces:**
- Produces: `HARDCOVER_KEY_PREFIX = "hardcover:"`; `hardcover_match_key(value: object) -> str` (`""` when unusable); `build_match_keys(title, author, subtitle=None, asin=None, isbn=None, hardcover_id=None) -> set[str]`; `author_match_keys` now also yields the reversed two-token order.

- [ ] **Step 1: Update the three exact-set assertions the rule change affects, and add the new tests**

In `tests/library/test_matching.py`, change these existing assertions (the reversed order is now a deliberate extra key):

```python
    def test_yields_the_plain_normalized_name(self):
        assert author_match_keys("Freida McFadden") == {"freida mcfadden", "mcfadden freida"}
```

```python
    def test_adds_the_asin_key_alongside_the_title_keys(self):
        keys = build_match_keys("The Housemaid", "Freida McFadden", asin="B0BSHZ1234")

        assert keys == {
            "housemaid|freida mcfadden",
            "housemaid|mcfadden freida",
            "asin:B0BSHZ1234",
        }
```

```python
    def test_a_malformed_asin_is_simply_ignored(self):
        keys = build_match_keys("The Housemaid", "Freida McFadden", asin="N/A")

        assert keys == {"housemaid|freida mcfadden", "housemaid|mcfadden freida"}
```

Append these classes to the end of the file (add `HARDCOVER_KEY_PREFIX` and `hardcover_match_key` to the `from shelfmark.library.matching import (...)` block):

```python
class TestAuthorReversal:
    """Grimmory stores light-novel authors surname-first with no comma
    ("Maruyama Kugane"); Hardcover has "Kugane Maruyama"."""

    def test_two_token_name_matches_either_way_round(self):
        assert author_match_keys("Maruyama Kugane") & author_match_keys("Kugane Maruyama")

    def test_reversal_only_meets_the_same_title(self):
        overlord = build_match_keys("Overlord, Vol. 1", "Maruyama Kugane")
        other = build_match_keys("Overlord, Vol. 2", "Kugane Maruyama")

        assert not overlord & other

    def test_a_different_author_with_the_same_title_shares_no_key(self):
        assert not build_match_keys("Overlord, Vol. 1", "Kugane Maruyama") & build_match_keys(
            "Overlord, Vol. 1", "Satoshi Oshio"
        )

    def test_three_tokens_are_not_reversed(self):
        assert author_match_keys("Rifujin na Magonote") == {"rifujin na magonote"}

    def test_a_comma_keeps_the_existing_inversion_only(self):
        assert author_match_keys("Maruyama, Kugane") == {"maruyama kugane", "kugane maruyama"}


class TestMediumLabels:
    """A format label on one title only is not a different edition."""

    def test_light_novel_novel_and_ln_are_ignored(self):
        for title in ("Overlord (Light Novel), Vol. 1", "Overlord (Novel)", "Overlord [LN]"):
            assert edition_qualifiers(title) == frozenset(), title

    def test_manga_still_marks_a_different_edition(self):
        assert edition_qualifiers("Overlord (Manga) Vol. 1") == frozenset({"manga"})

    def test_graphic_novel_still_marks_a_different_edition(self):
        assert edition_qualifiers("Overlord (Graphic Novel)") == frozenset({"graphic novel"})


class TestHardcoverKey:
    """A Hardcover book id names a work; only a clean id becomes a key."""

    def test_digits_make_a_namespaced_key(self):
        assert hardcover_match_key("886465") == f"{HARDCOVER_KEY_PREFIX}886465"
        assert hardcover_match_key(886465) == "hardcover:886465"

    def test_surrounding_whitespace_is_ignored(self):
        assert hardcover_match_key(" 886465 ") == "hardcover:886465"

    def test_anything_else_is_unusable(self):
        # Review Focus #2
        for value in ("hc-123", "12a", "", "   ", None, True, "²", "886 465", 1.5):
            assert hardcover_match_key(value) == "", repr(value)

    def test_build_match_keys_adds_the_hardcover_key(self):
        keys = build_match_keys("Overlord, Vol. 2", "Maruyama Kugane", hardcover_id="886465")

        assert "hardcover:886465" in keys
        assert "overlord vol 2|maruyama kugane" in keys

    def test_hardcover_key_never_collides_with_a_title_key(self):
        keys = build_match_keys("886465", "Someone", hardcover_id="886465")

        assert sum(k.startswith(HARDCOVER_KEY_PREFIX) for k in keys) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/library/test_matching.py -q`
Expected: FAIL — `ImportError: cannot import name 'HARDCOVER_KEY_PREFIX'`

- [ ] **Step 3: Implement in `shelfmark/library/matching.py`**

Replace the body of `author_match_keys` after `keys = {normalized}` with:

```python
    keys = {normalized}

    raw = author if isinstance(author, str) else ""
    if raw.count(",") == 1:
        last, _, first = raw.partition(",")
        flipped = normalize_author(f"{first.strip()} {last.strip()}")
        if flipped:
            keys.add(flipped)
    elif "," not in raw:
        # Surname-first without a comma ("Maruyama Kugane") is common for Japanese
        # names. Only a two-token name is reversed: longer names have no single
        # obvious flip, and the reversed key still only ever pairs with the same
        # normalized title, so it cannot reach a different book by itself.
        parts = normalized.split()
        if len(parts) == 2:
            keys.add(f"{parts[1]} {parts[0]}")

    return keys
```

and update its docstring's first paragraph to:

```python
    """Build every normalized spelling of one author's name.

    A single comma means an inverted name ("McFadden, Freida"), so both
    orderings are emitted. Two or more commas means a list of authors, where
    flipping would invent a person who does not exist. A comma-less two-token
    name is also emitted reversed, since surname-first order is common and
    carries no comma to announce it.
    """
```

Replace the qualifier set and its comment:

```python
# Completeness and medium, not edition. "(Unabridged)" is near-universal shelf
# noise, and "(Light Novel)" / "(Novel)" / "(LN)" name the medium — one side
# carries the label and the other does not, for the very same release.
# "(Manga)" and "(Graphic Novel)" stay: those are different adaptations.
_NON_EDITION_QUALIFIERS = frozenset({"unabridged", "abridged", "light novel", "novel", "ln"})
```

Add after `isbn_match_key`:

```python
HARDCOVER_KEY_PREFIX = "hardcover:"


def hardcover_match_key(value: object) -> str:
    """Build the namespaced key for a Hardcover book id, or "" if it is unusable.

    A Hardcover book id names a *work*: every edition shares it, and different
    adaptations (light novel vs manga) get different ids. Only a clean ASCII
    digit string is accepted — an exact match on junk is still an exact match.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    if not text or not text.isascii() or not text.isdigit():
        return ""
    return f"{HARDCOVER_KEY_PREFIX}{text}"
```

Change `build_match_keys` to accept and use the id:

```python
def build_match_keys(
    title: str | None,
    author: str | None,
    subtitle: str | None = None,
    asin: object = None,
    isbn: object = None,
    hardcover_id: object = None,
) -> set[str]:
    """Build the match keys for one book.

    Title keys are every title variant × every author variant; without both
    halves none are emitted, since half a key would match every other half-key.
    A valid ASIN, ISBN or Hardcover id adds one more key on top, and any of them
    is enough on its own — each is a complete identity where a bare title is not.
    """
    keys: set[str] = set()

    for key in (asin_match_key(asin), isbn_match_key(isbn), hardcover_match_key(hardcover_id)):
        if key:
            keys.add(key)

    titles = title_match_keys(title, subtitle)
    authors = author_match_keys(author)
    if titles and authors:
        keys.update(f"{t}{KEY_SEPARATOR}{a}" for t in titles for a in authors)

    return keys
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/library -q`
Expected: all pass (the whole `tests/library` package, since index and lookup consume these keys).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/library tests/library && uv run ruff format shelfmark/library tests/library
uv run basedpyright shelfmark/library
git add shelfmark/library/matching.py tests/library/test_matching.py
git commit -m "feat(library): Hardcover-ID key, reversed two-token authors, medium labels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: Grimmory client — `get_book`, auth error, nested page count

**Files:**
- Modify: `shelfmark/grimmory/client.py` (new `BookloreAuthError`, `get_book`; `list_books` page count)
- Test: `tests/grimmory/test_client.py`

**Interfaces:**
- Produces: `class BookloreAuthError(BookloreError)`; `get_book(booklore_config: BookloreConfig, token: str, book_id: object, *, session: requests.Session | None = None) -> dict[str, Any]` — raises `BookloreAuthError` on 401 and `BookloreError` on any other failure or malformed payload; `list_books` returns the page count from `payload["page"]["totalPages"]` (falling back to top-level `totalPages`) and raises when a page whose `content` is not literally empty carries no usable count.

- [ ] **Step 1: Write the failing tests**

In `tests/grimmory/test_client.py`, change the import line to:

```python
from shelfmark.grimmory.client import (
    BookloreAuthError,
    BookloreConfig,
    BookloreError,
    get_book,
    list_books,
)
```

Append:

```python
class TestListBooksPaging:
    """Grimmory returns Spring's PagedModel: {content, links, page: {totalPages, ...}}."""

    def test_reads_the_nested_page_count(self, monkeypatch):
        payload = {"content": [{"id": 1}], "page": {"totalPages": 2, "number": 0}}
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(payload))

        assert list_books(CONFIG, "token", page=0, size=500) == ([{"id": 1}], 2)

    def test_a_non_empty_page_without_a_count_is_malformed(self, monkeypatch):
        payload = {"content": [{"id": 1}], "page": {"number": 0}}
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(payload))

        with pytest.raises(BookloreError, match="page count"):
            list_books(CONFIG, "token", page=0, size=500)

    def test_junk_rows_without_a_count_are_malformed_not_empty(self, monkeypatch):
        payload = {"content": [None], "page": {"number": 0}}
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(payload))

        with pytest.raises(BookloreError, match="page count"):
            list_books(CONFIG, "token", page=0, size=500)

    def test_an_empty_library_is_one_empty_page(self, monkeypatch):
        payload = {"content": [], "page": {"number": 0}}
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(payload))

        assert list_books(CONFIG, "token", page=0, size=500) == ([], 1)

    def test_an_invalid_count_on_a_non_empty_page_is_malformed(self, monkeypatch):
        payload = {"content": [{"id": 1}], "page": {"totalPages": 0}}
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(payload))

        with pytest.raises(BookloreError):
            list_books(CONFIG, "token", page=0, size=500)


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.response


DETAIL = {"id": 165, "metadata": {"title": "Overlord Volume 3", "hardcoverBookId": "885682"}}


class TestGetBook:
    def test_returns_the_full_book_through_the_given_session(self):
        session = _Session(_Response(DETAIL))

        assert get_book(CONFIG, "token", 165, session=session) == DETAIL
        assert session.calls == ["http://grimmory:6060/api/v1/books/165"]

    def test_uses_requests_when_no_session_is_given(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(DETAIL))

        assert get_book(CONFIG, "token", "165")["id"] == 165

    def test_a_401_is_an_auth_error(self):
        with pytest.raises(BookloreAuthError):
            get_book(CONFIG, "token", 165, session=_Session(_Response({}, status_code=401)))

    def test_a_404_is_a_failure_not_a_skip(self):
        with pytest.raises(BookloreError) as raised:
            get_book(CONFIG, "token", 165, session=_Session(_Response({}, status_code=404)))
        assert not isinstance(raised.value, BookloreAuthError)

    def test_an_empty_payload_is_malformed(self):
        with pytest.raises(BookloreError, match="malformed"):
            get_book(CONFIG, "token", 165, session=_Session(_Response({})))

    def test_a_payload_for_another_book_is_malformed(self):
        other = {"id": 999, "metadata": {"title": "x"}}
        with pytest.raises(BookloreError, match="999"):
            get_book(CONFIG, "token", 165, session=_Session(_Response(other)))

    def test_transport_failure_is_a_booklore_error(self):
        class Broken:
            def get(self, url, **kwargs):
                raise requests.exceptions.ConnectionError

        with pytest.raises(BookloreError, match="Grimmory"):
            get_book(CONFIG, "token", 165, session=Broken())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/grimmory/test_client.py -q`
Expected: FAIL — `ImportError: cannot import name 'BookloreAuthError'`

- [ ] **Step 3: Implement in `shelfmark/grimmory/client.py`**

After `class BookloreError(Exception): ...` add:

```python
class BookloreAuthError(BookloreError):
    """Grimmory rejected the session token (HTTP 401) — a re-login may fix it."""
```

In `list_books`, replace the last block (from `raw_total = payload.get("totalPages")` to the `return`) with:

```python
    # Grimmory returns Spring's PagedModel, which nests the count under "page";
    # older Booklore builds put it at the top level. A non-empty page with no
    # usable count is malformed: guessing "one page" would silently drop every
    # later page from the index.
    paging = payload.get("page")
    raw_total = paging.get("totalPages") if isinstance(paging, dict) else None
    if raw_total is None:
        raw_total = payload.get("totalPages")
    if isinstance(raw_total, int) and not isinstance(raw_total, bool) and raw_total > 0:
        return books, raw_total
    # Only a literally empty `content` is an empty library. Rows filtered out
    # above as non-objects are a broken page, and reading that as "empty" would
    # let the sync replace the whole index with nothing.
    if not content:
        return books, 1
    msg = f"Unexpected {BOOKLORE_DISPLAY_NAME} book listing payload: missing page count"
    raise BookloreError(msg)
```

Append to the module:

```python
def get_book(
    booklore_config: BookloreConfig,
    token: str,
    book_id: object,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fetch one book in full — the only endpoint that returns provider IDs.

    The listing endpoints omit ``hardcoverBookId`` and the other provider IDs,
    so the library index reads each book individually. Every failure raises:
    a skipped book would silently lose its badge. A payload for a different
    book, or without a metadata object, is malformed rather than skippable.
    """
    url = f"{booklore_config.base_url}/api/v1/books/{book_id}"
    headers = {"Authorization": f"Bearer {token}"}
    http = session if session is not None else requests

    try:
        response = http.get(url, headers=headers, timeout=30, verify=booklore_config.verify_tls)
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {BOOKLORE_DISPLAY_NAME}"
        raise BookloreError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} book {book_id} read timed out"
        raise BookloreError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"Failed to fetch {BOOKLORE_DISPLAY_NAME} book {book_id}: {exc}"
        raise BookloreError(msg) from exc

    if response.status_code == 401:
        msg = f"{BOOKLORE_DISPLAY_NAME} session expired"
        raise BookloreAuthError(msg)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} book {book_id} read failed ({response.status_code})"
        raise BookloreError(msg) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        msg = f"Invalid {BOOKLORE_DISPLAY_NAME} book {book_id} response"
        raise BookloreError(msg) from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict):
        msg = f"{BOOKLORE_DISPLAY_NAME} book {book_id} response is malformed"
        raise BookloreError(msg)
    if str(payload.get("id")) != str(book_id):
        msg = f"{BOOKLORE_DISPLAY_NAME} returned book {payload.get('id')} when asked for {book_id}"
        raise BookloreError(msg)
    return payload
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/grimmory -q`
Expected: all pass (including the existing `test_treats_a_missing_total_pages_as_one`, whose page is empty).

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/grimmory tests/grimmory && uv run ruff format shelfmark/grimmory tests/grimmory
uv run basedpyright shelfmark/grimmory
git add shelfmark/grimmory/client.py tests/grimmory/test_client.py
git commit -m "feat(grimmory): read single books and honour the nested page count

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: Grimmory provider — Hardcover IDs from full book reads

**Files:**
- Modify: `shelfmark/library/index.py` (`LibraryItem` gains `hardcover_id: str = ""` as its last field)
- Modify: `shelfmark/library/providers/grimmory.py` (`extract_library_items`, `fetch_items`, imports)
- Test: `tests/library/test_providers_grimmory.py`

**Interfaces:**
- Consumes: `get_book`, `BookloreAuthError`, `BookloreError` (Task 2); `hardcover_match_key`, `HARDCOVER_KEY_PREFIX` (Task 1).
- Produces: `LibraryItem.hardcover_id: str` (digits or `""`); `GrimmoryProvider.fetch_items()` returns items built from full book payloads, raising `BookloreError` on any detail failure, a second 401, or page-cap exhaustion.

- [ ] **Step 1: Write the failing tests**

In `tests/library/test_providers_grimmory.py`, replace the whole `class TestPagination:` with:

```python
def _patch_sync(monkeypatch, pages, *, get_book=None, login=None):
    """Patch login, listing and per-book reads; by default a read echoes the listing row."""
    from shelfmark.library.providers import grimmory as provider_module

    rows = {str(row["id"]): row for books, _ in pages.values() for row in books}

    def echo(cfg, token, book_id, *, session=None):
        return rows[str(book_id)]

    monkeypatch.setattr(provider_module, "booklore_login", login or (lambda cfg: "token"))
    monkeypatch.setattr(
        provider_module, "list_books", lambda cfg, token, *, page, size: pages[page]
    )
    monkeypatch.setattr(provider_module, "get_book", get_book or echo)
    monkeypatch.setattr(provider_module.GrimmoryProvider, "is_enabled", lambda self: True)
    return provider_module


class TestPagination:
    def test_walks_every_page(self, monkeypatch):
        pages = {0: ([_book(book_id=1)], 3), 1: ([_book(book_id=2)], 3), 2: ([_book(book_id=3)], 3)}
        provider_module = _patch_sync(monkeypatch, pages)

        items = provider_module.GrimmoryProvider().fetch_items()

        assert [item.item_id for item in items] == ["1", "2", "3"]

    def test_fails_when_the_page_cap_stops_it_early(self, monkeypatch):
        # A server reporting an absurd totalPages must neither spin forever nor
        # be indexed partially: hitting the cap is a sync failure.
        from shelfmark.grimmory.client import BookloreError

        calls = {"n": 0}

        def endless(cfg, token, *, page, size):
            calls["n"] += 1
            return ([_book(book_id=page)], 10_000)

        provider_module = _patch_sync(monkeypatch, {})
        monkeypatch.setattr(provider_module, "list_books", endless)

        with pytest.raises(BookloreError, match="cap"):
            provider_module.GrimmoryProvider().fetch_items()
        assert calls["n"] == provider_module._MAX_PAGES

    def test_a_single_page_library_makes_one_call(self, monkeypatch):
        calls = {"n": 0}
        pages = {0: ([_book(book_id=1)], 1)}
        provider_module = _patch_sync(monkeypatch, pages)
        real_list = provider_module.list_books

        def counting(cfg, token, *, page, size):
            calls["n"] += 1
            return real_list(cfg, token, page=page, size=size)

        monkeypatch.setattr(provider_module, "list_books", counting)

        provider_module.GrimmoryProvider().fetch_items()

        assert calls["n"] == 1


class TestFullBookReads:
    """Only the single-book endpoint returns hardcoverBookId."""

    def test_reads_each_book_and_keeps_its_hardcover_id(self, monkeypatch):
        listed = _book(book_id=165, title="Overlord Volume 3")
        detailed = _book(book_id=165, title="Overlord Volume 3", hardcoverBookId="885682")
        provider_module = _patch_sync(
            monkeypatch,
            {0: ([listed], 1)},
            get_book=lambda cfg, token, book_id, *, session=None: detailed,
        )

        items = provider_module.GrimmoryProvider().fetch_items()

        assert items[0].hardcover_id == "885682"

    def test_a_non_digit_hardcover_id_is_dropped(self):
        assert extract_library_items([_book(hardcoverBookId="hc-1")])[0].hardcover_id == ""

    def test_a_book_without_a_hardcover_id_still_indexes(self):
        assert extract_library_items([_book()])[0].hardcover_id == ""

    def test_a_failed_read_fails_the_whole_sync(self, monkeypatch):
        from shelfmark.grimmory.client import BookloreError

        def broken(cfg, token, book_id, *, session=None):
            raise BookloreError("Grimmory book 1 read failed (404)")

        provider_module = _patch_sync(monkeypatch, {0: ([_book()], 1)}, get_book=broken)

        with pytest.raises(BookloreError, match="404"):
            provider_module.GrimmoryProvider().fetch_items()

    def test_one_expired_session_relogs_once(self, monkeypatch):
        from shelfmark.grimmory.client import BookloreAuthError

        logins = {"n": 0}
        reads = {"n": 0}

        def login(cfg):
            logins["n"] += 1
            return f"token-{logins['n']}"

        def read(cfg, token, book_id, *, session=None):
            reads["n"] += 1
            if token == "token-1":
                raise BookloreAuthError("Grimmory session expired")
            return _book(book_id=book_id)

        provider_module = _patch_sync(
            monkeypatch, {0: ([_book(book_id=1), _book(book_id=2)], 1)}, get_book=read, login=login
        )

        items = provider_module.GrimmoryProvider().fetch_items()

        assert [i.item_id for i in items] == ["1", "2"]
        assert logins["n"] == 2

    def test_a_second_expired_session_fails(self, monkeypatch):
        from shelfmark.grimmory.client import BookloreAuthError

        def always_expired(cfg, token, book_id, *, session=None):
            raise BookloreAuthError("Grimmory session expired")

        provider_module = _patch_sync(monkeypatch, {0: ([_book()], 1)}, get_book=always_expired)

        with pytest.raises(BookloreAuthError):
            provider_module.GrimmoryProvider().fetch_items()

    def test_reads_share_one_session(self, monkeypatch):
        sessions = []

        def read(cfg, token, book_id, *, session=None):
            sessions.append(session)
            return _book(book_id=book_id)

        provider_module = _patch_sync(
            monkeypatch, {0: ([_book(book_id=1), _book(book_id=2)], 1)}, get_book=read
        )
        provider_module.GrimmoryProvider().fetch_items()

        assert sessions[0] is not None and sessions[0] is sessions[1]

    def test_an_empty_library_makes_no_reads(self, monkeypatch):
        # Review Focus #3
        reads = {"n": 0}

        def read(cfg, token, book_id, *, session=None):
            reads["n"] += 1
            return {}

        provider_module = _patch_sync(monkeypatch, {0: ([], 1)}, get_book=read)

        assert provider_module.GrimmoryProvider().fetch_items() == []
        assert reads["n"] == 0

    def test_a_book_listed_twice_is_read_and_indexed_once(self, monkeypatch):
        # Review Focus #4
        reads = []

        def read(cfg, token, book_id, *, session=None):
            reads.append(book_id)
            return _book(book_id=book_id)

        pages = {0: ([_book(book_id=1)], 2), 1: ([_book(book_id=1)], 2)}
        provider_module = _patch_sync(monkeypatch, pages, get_book=read)

        items = provider_module.GrimmoryProvider().fetch_items()

        assert reads == [1]
        assert [i.item_id for i in items] == ["1"]
```

Add `import pytest` at the top of the file if it is not already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/library/test_providers_grimmory.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'get_book'` (module) and `hardcover_id` attribute errors.

- [ ] **Step 3: Implement**

In `shelfmark/library/index.py`, add the last field to `LibraryItem`:

```python
    asin: str
    isbn13: str
    # A Hardcover book id (digits) when the source knows the work; it is carried
    # only as a `hardcover:` match key, never stored as a column.
    hardcover_id: str = ""
```

In `shelfmark/library/providers/grimmory.py`, change the imports:

```python
import requests

from shelfmark.core.logger import setup_logger
from shelfmark.grimmory.client import (
    BookloreAuthError,
    BookloreConfig,
    BookloreError,
    booklore_login,
    get_book,
    list_books,
)
from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_EBOOK,
    SOURCE_GRIMMORY,
    LibraryItem,
)
from shelfmark.library.matching import HARDCOVER_KEY_PREFIX, hardcover_match_key, normalize_isbn
```

Add above `extract_library_items`:

```python
def _hardcover_id(value: object) -> str:
    key = hardcover_match_key(value)
    return key[len(HARDCOVER_KEY_PREFIX) :] if key else ""
```

In `extract_library_items`, add to the `LibraryItem(...)` call after `isbn13=...`:

```python
                hardcover_id=_hardcover_id(metadata.get("hardcoverBookId")),
```

Replace `fetch_items` with:

```python
    def fetch_items(self) -> list[LibraryItem]:
        """List every book, then read each in full for its provider IDs.

        The listing omits ``hardcoverBookId``, so each book is read on its own
        (one shared session). Every failure fails the sync, so the scheduler
        keeps the previous index rather than storing one with badges missing:
        a failed read, a second expired session, or a listing too long for the
        page cap.
        """
        from shelfmark.core.config import config

        booklore_config = BookloreConfig(
            base_url=str(config.get("BOOKLORE_HOST", "") or "").strip().rstrip("/"),
            username=str(config.get("BOOKLORE_USERNAME", "") or "").strip(),
            password=str(config.get("BOOKLORE_PASSWORD", "") or ""),
            library_id=0,
            path_id=0,
        )

        token = booklore_login(booklore_config)

        rows: list[dict[str, Any]] = []
        page = 0
        total_pages = 1
        while page < total_pages:
            if page >= _MAX_PAGES:
                msg = f"Grimmory listing exceeded the {_MAX_PAGES}-page cap"
                raise BookloreError(msg)
            books, total_pages = list_books(booklore_config, token, page=page, size=_PAGE_SIZE)
            rows.extend(books)
            page += 1

        details: list[dict[str, Any]] = []
        seen: set[str] = set()
        relogged = False
        with requests.Session() as session:
            for row in rows:
                book_id = row.get("id")
                if book_id is None or str(book_id) in seen:
                    continue
                seen.add(str(book_id))
                try:
                    detail = get_book(booklore_config, token, book_id, session=session)
                except BookloreAuthError:
                    if relogged:
                        raise
                    relogged = True
                    token = booklore_login(booklore_config)
                    detail = get_book(booklore_config, token, book_id, session=session)
                details.append(detail)

        return extract_library_items(details)
```

If `logger` is no longer referenced in the module after this change, leave its definition — other providers follow the same pattern.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/library tests/grimmory -q`
Expected: all pass.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/library tests/library && uv run ruff format shelfmark/library tests/library
uv run basedpyright shelfmark/library
git add shelfmark/library/index.py shelfmark/library/providers/grimmory.py tests/library/test_providers_grimmory.py
git commit -m "feat(library): index Grimmory Hardcover IDs from full book reads

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: Index + lookup — Hardcover key, read-back, conflict veto

**Files:**
- Modify: `shelfmark/library/index.py` (`LibraryMatch.hardcover_id`; `replace_items`; `find_matches`)
- Modify: `shelfmark/library/lookup.py` (`lookup_books`)
- Test: `tests/library/test_index.py`, `tests/library/test_lookup.py`

**Interfaces:**
- Consumes: `build_match_keys(..., hardcover_id=)`, `hardcover_match_key`, `HARDCOVER_KEY_PREFIX` (Task 1); `LibraryItem.hardcover_id` (Task 3).
- Produces: `LibraryMatch.hardcover_id: str` (last field, default `""`) filled from the item's `hardcover:` key; `lookup_books` reads `provider`/`provider_id` per book.

- [ ] **Step 1: Write the failing tests**

Append to `tests/library/test_index.py`:

```python
class TestHardcoverKey:
    def test_an_item_is_found_by_its_hardcover_key_and_carries_the_id(self, index):
        overlord = _item(
            title="Overlord, Vol. 1", author="Maruyama Kugane", asin="", hardcover_id="730514"
        )
        index.replace_items(SOURCE_AUDIOBOOKSHELF, [overlord])

        matches = index.find_matches(build_match_keys("", "", hardcover_id="730514"))

        assert [m.item_id for m in matches] == ["li_1"]
        assert matches[0].hardcover_id == "730514"

    def test_an_item_without_a_hardcover_id_reports_none(self, index):
        index.replace_items(SOURCE_AUDIOBOOKSHELF, [_item()])

        matches = index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))

        assert matches[0].hardcover_id == ""
```

Append to `tests/library/test_lookup.py`:

```python
def _overlord(item_id="161", hardcover_id="730514", **overrides):
    fields = {
        "title": "Overlord, Vol. 1",
        "author": "Maruyama Kugane",
        "hardcover_id": hardcover_id,
    }
    fields.update(overrides)
    return _stored(SOURCE_GRIMMORY, MEDIA_TYPE_EBOOK, item_id=item_id, **fields)


def _hardcover_result(provider_id="730514", provider="hardcover", **overrides):
    book = {
        "id": "hc1",
        "title": "Overlord (Light Novel), Vol. 1: The Undead King",
        "author": "Kugane Maruyama",
        "provider": provider,
        "provider_id": provider_id,
        "content_type": "ebook",
    }
    book.update(overrides)
    return book


class TestHardcoverIdMatching:
    def test_the_real_overlord_pair_is_held_not_just_matched(self, index, enabled_providers):
        index.replace_items(SOURCE_GRIMMORY, [_overlord()])

        match = lookup_books([_hardcover_result()], index=index)["matches"]["hc1"]

        assert [i["item_id"] for i in match["items"]] == ["161"]
        assert match["other_editions"] == []

    def test_provider_spelling_does_not_matter(self, index, enabled_providers):
        # Review Focus #1
        index.replace_items(SOURCE_GRIMMORY, [_overlord()])

        for provider in ("Hardcover", "HARDCOVER", " hardcover "):
            result = lookup_books(
                [_hardcover_result(provider=provider, title="Unrelated", author="Nobody")],
                index=index,
            )
            assert "hc1" in result["matches"], provider

    def test_another_providers_numeric_id_is_not_a_hardcover_id(self, index, enabled_providers):
        index.replace_items(SOURCE_GRIMMORY, [_overlord()])

        result = lookup_books(
            [_hardcover_result(provider="openlibrary", title="Unrelated", author="Nobody")],
            index=index,
        )

        assert result["matches"] == {}

    def test_a_different_hardcover_id_vetoes_a_title_match(self, index, enabled_providers):
        # The manga shares the normalized title and (reversed) author with the
        # light novel, but Hardcover says it is a different work.
        index.replace_items(SOURCE_GRIMMORY, [_overlord()])

        manga = _hardcover_result(provider_id="480363", title="Overlord (Manga) Vol. 1")
        result = lookup_books([manga], index=index)

        assert result["matches"] == {}

    def test_a_manga_title_without_ids_still_demotes(self, index, enabled_providers):
        index.replace_items(SOURCE_GRIMMORY, [_overlord(hardcover_id="")])

        manga = _hardcover_result(provider_id="", title="Overlord (Manga) Vol. 1")
        match = lookup_books([manga], index=index)["matches"]["hc1"]

        assert match["items"] == []
        assert [i["item_id"] for i in match["other_editions"]] == ["161"]

    def test_an_item_without_a_hardcover_id_is_not_vetoed(self, index, enabled_providers):
        index.replace_items(SOURCE_GRIMMORY, [_overlord(hardcover_id="")])

        result = lookup_books([_hardcover_result(title="Overlord, Vol. 1")], index=index)
        match = result["matches"]["hc1"]

        assert [i["item_id"] for i in match["items"]] == ["161"]

    def test_an_audiobook_search_reports_the_ebook_as_another_format(
        self, index, enabled_providers
    ):
        index.replace_items(SOURCE_GRIMMORY, [_overlord()])

        result = lookup_books([_hardcover_result(content_type="audiobook")], index=index)
        match = result["matches"]["hc1"]

        assert match["items"] == []
        assert [i["item_id"] for i in match["other_formats"]] == ["161"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/library/test_index.py tests/library/test_lookup.py -q`
Expected: FAIL — `AttributeError: 'LibraryMatch' object has no attribute 'hardcover_id'` and missing matches. Three guard tests already pass at this point: `test_another_providers_numeric_id_is_not_a_hardcover_id`, `test_a_manga_title_without_ids_still_demotes` and `test_an_item_without_a_hardcover_id_is_not_vetoed`. The last one passes because Task 1's author reversal already makes the title key match. They pin behaviour that must survive the change.

- [ ] **Step 3: Implement**

In `shelfmark/library/index.py`:

Change the import to `from shelfmark.library.matching import HARDCOVER_KEY_PREFIX, build_match_keys`.

Add the last field to `LibraryMatch`:

```python
    asin: str
    isbn13: str
    # The matched item's Hardcover book id, read back from its `hardcover:` key.
    # Internal: lets a lookup veto a match whose work id disagrees.
    hardcover_id: str = ""
```

In `replace_items`, pass the id into the keys:

```python
            keys = build_match_keys(
                item.title,
                item.author,
                item.subtitle,
                asin=item.asin,
                isbn=item.isbn13,
                hardcover_id=item.hardcover_id,
            )
```

In `find_matches`, select the item's Hardcover key alongside the row and fill the field. Replace the `cursor = conn.execute(...)` call and the `LibraryMatch(...)` construction with:

```python
                    cursor = conn.execute(
                        f"""
                        SELECT DISTINCT i.source, i.item_id, i.library_id, i.library_name,
                               i.media_type, i.title, i.author, i.asin, i.isbn13,
                               (SELECT h.match_key FROM library_item_keys h
                                 WHERE h.source = i.source AND h.item_id = i.item_id
                                   AND h.match_key LIKE ?
                                 LIMIT 1) AS hardcover_key
                        FROM library_items i
                        JOIN library_item_keys k
                          ON k.item_id = i.item_id AND k.source = i.source
                        WHERE k.match_key IN ({placeholders}){source_filter}
                        """,  # noqa: S608 - placeholders only, keys/sources are bound
                        [f"{HARDCOVER_KEY_PREFIX}%", *params],
                    )
                    for row in cursor.fetchall():
                        hardcover_key = str(row["hardcover_key"] or "")
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
                            hardcover_id=hardcover_key[len(HARDCOVER_KEY_PREFIX) :],
                        )
                        matches[(match.source, match.item_id)] = match
```

In `shelfmark/library/lookup.py`:

Change the matching import to:

```python
from shelfmark.library.matching import (
    HARDCOVER_KEY_PREFIX,
    build_match_keys,
    edition_qualifiers,
    hardcover_match_key,
    normalize_asin,
)
```

Add above `lookup_books`:

```python
def _hardcover_id(book: dict[str, Any]) -> str:
    """The book's Hardcover id, only when the result came from Hardcover."""
    provider = str(book.get("provider") or "").strip().casefold()
    if provider != "hardcover":
        return ""
    key = hardcover_match_key(book.get("provider_id"))
    return key[len(HARDCOVER_KEY_PREFIX) :] if key else ""


def _without_conflicts(matches: list[LibraryMatch], hardcover_id: str) -> list[LibraryMatch]:
    """Drop holdings whose Hardcover work id disagrees with the book's.

    Two verified works can still share a normalized title/author key (a light
    novel and its manga, say); the work id is the stronger evidence.
    """
    if not hardcover_id:
        return matches
    return [m for m in matches if not m.hardcover_id or m.hardcover_id == hardcover_id]
```

In `lookup_books`, replace the key building and lookup:

```python
        hardcover_id = _hardcover_id(book)
        keys = build_match_keys(
            book.get("title"),
            book.get("author"),
            asin=book.get("asin"),
            isbn=book.get("isbn_13") or book.get("isbn_10"),
            hardcover_id=hardcover_id,
        )
        if not keys:
            continue

        found = _without_conflicts(library_index.find_matches(keys, states.keys()), hardcover_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/library tests/grimmory -q`
Expected: all pass.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/library tests/library && uv run ruff format shelfmark/library tests/library
uv run basedpyright shelfmark/library
git add shelfmark/library/index.py shelfmark/library/lookup.py tests/library/test_index.py tests/library/test_lookup.py
git commit -m "feat(library): match Hardcover results by work id, veto conflicting ids

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Frontend helpers — send the Hardcover identity, combined-mode lock rule

**Files:**
- Modify: `src/frontend/src/utils/libraryMatches.ts`
- Modify: `src/frontend/src/hooks/useLibraryMatches.ts` (`useBothFormatMatches`)
- Test: `src/frontend/src/tests/libraryMatches.test.ts`

**Interfaces:**
- Consumes: the backend contract from Task 4 — each lookup book may carry `provider` and `provider_id`; the backend only trusts `provider_id` when `provider` is Hardcover.
- Produces (used by Task 6):
  - `LibraryLookupBook.provider?: string`, `LibraryLookupBook.provider_id?: string`
  - `singleBookLookup(id, title, author, asin?, isbn?, contentType?, provider?, providerId?): Book[]`
  - `hardcoverIdentity(bookData: Record<string, unknown> | null | undefined): { provider?: string; providerId?: string }`
  - `interface FormatMatches { ebook?: LibraryMatch; audiobook?: LibraryMatch }`
  - `interface BothFormatMatches { ebook: Record<string, LibraryMatch>; audiobook: Record<string, LibraryMatch> }`
  - `withContentType(books: Book[], contentType: string): Book[]`
  - `bothFormatsFor(both: BothFormatMatches | null, id: string): FormatMatches | undefined`
  - `isLockedInLibrary(match: LibraryMatch | undefined, bothFormats?: FormatMatches): boolean`
  - `useBothFormatMatches(books: Book[], enabled: boolean): BothFormatMatches | null` (in `hooks/useLibraryMatches.ts`)

Why combined mode looks each format up separately: the combined action acquires both formats, so it may lock only when *each* format is held as the same edition. The backend sorts editions only within the requested format: `other_formats` is never edition-checked (`_match_payload`). So "the other format appears in `other_formats`" would let a full-cast audiobook stand in for the recording being acquired. A lookup per format puts each holding through that format's own edition check, with no backend change.

- [ ] **Step 1: Write the failing tests**

In `src/frontend/src/tests/libraryMatches.test.ts`, add `bothFormatsFor`, `hardcoverIdentity`, `isLockedInLibrary` and `withContentType` to the import list from `'../utils/libraryMatches'`, then append:

```ts
describe('Hardcover identity in the lookup', () => {
  it('sends the provider id for a Hardcover result', () => {
    const payload = buildLibraryLookupPayload([
      book({ provider: 'hardcover', provider_id: '730514' }),
    ]);

    expect(payload).toEqual([
      {
        id: 'bk1',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        provider: 'hardcover',
        provider_id: '730514',
      },
    ]);
  });

  it('keeps the provider id off other providers', () => {
    const payload = buildLibraryLookupPayload([
      book({ provider: 'openlibrary', provider_id: 'OL123W' }),
    ]);

    expect(payload[0]).not.toHaveProperty('provider_id');
    expect(payload[0]).not.toHaveProperty('provider');
  });

  it('does not make an otherwise unmatchable book eligible', () => {
    const payload = buildLibraryLookupPayload([
      book({ author: '', provider: 'hardcover', provider_id: '730514' }),
    ]);

    expect(payload).toEqual([]);
  });

  it('refetches when the provider id changes', () => {
    const before = booksLookupSignature([book({ provider: 'hardcover', provider_id: '1' })]);
    const after = booksLookupSignature([book({ provider: 'hardcover', provider_id: '2' })]);

    expect(before).not.toEqual(after);
  });

  it('carries the identity through singleBookLookup', () => {
    const [entry] = singleBookLookup(
      'details-x',
      'Overlord',
      'Kugane Maruyama',
      undefined,
      undefined,
      'ebook',
      'hardcover',
      '730514',
    );

    expect(buildLibraryLookupPayload([entry])[0]).toMatchObject({
      provider: 'hardcover',
      provider_id: '730514',
    });
  });

  it('reads a stored request identity only when it is Hardcover', () => {
    expect(hardcoverIdentity({ provider: 'hardcover', provider_id: 730514 })).toEqual({
      provider: 'hardcover',
      providerId: '730514',
    });
    expect(hardcoverIdentity({ provider: 'Hardcover', provider_id: ' 730514 ' })).toEqual({
      provider: 'hardcover',
      providerId: '730514',
    });
    expect(hardcoverIdentity({ provider: 'openlibrary', provider_id: 'OL1W' })).toEqual({});
    expect(hardcoverIdentity({ provider: 'hardcover', provider_id: '' })).toEqual({});
    expect(hardcoverIdentity(null)).toEqual({});
  });
});

describe('isLockedInLibrary', () => {
  const held = match();
  // Same book, same format, but a different recording (a full-cast adaptation, say).
  const differentEdition = match({ items: [], other_editions: match().items });

  it('locks on a same-format holding outside combined mode', () => {
    expect(isLockedInLibrary(held)).toBe(true);
  });

  it('never locks without a same-format holding', () => {
    expect(isLockedInLibrary(differentEdition)).toBe(false);
    expect(isLockedInLibrary(undefined)).toBe(false);
  });

  // Review Focus #5: combined mode acquires both formats, so one is not enough.
  it('does not lock combined mode on one format', () => {
    expect(isLockedInLibrary(held, { ebook: held, audiobook: undefined })).toBe(false);
    expect(isLockedInLibrary(held, { ebook: undefined, audiobook: held })).toBe(false);
  });

  it('does not lock combined mode when the other format is only a different edition', () => {
    expect(isLockedInLibrary(held, { ebook: held, audiobook: differentEdition })).toBe(false);
  });

  it('locks combined mode when both formats are held', () => {
    expect(isLockedInLibrary(held, { ebook: held, audiobook: held })).toBe(true);
  });
});

describe('combined-mode lookups', () => {
  it('forces every book to one format', () => {
    const books = withContentType(
      [book({ content_type: 'ebook' }), book({ id: 'bk2' })],
      'audiobook',
    );

    expect(buildLibraryLookupPayload(books).map((entry) => entry.content_type)).toEqual([
      'audiobook',
      'audiobook',
    ]);
  });

  it('picks one book out of both lookups', () => {
    const both = { ebook: { bk1: match() }, audiobook: {} };

    expect(bothFormatsFor(both, 'bk1')).toEqual({ ebook: match(), audiobook: undefined });
  });

  it('has nothing outside combined mode', () => {
    expect(bothFormatsFor(null, 'bk1')).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/frontend && npx vitest run src/tests/libraryMatches.test.ts`
Expected: FAIL — `bothFormatsFor`, `hardcoverIdentity`, `isLockedInLibrary` and `withContentType` are not exported, and the payload omits `provider`.

- [ ] **Step 3: Implement**

In `src/frontend/src/utils/libraryMatches.ts`:

Extend `LibraryLookupBook`:

```ts
export interface LibraryLookupBook {
  id: string;
  title?: string;
  author?: string;
  asin?: string;
  isbn_10?: string;
  isbn_13?: string;
  content_type?: string;
  /** Only ever 'hardcover': no other provider's id is a work identity the index keeps. */
  provider?: string;
  provider_id?: string;
}
```

Add above `buildLibraryLookupPayload`:

```ts
const HARDCOVER = 'hardcover';

const isHardcover = (provider: string | undefined): boolean =>
  (provider ?? '').trim().toLowerCase() === HARDCOVER;
```

In `buildLibraryLookupPayload`, after `const contentType = ...;` add:

```ts
    const providerId = isHardcover(book.provider) ? (book.provider_id ?? '').trim() : '';
```

and after `if (contentType) entry.content_type = contentType;` add:

```ts
    if (providerId) {
      entry.provider = HARDCOVER;
      entry.provider_id = providerId;
    }
```

The eligibility check above it stays as it is: a Hardcover id never makes a book eligible on its own.

Replace `singleBookLookup` (keep its doc comment, adding one paragraph) with:

```ts
/**
 * ...existing comment...
 *
 * `provider`/`providerId` carry the book's Hardcover identity when it has one;
 * the payload builder drops them for any other provider.
 */
export const singleBookLookup = (
  id: string,
  title: string | undefined,
  author: string | undefined,
  asin?: string,
  isbn?: string,
  contentType?: string,
  provider?: string,
  providerId?: string,
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
      provider: provider || undefined,
      provider_id: providerId || undefined,
    },
  ];
};

/**
 * The Hardcover identity stored with a request's book data, if it has one.
 *
 * Requests keep `provider`/`provider_id` from the result they were made from,
 * but as untyped JSON — the id may have come back as a number.
 */
export const hardcoverIdentity = (
  bookData: Record<string, unknown> | null | undefined,
): { provider?: string; providerId?: string } => {
  const provider = typeof bookData?.provider === 'string' ? bookData.provider : undefined;
  const rawId = bookData?.provider_id;
  let providerId = '';
  if (typeof rawId === 'string') providerId = rawId.trim();
  else if (typeof rawId === 'number' && Number.isFinite(rawId)) providerId = String(rawId);
  return isHardcover(provider) && providerId ? { provider: HARDCOVER, providerId } : {};
};
```

Replace `booksLookupSignature` with:

```ts
/** A stable key for a book list, so scrolling a result set refetches only once. */
export const booksLookupSignature = (books: Book[], defaultContentType?: string): string =>
  buildLibraryLookupPayload(books, defaultContentType)
    .map((book) =>
      [
        book.id,
        book.asin ?? '',
        book.isbn_13 ?? book.isbn_10 ?? '',
        book.content_type ?? '',
        book.provider_id ?? '',
      ].join('#'),
    )
    .join(',');
```

Add after `isHeldInFormat`:

```ts
/** One book's holdings in each format, from a lookup per format (combined mode). */
export interface FormatMatches {
  ebook?: LibraryMatch;
  audiobook?: LibraryMatch;
}

/** A whole result set's per-format lookups, keyed like any lookup response. */
export interface BothFormatMatches {
  ebook: Record<string, LibraryMatch>;
  audiobook: Record<string, LibraryMatch>;
}

/** The same books, all asked about as one format. */
export const withContentType = (books: Book[], contentType: string): Book[] =>
  books.map((book) => ({ ...book, content_type: contentType }));

export const bothFormatsFor = (
  both: BothFormatMatches | null,
  id: string,
): FormatMatches | undefined =>
  both ? { ebook: both.ebook[id], audiobook: both.audiobook[id] } : undefined;

/**
 * Whether the acquire action should lock.
 *
 * Outside combined mode, holding the browsed format locks. Combined mode's
 * action fetches both formats, so it locks only when each is held as the same
 * edition, which is what each format's own lookup says in its `items`. One
 * format held is still worth the badge, never the lock.
 */
export const isLockedInLibrary = (
  match: LibraryMatch | undefined,
  bothFormats?: FormatMatches,
): boolean =>
  bothFormats
    ? isHeldInFormat(bothFormats.ebook) && isHeldInFormat(bothFormats.audiobook)
    : isHeldInFormat(match);
```

In `src/frontend/src/hooks/useLibraryMatches.ts`, change the utils import to

```ts
import type { BothFormatMatches, LibraryMatch } from '../utils/libraryMatches';
import {
  booksLookupSignature,
  buildLibraryLookupPayload,
  withContentType,
} from '../utils/libraryMatches';
```

and append:

```ts
const NO_BOOKS: Book[] = [];

/**
 * Combined mode's lock: ask about the same books once as ebooks and once as
 * audiobooks, so each format gets its own edition check.
 *
 * Disabled, both lookups see no books and make no request.
 */
export const useBothFormatMatches = (books: Book[], enabled: boolean): BothFormatMatches | null => {
  const ebook = useLibraryMatches(enabled ? withContentType(books, 'ebook') : NO_BOOKS);
  const audiobook = useLibraryMatches(enabled ? withContentType(books, 'audiobook') : NO_BOOKS);
  return enabled ? { ebook, audiobook } : null;
};
```

`useLibraryMatches` refetches on its signature, not on array identity, so the fresh arrays each render cost no requests.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/frontend && npx vitest run src/tests/libraryMatches.test.ts`
Expected: all pass, including the existing `singleBookLookup` and signature tests.

- [ ] **Step 5: Check and commit**

```bash
cd src/frontend && npm run typecheck && npm run lint && npm run format:check && cd ../..
git add src/frontend/src/utils/libraryMatches.ts src/frontend/src/hooks/useLibraryMatches.ts src/frontend/src/tests/libraryMatches.test.ts
git commit -m "feat(frontend): send Hardcover identity to the library lookup, combined-mode lock rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Knip may flag `useBothFormatMatches` as unused until Task 6 wires it. The other new exports are imported by the test file.

---
### Task 6: Frontend wiring — combined-mode lock in the result views and details, identity from the three single-book surfaces

**Files:**
- Modify: `src/frontend/src/App.tsx` (the `<ResultsSection>` element, ~line 2587, and the `<DetailsModal>` element, ~line 2631)
- Modify: `src/frontend/src/components/ResultsSection.tsx`
- Modify: `src/frontend/src/components/resultsViews/CardView.tsx`, `CompactView.tsx`, `ListView.tsx`
- Modify: `src/frontend/src/components/DetailsModal.tsx`
- Modify: `src/frontend/src/components/activity/ActivityCard.tsx`
- Modify: `src/frontend/src/components/RequestConfirmationModal.tsx`

**Interfaces:**
- Consumes (Task 5): `useBothFormatMatches(books, enabled)`, `bothFormatsFor(both, id)`, `isLockedInLibrary(match, bothFormats?)`, the `FormatMatches`/`BothFormatMatches` types, `singleBookLookup(..., provider?, providerId?)`, `hardcoverIdentity(bookData)`.
- Produces: `ResultsSectionProps.combinedMode?: boolean` and `DetailsModalProps.combinedMode?: boolean` (default `false`); `CardViewProps.bothFormats?: FormatMatches`, `CompactViewProps.bothFormats?: FormatMatches`, `ListViewProps.bothFormatMatches?: BothFormatMatches | null`.

There is no component test harness in this repo (vitest runs pure-module tests only). The behaviour lives in Task 5's tested helpers; this task is wiring, checked by the typechecker, lint, knip and the acceptance run in Task 7.

`DiscoverSection` only shows the badge (no lock) and already sends whole `Book`s, so it picks up the Hardcover id through `buildLibraryLookupPayload` with no change.

- [ ] **Step 1: Combined-mode lock in the result views and the details modal**

`App.tsx`: on the `<ResultsSection ...>` element, next to `defaultContentType={effectiveContentType}`, add this. Add the same line on the `<DetailsModal ...>` element, next to its `defaultContentType={effectiveContentType}`. Its "Find Downloads" runs the same combined acquisition (`handleGetReleases`), so it must lock by the same rule.

```tsx
            combinedMode={effectiveCombinedMode}
```

`ResultsSection.tsx`: change the hook import to `import { useBothFormatMatches, useLibraryMatches } from '../hooks/useLibraryMatches';` and add `import { bothFormatsFor } from '../utils/libraryMatches';` directly above `import { isBookRequested } from '../utils/requestedBooks';`. Then add to `ResultsSectionProps`, after `defaultContentType?: string;`:

```ts
  /** Combined mode acquires both formats, so the lock needs both held. */
  combinedMode?: boolean;
```

Add `combinedMode = false,` to the destructured props after `defaultContentType,`. Then, right after `const libraryMatches = useLibraryMatches(books, defaultContentType);`, add:

```ts
  // Combined mode only: the same books asked about once per format.
  const bothFormatMatches = useBothFormatMatches(books, combinedMode);
```

Pass `bothFormatMatches={bothFormatMatches}` to `<ListView>`, and `bothFormats={bothFormatsFor(bothFormatMatches, book.id)}` to `<CardView>` and `<CompactView>`. Put each next to its `libraryMatch`/`libraryMatches` prop.

`CardView.tsx` and `CompactView.tsx`:
1. Add this to the props interface after `isRequested?: boolean;`:

   ```ts
     /** Both formats' holdings; set only in combined mode. */
     bothFormats?: FormatMatches;
   ```

2. Add `bothFormats,` to the destructured props after `isRequested = false,`.
3. Change the imports to `import { isLockedInLibrary } from '../../utils/libraryMatches';` and `import type { FormatMatches, LibraryMatch } from '../../utils/libraryMatches';`.
4. Replace both occurrences of

   ```tsx
   isInLibrary={isHeldInFormat(libraryMatch)}
   ```

   with

   ```tsx
   isInLibrary={isLockedInLibrary(libraryMatch, bothFormats)}
   ```

`ListView.tsx`:
1. Add `bothFormatMatches?: BothFormatMatches | null;` to `ListViewProps` after `openRequestKeys?: Set<string>;`.
2. Add `bothFormatMatches = null,` to the destructured props after `openRequestKeys = NO_OPEN_REQUESTS,`.
3. Change the imports to `import { bothFormatsFor, isLockedInLibrary } from '../../utils/libraryMatches';` and `import type { BothFormatMatches, LibraryMatch } from '../../utils/libraryMatches';`.
4. Replace

   ```tsx
   isInLibrary={isHeldInFormat(libraryMatches[book.id])}
   ```

   with

   ```tsx
   isInLibrary={isLockedInLibrary(
     libraryMatches[book.id],
     bothFormatsFor(bothFormatMatches, book.id),
   )}
   ```

`DetailsModal.tsx`:
1. Add this to `DetailsModalProps` after `defaultContentType?: string;`:

   ```ts
     /** Combined mode acquires both formats, so the lock needs both held. */
     combinedMode?: boolean;
   ```

2. Add `combinedMode = false,` to the destructured props after `defaultContentType,`.
3. Change the hook import to `import { useBothFormatMatches, useLibraryMatches } from '../hooks/useLibraryMatches';`, and the utils import to the following (`isHeldInFormat` is no longer used here):

   ```tsx
   import {
     applyInLibraryLock,
     bothFormatsFor,
     isLockedInLibrary,
     singleBookLookup,
   } from '../utils/libraryMatches';
   ```
4. Directly after the `const libraryMatch = useLibraryMatches(lookupBooks)[...]` line (it must stay above any early return, like every hook), add:

   ```tsx
     const bothFormatMatches = useBothFormatMatches(lookupBooks, combinedMode);
   ```

5. Replace

   ```tsx
     const effectiveButtonState = applyInLibraryLock(buttonState, isHeldInFormat(libraryMatch));
   ```

   with

   ```tsx
     const effectiveButtonState = applyInLibraryLock(
       buttonState,
       isLockedInLibrary(libraryMatch, bothFormatsFor(bothFormatMatches, `details-${book.id}`)),
     );
   ```

The badges (`InLibraryBadge`) are untouched: holding one format still shows them. The request-confirmation modal and the approval panel only advise and never lock, so they need no combined-mode change.

- [ ] **Step 2: Pass the identity from the three single-book surfaces**

`DetailsModal.tsx` — in the `lookupBooks` memo, add two arguments after `book?.content_type ?? defaultContentType,`:

```tsx
        book?.provider,
        book?.provider_id,
```

and add `book?.provider,` and `book?.provider_id,` to its dependency list (before `defaultContentType`).

`activity/ActivityCard.tsx` — import `hardcoverIdentity` alongside `singleBookLookup`. Replace the `lookupBooks` memo with:

```tsx
  const reviewIdentity = hardcoverIdentity(reviewBookData);
  const lookupBooks = useMemo(
    () =>
      singleBookLookup(
        `review-${reviewRecord.id}`,
        toOptionalText(reviewBookData.title),
        toOptionalText(reviewBookData.author),
        toOptionalText(reviewBookData.asin),
        toOptionalText(reviewBookData.isbn_13) ?? toOptionalText(reviewBookData.isbn_10),
        reviewRecord.content_type,
        reviewIdentity.provider,
        reviewIdentity.providerId,
      ),
    [
      reviewRecord.id,
      reviewRecord.content_type,
      reviewBookData.title,
      reviewBookData.author,
      reviewBookData.asin,
      reviewBookData.isbn_13,
      reviewBookData.isbn_10,
      reviewIdentity.provider,
      reviewIdentity.providerId,
    ],
  );
```

`RequestConfirmationModal.tsx` — import `hardcoverIdentity` alongside `singleBookLookup`. In the session component (the one destructuring `payload: CreateRequestPayload`), replace the `lookupBooks` memo with:

```tsx
  // The preview carries no provider fields; the request's own book data does.
  const requestIdentity = hardcoverIdentity(payload.book_data);
  const lookupBooks = useMemo(
    () =>
      preview
        ? singleBookLookup(
            'request-confirmation',
            preview.title,
            preview.author,
            preview.asin,
            preview.isbn_13 ?? preview.isbn_10,
            preview.content_type,
            requestIdentity.provider,
            requestIdentity.providerId,
          )
        : EMPTY_LOOKUP_BOOKS,
    [preview, requestIdentity.provider, requestIdentity.providerId],
  );
```

The banner's `isHeldInFormat(libraryMatch)` in this modal stays: it decides the wording of an advisory message, not a lock.

- [ ] **Step 3: Run the frontend gates**

Run: `make frontend-checks frontend-test` (or, from `src/frontend`: `npm run typecheck && npm run lint && npm run format:check && npm run knip && npm run test:unit`)
Expected: all pass. Combined mode makes two extra lookup requests per result set (one per format); other modes make none. `npm run knip` already exits non-zero on main (2 unused exports, 27 unused exported types, pre-existing); its output must be unchanged from main — nothing new from this branch. (`make frontend-checks` does not run knip.)

- [ ] **Step 4: Commit**

```bash
git add src/frontend/src/App.tsx src/frontend/src/components
git commit -m "feat(frontend): lock combined mode only on both formats; pass Hardcover identity from details, review and request

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: Full gates, release, acceptance

**Files:** none (release and verification only). In fleet-infra: `clusters/my-cluster/media/media.shelfmark.yaml` (image tag).

**Every push in this task is user-gated.** Stop and ask before `scripts/release-local.sh` (it pushes `main` + a tag and publishes to ghcr) and before pushing fleet-infra. If the auto-mode classifier refuses `release-local.sh`, report it and wait for the user to re-authorize — never decompose it into raw `docker buildx --push`.

- [ ] **Step 1: Run every gate on the branch**

Run: `make python-checks python-test frontend-checks frontend-test`
Also: `cd src/frontend && npm run knip; cd ../..`
Expected: green except the known pre-existing failures, which must be unchanged from `main`: 9 `tests/config/test_entrypoint_permissions.py` failures on macOS (bash 3.2 lacks `${1,,}`), 4 basedpyright `reportOptionalSubscript` errors at `shelfmark/main.py:2305-2308`, and knip's existing unused-export list. The suite needs `uv sync --all-extras` (plain `uv sync` lacks seleniumbase), and `make python-test` uses `-x`, so it stops at the entrypoint failures on macOS. Run `uv run pytest tests/ -q -m "not integration and not e2e"` to see the full count. Anything new and red gets fixed before going further.

- [ ] **Step 2: Release (user-gated)**

After the branch is merged to `main` and the user approves:

```bash
scripts/release-local.sh --dry-run   # confirm the version it picks
scripts/release-local.sh -y          # or with the explicit X.Y.Z-fork.N the user confirms
```

Expected: the script prints the pushed tag and the verified ghcr digest.

- [ ] **Step 3: Bump fleet-infra (user-gated)**

In `~/projects/fleet-infra`: set the Shelfmark image in `clusters/my-cluster/media/media.shelfmark.yaml` to the new `X.Y.Z-fork.N`. When the drift hook flags `clusters/my-cluster/media/CLAUDE.md`, re-read its bound notes against the manifest and run `drift link clusters/my-cluster/media/CLAUDE.md --doc-is-still-accurate`. Commit `media: bump shelfmark to X.Y.Z-fork.N (Hardcover-ID library matching)`, push after the user approves, then:

```bash
flux reconcile kustomization flux-system --with-source
kubectl -n media get pods -l app=shelfmark -w   # until the new pod is Running
```

Expected: the pod runs the new image (`kubectl -n media get pod -l app=shelfmark -o jsonpath='{..image}'`).

- [ ] **Step 4: Sync the Grimmory index**

Settings → Grimmory → **Sync Library Now**. Expected: success with ~148 items; the sync now does one detail read per book (~55 ms each, well under a minute).

Then confirm the keys landed:

```bash
POD=$(kubectl -n media get pod -l app=shelfmark -o jsonpath='{.items[0].metadata.name}')
kubectl exec -i -n media "$POD" -- python3 - <<'EOF'
import sqlite3
c = sqlite3.connect("file:/config/library_index.db?mode=ro", uri=True)
print(c.execute("select count(*) from library_item_keys where source='grimmory' and match_key like 'hardcover:%'").fetchone())
for row in c.execute("select i.title, k.match_key from library_items i join library_item_keys k on k.source=i.source and k.item_id=i.item_id where i.source='grimmory' and k.match_key like 'hardcover:%' and i.title like 'Overlord%' order by i.title"):
    print(row)
EOF
```

Expected: a non-zero count (the tagger wrote Hardcover ids onto the accepted books), and Overlord volumes listed with `hardcover:<digits>` keys.

- [ ] **Step 5: Acceptance search**

In Shelfmark (ebook mode, Hardcover provider), search **"Overlord"** and **"Mushoku Tensei"**.

Expected:
- Tagged volumes (e.g. Overlord 2, 3, 7, 8, 9; Mushoku Tensei 3–15) show **In library** and their acquire action is locked.
- Overlord manga results and untagged volumes show no badge (or only a "different edition" note), and stay acquirable.
- In combined mode, an ebook-only holding shows the badge but the action is **not** locked.

If a tagged volume does not badge, check its Grimmory `hardcoverBookId` against the search result's `provider_id` before suspecting the code.

- [ ] **Step 6: Record**

Update `SESSION_STATE.md` and the `grimmory-metadata-tagger` memory (P3 shipped; the `list_books` paging bug is fixed).
