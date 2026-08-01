# ABS Auth Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audiobookshelf users log into Shelfmark with their ABS credentials; Shelfmark validates via unauthenticated `POST {ABS_URL}/login`, auto-provisions a local `user`-role account linked by the stable ABS user id, and keeps its own session.

**Architecture:** Mirror the existing CWA auth-source pattern: a new `abs` branch in `api_login` validates externally and provisions through `upsert_external_user`, with a new `abs_subject` identity column mirroring `oidc_subject`. `abs` mode fails **closed**: `determine_auth_mode` gates only on a local admin existing, never on ABS config, so a missing ABS config yields 503s — not the wide-open `"none"` mode.

**Tech Stack:** Flask + SQLite (`users.db`), `requests`, pytest; React/TypeScript frontend (vitest/oxlint/oxfmt).

**Spec:** `docs/superpowers/specs/2026-08-01-abs-auth-source-design.md` — the authority for all behavior below.

## Global Constraints

- **Python 3.14 / PEP 758:** `except A, B:` (no parens, no `as`) is valid syntax used throughout this codebase. NEVER "fix" it. When you need `as e` with multiple types, parenthesize: `except (A, B) as e:`. A tuple *variable* must be STAR-EXPANDED inside a tuple display — `except (ImportError, *_OPERATIONAL_ERRORS) as e:` (the form `main.py:100` already uses). NEVER nest a tuple as a tuple element (`except (ValueError, _OPERATIONAL_ERRORS):`) — that raises `TypeError` at exception-match time.
- Python tests: `uv run pytest <path> -v` from the repo root.
- Python lint/format after each task: `uv run ruff check shelfmark tests && uv run ruff format shelfmark tests`.
- Frontend verification (Task 7 only): `npm run typecheck && npx oxlint src/ && npm run format:check && npm run test:unit && npm run build` from `src/frontend`.
- ~24 PRE-EXISTING full-suite failures on this macOS machine (bash 3.2 entrypoint tests + network/bypasser). Judge success by *targeted* test runs and by the full-suite failure list being unchanged, not by zero failures.
- Shelfmark never stores ABS passwords. No new env vars.
- Work on branch `feat/abs-auth-source` (create from `main` at execution start).
- TDD: every behavior change gets a failing test first. Commit at the end of every task.

---

### Task 0: Create the feature branch

- [ ] **Step 1: Branch**

```bash
git checkout -b feat/abs-auth-source
```

---

### Task 1: Auth mode plumbing (`auth_modes.py`)

**Files:**
- Modify: `shelfmark/core/auth_modes.py` (constants ~line 13-23, `determine_auth_mode` ~line 69, `is_user_active_for_auth_mode` ~line 126)
- Test: `tests/core/test_abs_auth.py` (new file)

**Interfaces:**
- Produces: `AUTH_SOURCE_ABS = "abs"` (imported by later tasks); `determine_auth_mode` returns `"abs"` when `AUTH_METHOD == "abs"` and a local admin is available; builtin users count as active under `abs` mode.
- Side effect relied on later: `UserDB._VALID_AUTH_SOURCES = frozenset(AUTH_SOURCE_SET)`, so extending `AUTH_SOURCES` automatically makes `auth_source="abs"` valid in `create_user`/`update_user`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_abs_auth.py`:

```python
"""Tests for the abs auth source: mode resolution and user-activity policy."""

from shelfmark.core.auth_modes import (
    AUTH_SOURCE_ABS,
    AUTH_SOURCE_SET,
    determine_auth_mode,
    is_user_active_for_auth_mode,
)


class TestDetermineAuthModeAbs:
    def test_returns_abs_when_local_admin_exists(self):
        result = determine_auth_mode({"AUTH_METHOD": "abs"}, None, has_local_admin=True)
        assert result == AUTH_SOURCE_ABS

    def test_returns_none_without_local_admin(self):
        result = determine_auth_mode({"AUTH_METHOD": "abs"}, None, has_local_admin=False)
        assert result == "none"

    def test_disable_local_auth_substitutes_for_local_admin(self):
        result = determine_auth_mode(
            {"AUTH_METHOD": "abs"},
            None,
            has_local_admin=False,
            disable_local_auth=True,
        )
        assert result == AUTH_SOURCE_ABS

    def test_abs_mode_does_not_gate_on_abs_connection_config(self):
        # Fail closed: no AUDIOBOOKSHELF_* keys in security_config at all,
        # mode must still resolve to "abs" (never degrade to open "none").
        security_config = {"AUTH_METHOD": "abs", "OIDC_DISCOVERY_URL": "", "OIDC_CLIENT_ID": ""}
        assert determine_auth_mode(security_config, None, has_local_admin=True) == AUTH_SOURCE_ABS

    def test_abs_registered_in_auth_source_set(self):
        assert "abs" in AUTH_SOURCE_SET


class TestIsUserActiveForAuthModeAbs:
    def test_builtin_user_active_under_abs_mode(self):
        user = {"auth_source": "builtin", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, AUTH_SOURCE_ABS) is True

    def test_abs_user_active_under_abs_mode(self):
        user = {"auth_source": "abs", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, AUTH_SOURCE_ABS) is True

    def test_abs_user_inactive_under_builtin_mode(self):
        user = {"auth_source": "abs", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, "builtin") is False

    def test_cwa_user_inactive_under_abs_mode(self):
        user = {"auth_source": "cwa", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, AUTH_SOURCE_ABS) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_abs_auth.py -v`
Expected: FAIL with `ImportError: cannot import name 'AUTH_SOURCE_ABS'`

- [ ] **Step 3: Implement**

In `shelfmark/core/auth_modes.py`, extend the constants block:

```python
AUTH_SOURCE_BUILTIN = "builtin"
AUTH_SOURCE_OIDC = "oidc"
AUTH_SOURCE_PROXY = "proxy"
AUTH_SOURCE_CWA = "cwa"
AUTH_SOURCE_ABS = "abs"
AUTH_SOURCES = (
    AUTH_SOURCE_BUILTIN,
    AUTH_SOURCE_OIDC,
    AUTH_SOURCE_PROXY,
    AUTH_SOURCE_CWA,
    AUTH_SOURCE_ABS,
)
```

In `determine_auth_mode`, add a branch after the `cwa` branch:

```python
    if auth_mode == AUTH_SOURCE_ABS and local_admin_available:
        # Deliberately NOT gated on the ABS connection being configured:
        # a missing ABS config must fail closed (mode stays "abs", ABS
        # logins get 503, builtin fallback still works) rather than
        # degrade to the wide-open "none" mode like oidc/proxy/cwa do.
        return AUTH_SOURCE_ABS
```

In `is_user_active_for_auth_mode`, change the builtin line:

```python
    if source == AUTH_SOURCE_BUILTIN:
        return auth_mode in (AUTH_SOURCE_BUILTIN, AUTH_SOURCE_OIDC, AUTH_SOURCE_ABS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_abs_auth.py -v`
Expected: PASS (all 9)

- [ ] **Step 5: Check no regressions in neighboring auth tests**

Run: `uv run pytest tests/core/test_oidc_integration.py tests/core/test_admin_users_api.py -q`
Expected: same results as before this task (no new failures)

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format shelfmark tests
git add shelfmark/core/auth_modes.py tests/core/test_abs_auth.py
git commit -m "feat(auth): register abs auth source with fail-closed mode resolution"
```

---

### Task 2: `abs_subject` column in UserDB (all four surfaces)

**Files:**
- Modify: `shelfmark/core/user_db.py` — `_CREATE_TABLES_SQL` (~line 27-38), `initialize` (~line 197), new migration method (~line 214 area), `create_user` (~line 287), `get_user` (~line 332), `_ALLOWED_UPDATE_COLUMNS`/`_USER_UPDATE_STATEMENTS` (~line 359-376)
- Test: `tests/core/test_abs_auth.py` (extend)

**Interfaces:**
- Consumes: `AUTH_SOURCE_ABS` from Task 1 (via `AUTH_SOURCE_SET` → `_VALID_AUTH_SOURCES`).
- Produces: `UserDB.create_user(..., abs_subject: str | None = None)`; `UserDB.get_user(abs_subject=...)`; `update_user(user_id, abs_subject=...)`; uniqueness enforced by index `idx_users_abs_subject`. Duplicate subject raises `ValueError` (create) — the existing `sqlite3.IntegrityError → ValueError` translation covers it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_abs_auth.py`:

```python
import sqlite3

import pytest

from shelfmark.core.user_db import UserDB


@pytest.fixture
def user_db(tmp_path):
    db = UserDB(str(tmp_path / "users.db"))
    db.initialize()
    return db


class TestUserDbAbsSubject:
    def test_create_and_lookup_by_abs_subject(self, user_db):
        created = user_db.create_user(
            username="absuser", auth_source="abs", abs_subject="abs-id-1"
        )
        found = user_db.get_user(abs_subject="abs-id-1")
        assert found is not None
        assert found["id"] == created["id"]
        assert found["abs_subject"] == "abs-id-1"
        assert found["auth_source"] == "abs"

    def test_abs_subject_must_be_unique(self, user_db):
        user_db.create_user(username="a1", auth_source="abs", abs_subject="dup")
        with pytest.raises(ValueError):
            user_db.create_user(username="a2", auth_source="abs", abs_subject="dup")

    def test_multiple_null_abs_subjects_allowed(self, user_db):
        user_db.create_user(username="plain1")
        user_db.create_user(username="plain2")  # both NULL abs_subject: fine

    def test_update_abs_subject(self, user_db):
        created = user_db.create_user(username="linkme")
        user_db.update_user(created["id"], abs_subject="abs-id-9", auth_source="abs")
        found = user_db.get_user(abs_subject="abs-id-9")
        assert found is not None
        assert found["id"] == created["id"]

    def test_migration_adds_column_to_legacy_db(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT,
                display_name  TEXT,
                password_hash TEXT,
                oidc_subject  TEXT UNIQUE,
                auth_source   TEXT NOT NULL DEFAULT 'builtin',
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute("INSERT INTO users (username) VALUES ('olduser')")
        conn.commit()
        conn.close()

        db = UserDB(str(db_path))
        db.initialize()
        old = db.get_user(username="olduser")
        assert old is not None
        assert old["abs_subject"] is None
        db.update_user(old["id"], abs_subject="migrated-id")
        assert db.get_user(abs_subject="migrated-id") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_abs_auth.py::TestUserDbAbsSubject -v`
Expected: FAIL — `create_user() got an unexpected keyword argument 'abs_subject'`

- [ ] **Step 3: Implement in `shelfmark/core/user_db.py`**

1. `_CREATE_TABLES_SQL` users table — add one line after `oidc_subject`:

```sql
    oidc_subject  TEXT UNIQUE,
    abs_subject   TEXT,
```

(No inline `UNIQUE` — uniqueness comes from the index below so fresh and migrated databases behave identically.)

2. New migration method next to `_migrate_auth_source_column`:

```python
    def _migrate_abs_subject_column(self, conn: sqlite3.Connection) -> None:
        """Ensure users.abs_subject exists with a unique index (ABS identity link)."""
        columns = conn.execute("PRAGMA table_info(users)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "abs_subject" not in column_names:
            conn.execute("ALTER TABLE users ADD COLUMN abs_subject TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_abs_subject ON users(abs_subject)"
        )
```

3. Call it in `initialize()` right after `self._migrate_auth_source_column(conn)`:

```python
                self._migrate_auth_source_column(conn)
                self._migrate_abs_subject_column(conn)
```

4. `create_user` — add parameter `abs_subject: str | None = None` after `oidc_subject`, and extend the INSERT:

```python
                cursor = conn.execute(
                    """INSERT INTO users (
                           username, email, display_name, password_hash, oidc_subject,
                           abs_subject, auth_source, role
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        username,
                        email,
                        display_name,
                        password_hash,
                        oidc_subject,
                        abs_subject,
                        auth_source,
                        role,
                    ),
                )
```

5. `get_user` — add parameter `abs_subject: str | None = None` and a branch:

```python
            elif oidc_subject is not None:
                row = conn.execute(
                    "SELECT * FROM users WHERE oidc_subject = ?", (oidc_subject,)
                ).fetchone()
            elif abs_subject is not None:
                row = conn.execute(
                    "SELECT * FROM users WHERE abs_subject = ?", (abs_subject,)
                ).fetchone()
```

Update the docstring: `"""Get a user by id, username, oidc_subject, or abs_subject. Returns None if not found."""`

6. Update allowlist and statements:

```python
    _ALLOWED_UPDATE_COLUMNS: ClassVar[frozenset[str]] = frozenset(
        {
            "email",
            "display_name",
            "password_hash",
            "oidc_subject",
            "abs_subject",
            "auth_source",
            "role",
        }
    )
    _USER_UPDATE_STATEMENTS: ClassVar[dict[str, str]] = {
        "email": "UPDATE users SET email = ? WHERE id = ?",
        "display_name": "UPDATE users SET display_name = ? WHERE id = ?",
        "password_hash": "UPDATE users SET password_hash = ? WHERE id = ?",
        "oidc_subject": "UPDATE users SET oidc_subject = ? WHERE id = ?",
        "abs_subject": "UPDATE users SET abs_subject = ? WHERE id = ?",
        "auth_source": "UPDATE users SET auth_source = ? WHERE id = ?",
        "role": "UPDATE users SET role = ? WHERE id = ?",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_abs_auth.py -v`
Expected: PASS

- [ ] **Step 5: Check no regressions**

Run: `uv run pytest tests/core/test_user_db.py tests/core/test_builtin_admin_sync.py -q`
Expected: no new failures

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format shelfmark tests
git add shelfmark/core/user_db.py tests/core/test_abs_auth.py
git commit -m "feat(auth): add unique abs_subject identity column to users db"
```

---

### Task 3: `abs_subject` support in external user linking

**Files:**
- Modify: `shelfmark/core/external_user_linking.py` — `_get_by_subject` (~line 52), `_build_updates` (~line 106), create path in `upsert_external_user` (~line 286-296)
- Test: `tests/core/test_abs_auth.py` (extend)

**Interfaces:**
- Consumes: `UserDB.get_user(abs_subject=...)` from Task 2.
- Produces: `upsert_external_user(user_db, auth_source="abs", username=..., role="user", subject_field="abs_subject", subject=<abs id>, collision_strategy=..., context=...)` links/creates rows carrying `abs_subject`. `abs_subject` is an explicit allowlisted branch — never a dynamic column name in SQL.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_abs_auth.py`:

```python
from shelfmark.core.external_user_linking import upsert_external_user


class TestUpsertExternalUserAbsSubject:
    def test_create_stores_abs_subject(self, user_db):
        user, action = upsert_external_user(
            user_db,
            auth_source="abs",
            username="listener",
            role="user",
            subject_field="abs_subject",
            subject="abs-uuid-1",
            context="test",
        )
        assert action == "created"
        assert user["abs_subject"] == "abs-uuid-1"
        assert user["auth_source"] == "abs"
        assert user["role"] == "user"

    def test_subject_match_survives_abs_username_rename(self, user_db):
        first, _ = upsert_external_user(
            user_db,
            auth_source="abs",
            username="oldname",
            role="user",
            subject_field="abs_subject",
            subject="stable-id",
            context="test",
        )
        second, action = upsert_external_user(
            user_db,
            auth_source="abs",
            username="newname",
            role="user",
            subject_field="abs_subject",
            subject="stable-id",
            context="test",
        )
        assert action == "updated"
        assert second["id"] == first["id"]
        # Local username intentionally stays stale (spec decision 7).
        assert second["username"] == "oldname"

    def test_suffix_collision_leaves_existing_user_untouched(self, user_db):
        local = user_db.create_user(
            username="admin", password_hash="x", auth_source="builtin", role="admin"
        )
        user, action = upsert_external_user(
            user_db,
            auth_source="abs",
            username="admin",
            role="user",
            subject_field="abs_subject",
            subject="abs-admin-id",
            collision_strategy="suffix",
            context="test",
        )
        assert action == "created"
        assert user["username"] == "admin_1"
        untouched = user_db.get_user(user_id=local["id"])
        assert untouched["auth_source"] == "builtin"
        assert untouched["role"] == "admin"

    def test_takeover_collision_converts_existing_user(self, user_db):
        local = user_db.create_user(username="bob", password_hash="x", auth_source="builtin")
        user, action = upsert_external_user(
            user_db,
            auth_source="abs",
            username="bob",
            role="user",
            subject_field="abs_subject",
            subject="abs-bob-id",
            collision_strategy="takeover",
            context="test",
        )
        assert action == "updated"
        assert user["id"] == local["id"]
        assert user["auth_source"] == "abs"
        assert user["abs_subject"] == "abs-bob-id"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_abs_auth.py::TestUpsertExternalUserAbsSubject -v`
Expected: `test_create_stores_abs_subject` FAILS with `assert None == "abs-uuid-1"` (subject silently dropped today); rename test FAILS with `action == "created"` (no subject match)

- [ ] **Step 3: Implement in `shelfmark/core/external_user_linking.py`**

1. `_get_by_subject`:

```python
def _get_by_subject(
    user_db: UserDB, subject_field: str | None, subject: str | None
) -> dict[str, Any] | None:
    if not subject_field or not subject:
        return None
    if subject_field == "oidc_subject":
        return user_db.get_user(oidc_subject=subject)
    if subject_field == "abs_subject":
        return user_db.get_user(abs_subject=subject)
    return None
```

2. `_build_updates` — after the `oidc_subject` line:

```python
    if subject_field == "oidc_subject" and subject:
        updates["oidc_subject"] = subject
    if subject_field == "abs_subject" and subject:
        updates["abs_subject"] = subject
```

3. Create path in `upsert_external_user` — after the `oidc_subject` create_kwargs line:

```python
    if subject_field == "oidc_subject" and subject:
        create_kwargs["oidc_subject"] = subject
    if subject_field == "abs_subject" and subject:
        create_kwargs["abs_subject"] = subject
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_abs_auth.py -v`
Expected: PASS

- [ ] **Step 5: Check no regressions**

Run: `uv run pytest tests/core/test_cwa_user_sync.py tests/core/test_oidc_auth.py -q`
Expected: no new failures

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format shelfmark tests
git add shelfmark/core/external_user_linking.py tests/core/test_abs_auth.py
git commit -m "feat(auth): allowlist abs_subject in external user linking"
```

---

### Task 4: `verify_abs_login` credential verifier

**Files:**
- Modify: `shelfmark/audiobookshelf/client.py` (append after `AudiobookshelfClient`; update module docstring)
- Test: `tests/audiobookshelf/test_client.py` (extend)

**Interfaces:**
- Produces: `verify_abs_login(url: str, username: str, password: str, *, timeout: int = 30) -> AbsLoginUser | None` and frozen dataclass `AbsLoginUser(id: str, username: str, type: str, is_active: bool)`. Contract: `None` on 401/403 or unusable user payload; raises `ValueError` on unexpected status / malformed or oversized body / blank URL; propagates `requests.exceptions.RequestException` on transport failure. Never sends an Authorization header; never follows redirects.

- [ ] **Step 1: Write the failing tests**

Append to `tests/audiobookshelf/test_client.py`:

```python
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

import pytest
import requests

from shelfmark.audiobookshelf.client import AbsLoginUser, verify_abs_login


def _login_response(status_code=200, payload=None, content=b"{}"):
    response = Mock()
    response.status_code = status_code
    response.content = content
    response.json.return_value = payload if payload is not None else {}
    return response


_VALID_PAYLOAD = {
    "user": {"id": "usr_123", "username": "alice", "type": "user", "isActive": True}
}


class TestVerifyAbsLogin:
    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_success_returns_normalized_user(self, mock_post):
        mock_post.return_value = _login_response(payload=_VALID_PAYLOAD)
        user = verify_abs_login("http://abs.local", "alice", "pw")
        assert user == AbsLoginUser(id="usr_123", username="alice", type="user", is_active=True)

    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_request_carries_no_auth_header_and_no_redirects(self, mock_post):
        mock_post.return_value = _login_response(payload=_VALID_PAYLOAD)
        verify_abs_login("http://abs.local", "alice", "pw", timeout=7)
        kwargs = mock_post.call_args.kwargs
        assert "Authorization" not in kwargs.get("headers", {})
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == 7
        assert kwargs["json"] == {"username": "alice", "password": "pw"}
        assert mock_post.call_args.args[0] == "http://abs.local/login"

    @pytest.mark.parametrize("status", [401, 403])
    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_invalid_credentials_return_none(self, mock_post, status):
        mock_post.return_value = _login_response(status_code=status)
        assert verify_abs_login("http://abs.local", "alice", "bad") is None

    @pytest.mark.parametrize("status", [302, 500, 502])
    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_unexpected_status_raises_value_error(self, mock_post, status):
        mock_post.return_value = _login_response(status_code=status)
        with pytest.raises(ValueError):
            verify_abs_login("http://abs.local", "alice", "pw")

    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_transport_error_propagates(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("down")
        with pytest.raises(requests.exceptions.RequestException):
            verify_abs_login("http://abs.local", "alice", "pw")

    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_malformed_json_raises_value_error(self, mock_post):
        response = _login_response()
        response.json.side_effect = requests.exceptions.JSONDecodeError("bad", "doc", 0)
        mock_post.return_value = response
        with pytest.raises(ValueError):
            verify_abs_login("http://abs.local", "alice", "pw")

    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_oversized_body_raises_value_error(self, mock_post):
        mock_post.return_value = _login_response(content=b"x" * 1_000_001)
        with pytest.raises(ValueError):
            verify_abs_login("http://abs.local", "alice", "pw")

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"user": "not-a-dict"},
            {"user": {"id": "", "username": "alice", "type": "user", "isActive": True}},
            {"user": {"id": "usr_1", "username": "", "type": "user", "isActive": True}},
        ],
    )
    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_unusable_user_payload_returns_none(self, mock_post, payload):
        mock_post.return_value = _login_response(payload=payload)
        assert verify_abs_login("http://abs.local", "alice", "pw") is None

    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_missing_is_active_normalizes_to_false(self, mock_post):
        payload = {"user": {"id": "usr_1", "username": "alice", "type": "user"}}
        mock_post.return_value = _login_response(payload=payload)
        user = verify_abs_login("http://abs.local", "alice", "pw")
        assert user is not None
        assert user.is_active is False

    @pytest.mark.parametrize("raw_is_active", [None, "yes", "true", 1])
    @patch("shelfmark.audiobookshelf.client.requests.post")
    def test_non_boolean_is_active_normalizes_to_false(self, mock_post, raw_is_active):
        # Only a strict boolean True counts as active — strings/ints from a
        # weird or hostile payload must not pass the eligibility check.
        payload = {
            "user": {
                "id": "usr_1",
                "username": "alice",
                "type": "user",
                "isActive": raw_is_active,
            }
        }
        mock_post.return_value = _login_response(payload=payload)
        user = verify_abs_login("http://abs.local", "alice", "pw")
        assert user is not None
        assert user.is_active is False

    def test_blank_url_raises_value_error(self):
        with pytest.raises(ValueError):
            verify_abs_login("", "alice", "pw")

    def test_abs_login_user_is_frozen(self):
        user = AbsLoginUser(id="a", username="b", type="user", is_active=True)
        with pytest.raises(FrozenInstanceError):
            user.id = "c"  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/audiobookshelf/test_client.py -k VerifyAbsLogin -v`
Expected: FAIL with `ImportError: cannot import name 'AbsLoginUser'`

- [ ] **Step 3: Implement in `shelfmark/audiobookshelf/client.py`**

Add `from dataclasses import dataclass` to the imports (the file already imports `requests`, `normalize_http_url`, `get_ssl_verify`, and has `logger`). Append after the `AudiobookshelfClient` class:

```python
_ABS_LOGIN_MAX_BYTES = 1_000_000
_ABS_LOGIN_REJECTED_STATUSES = (401, 403)
_HTTP_STATUS_OK = 200


@dataclass(frozen=True)
class AbsLoginUser:
    """Identity fields Shelfmark needs from a successful ABS login."""

    id: str
    username: str
    type: str
    is_active: bool


def verify_abs_login(
    url: str,
    username: str,
    password: str,
    *,
    timeout: int = 30,
) -> AbsLoginUser | None:
    """Validate ABS credentials via the unauthenticated ``POST /login``.

    Unlike ``AudiobookshelfClient`` (bearer-token, read-only GETs), this is a
    fresh request carrying only the end user's credentials: no Authorization
    header, no shared session, no redirect following.

    Returns ``None`` when ABS rejects the credentials or returns an unusable
    user payload. Raises ``ValueError`` on unexpected statuses or malformed
    bodies and lets ``requests.exceptions.RequestException`` propagate, so
    callers can tell "wrong password" from "ABS is down".
    """
    base_url = normalize_http_url(url)
    if not base_url:
        msg = "Audiobookshelf URL is not configured"
        raise ValueError(msg)

    response = requests.post(
        base_url + "/login",
        json={"username": username, "password": password},
        headers={"Accept": "application/json"},
        timeout=timeout,
        verify=get_ssl_verify(base_url),
        allow_redirects=False,
    )

    if response.status_code in _ABS_LOGIN_REJECTED_STATUSES:
        return None
    if response.status_code != _HTTP_STATUS_OK:
        msg = f"Unexpected Audiobookshelf login status: {response.status_code}"
        raise ValueError(msg)
    if len(response.content) > _ABS_LOGIN_MAX_BYTES:
        msg = "Audiobookshelf login response exceeded the size limit"
        raise ValueError(msg)

    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as e:
        msg = "Invalid JSON in Audiobookshelf login response"
        raise ValueError(msg) from e

    user = payload.get("user") if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        logger.warning("Audiobookshelf login response carried no user object")
        return None

    user_id = str(user.get("id") or "").strip()
    abs_username = str(user.get("username") or "").strip()
    if not user_id or not abs_username:
        logger.warning("Audiobookshelf login user is missing id or username")
        return None

    return AbsLoginUser(
        id=user_id,
        username=abs_username,
        type=str(user.get("type") or "").strip().lower(),
        is_active=user.get("isActive") is True,
    )
```

Also update the module docstring's read-only claim, e.g. append: `verify_abs_login is the one exception: an unauthenticated POST /login used by the abs auth source.`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/audiobookshelf/test_client.py -v`
Expected: PASS (new + all pre-existing client tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format shelfmark tests
git add shelfmark/audiobookshelf/client.py tests/audiobookshelf/test_client.py
git commit -m "feat(auth): add unauthenticated ABS login verifier"
```

---

### Task 5: `abs` branch in the login route

**Files:**
- Modify: `shelfmark/main.py` — imports near the existing `auth_modes` import, and `api_login` (insert the branch after the `cwa` branch ends ~line 2221, before the `Unknown authentication mode` fallthrough ~line 2223)
- Test: `tests/e2e/test_auth_endpoints.py` (extend `TestLoginEndpoint` area with a new class)

**Interfaces:**
- Consumes: `verify_abs_login`/`AbsLoginUser` (Task 4), `upsert_external_user` with `subject_field="abs_subject"` (Task 3), `normalize_auth_source` (existing).
- Produces: `POST /api/auth/login` behavior in `abs` mode per the spec's error table. Session keys identical to other modes: `user_id` (the *local* username, possibly suffixed), `db_user_id`, `is_admin`, `session.permanent`.
- Rate limiting: all lockout calls inside the branch use `rate_key = username.lower()` (username is already `.strip()`ed by the route).

- [ ] **Step 1: Write the failing tests**

Add to `tests/e2e/test_auth_endpoints.py` (same module as `TestLoginEndpoint`; it already imports `patch`, `sqlite3`, `_as_response`, and provides the `main_module` fixture with a real `user_db`):

```python
import pytest  # skip if the file already imports it

from shelfmark.audiobookshelf.client import AbsLoginUser

_ABS_CONFIG = {"AUDIOBOOKSHELF_ENABLED": True, "AUDIOBOOKSHELF_URL": "http://abs.test"}


def _abs_config_get(key, default=None):
    return _ABS_CONFIG.get(key, default)


def _abs_user(**overrides):
    fields = {"id": "abs-1", "username": "listener", "type": "user", "is_active": True}
    fields.update(overrides)
    return AbsLoginUser(**fields)


class TestLoginAbsMode:
    def _login(self, main_module, verify_result, json_body, *, verify_side_effect=None):
        verify_patch = patch(
            "shelfmark.audiobookshelf.client.verify_abs_login",
            side_effect=verify_side_effect,
            **({} if verify_side_effect else {"return_value": verify_result}),
        )
        with (
            patch.object(main_module, "get_auth_mode", return_value="abs"),
            patch.object(main_module, "is_account_locked", return_value=False),
            patch.object(main_module.app_config, "get", side_effect=_abs_config_get),
            verify_patch,
            main_module.app.test_request_context(
                "/api/auth/login", method="POST", json=json_body
            ),
        ):
            resp = _as_response(main_module.api_login())
            return resp, resp.get_json(), dict(main_module.session)

    def test_provisions_user_role_account_on_first_login(self, main_module):
        resp, data, session_data = self._login(
            main_module,
            _abs_user(),
            {"username": "listener", "password": "pw", "remember_me": False},
        )
        assert resp.status_code == 200
        assert data == {"success": True}
        assert session_data.get("user_id") == "listener"
        assert session_data.get("is_admin") is False
        db_user = main_module.user_db.get_user(abs_subject="abs-1")
        assert db_user is not None
        assert db_user["auth_source"] == "abs"
        assert db_user["role"] == "user"

    def test_relinks_by_subject_after_abs_rename(self, main_module):
        self._login(
            main_module, _abs_user(), {"username": "listener", "password": "pw"}
        )
        first = main_module.user_db.get_user(abs_subject="abs-1")
        resp, _, session_data = self._login(
            main_module,
            _abs_user(username="renamed"),
            {"username": "renamed", "password": "pw"},
        )
        assert resp.status_code == 200
        relinked = main_module.user_db.get_user(abs_subject="abs-1")
        assert relinked["id"] == first["id"]
        # Local username intentionally unchanged; session uses the local name.
        assert relinked["username"] == "listener"
        assert session_data.get("user_id") == "listener"

    def test_guest_type_rejected(self, main_module):
        resp, _, session_data = self._login(
            main_module,
            _abs_user(type="guest"),
            {"username": "listener", "password": "pw"},
        )
        assert resp.status_code == 401
        assert "user_id" not in session_data

    def test_unknown_type_rejected(self, main_module):
        resp, _, _ = self._login(
            main_module,
            _abs_user(type="superuser"),
            {"username": "listener", "password": "pw"},
        )
        assert resp.status_code == 401

    def test_inactive_user_rejected(self, main_module):
        resp, _, _ = self._login(
            main_module,
            _abs_user(is_active=False),
            {"username": "listener", "password": "pw"},
        )
        assert resp.status_code == 401

    def test_bad_credentials_rejected(self, main_module):
        resp, _, _ = self._login(
            main_module, None, {"username": "listener", "password": "wrong"}
        )
        assert resp.status_code == 401

    def test_abs_unreachable_returns_503_for_non_builtin_user(self, main_module):
        import requests as requests_lib

        resp, data, session_data = self._login(
            main_module,
            None,
            {"username": "listener", "password": "pw"},
            verify_side_effect=requests_lib.exceptions.ConnectionError("down"),
        )
        assert resp.status_code == 503
        assert data == {"error": "Authentication service unavailable"}
        assert "user_id" not in session_data

    def test_builtin_fallback_works_while_abs_down(self, main_module):
        import requests as requests_lib

        main_module.user_db.create_user(
            username="localadmin", password_hash="hash", auth_source="builtin", role="admin"
        )
        with patch.object(main_module, "check_password_hash", return_value=True):
            resp, data, session_data = self._login(
                main_module,
                None,
                {"username": "localadmin", "password": "pw"},
                verify_side_effect=requests_lib.exceptions.ConnectionError("down"),
            )
        assert resp.status_code == 200
        assert data == {"success": True}
        assert session_data.get("is_admin") is True

    def test_disable_local_auth_blocks_builtin_fallback_not_abs(self, main_module):
        main_module.user_db.create_user(
            username="localonly", password_hash="hash", auth_source="builtin"
        )
        with (
            patch.object(main_module, "DISABLE_LOCAL_AUTH", True),
            patch.object(main_module, "check_password_hash", return_value=True),
        ):
            # Local password is valid, but the fallback step is disabled, so the
            # attempt is forwarded to ABS which rejects it.
            resp, _, _ = self._login(
                main_module, None, {"username": "localonly", "password": "pw"}
            )
        assert resp.status_code == 401

        # ABS validation itself must still work under the flag.
        with patch.object(main_module, "DISABLE_LOCAL_AUTH", True):
            resp, data, session_data = self._login(
                main_module, _abs_user(), {"username": "listener", "password": "pw"}
            )
        assert resp.status_code == 200
        assert data == {"success": True}
        assert session_data.get("user_id") == "listener"

    def test_builtin_admin_collision_suffixes_instead_of_takeover(self, main_module):
        admin = main_module.user_db.create_user(
            username="admin", password_hash="hash", auth_source="builtin", role="admin"
        )
        resp, _, session_data = self._login(
            main_module,
            _abs_user(username="admin", id="abs-admin"),
            {"username": "admin", "password": "pw"},
        )
        assert resp.status_code == 200
        untouched = main_module.user_db.get_user(user_id=admin["id"])
        assert untouched["auth_source"] == "builtin"
        assert untouched["role"] == "admin"
        provisioned = main_module.user_db.get_user(abs_subject="abs-admin")
        assert provisioned["username"] == "admin_1"
        assert session_data.get("user_id") == "admin_1"
        assert session_data.get("is_admin") is False

    def test_non_admin_builtin_collision_takes_over(self, main_module):
        local = main_module.user_db.create_user(
            username="bob", password_hash="oldhash", auth_source="builtin"
        )
        resp, _, _ = self._login(
            main_module,
            _abs_user(username="bob", id="abs-bob"),
            {"username": "bob", "password": "pw"},
        )
        assert resp.status_code == 200
        taken = main_module.user_db.get_user(user_id=local["id"])
        assert taken["auth_source"] == "abs"
        assert taken["abs_subject"] == "abs-bob"

    def test_taken_over_row_old_password_no_longer_authenticates(self, main_module):
        # Full takeover sequence: builtin row with a password hash gets taken
        # over by an ABS login, keeping the stale hash on the row.
        local = main_module.user_db.create_user(
            username="carol", password_hash="oldhash", auth_source="builtin"
        )
        resp, _, _ = self._login(
            main_module,
            _abs_user(username="carol", id="abs-carol"),
            {"username": "carol", "password": "abspw"},
        )
        assert resp.status_code == 200
        taken = main_module.user_db.get_user(user_id=local["id"])
        assert taken["auth_source"] == "abs"
        assert taken["password_hash"] == "oldhash"  # takeover must not touch the hash

        # The stale hash is now inert: builtin-first step skips abs-source rows
        # even when the hash would match, and ABS rejects the old password.
        with patch.object(main_module, "check_password_hash", return_value=True):
            resp2, _, session_data = self._login(
                main_module, None, {"username": "carol", "password": "old"}
            )
        assert resp2.status_code == 401
        assert "user_id" not in session_data

    def test_abs_unconfigured_fails_closed_with_503(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="abs"),
            patch.object(main_module, "is_account_locked", return_value=False),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=lambda key, default=None: default,
            ),
            main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"username": "listener", "password": "pw"},
            ),
        ):
            resp = _as_response(main_module.api_login())
            session_data = dict(main_module.session)
        assert resp.status_code == 503
        assert "user_id" not in session_data

    def test_provisioning_failure_returns_500_without_session(self, main_module):
        with patch.object(
            main_module, "upsert_external_user", side_effect=ValueError("boom")
        ):
            resp, data, session_data = self._login(
                main_module, _abs_user(), {"username": "listener", "password": "pw"}
            )
        assert resp.status_code == 500
        assert data == {"error": "Authentication system error"}
        assert "user_id" not in session_data

    def test_not_found_provisioning_result_returns_500(self, main_module):
        with patch.object(
            main_module, "upsert_external_user", return_value=(None, "not_found")
        ):
            resp, data, session_data = self._login(
                main_module, _abs_user(), {"username": "listener", "password": "pw"}
            )
        assert resp.status_code == 500
        assert data == {"error": "Authentication system error"}
        assert "user_id" not in session_data

    def test_rate_limit_key_is_casefolded_and_trimmed(self, main_module):
        # Case/whitespace variants of one identifier must share a lockout
        # counter (the route strips, the abs branch lowercases).
        main_module.failed_login_attempts.clear()
        self._login(main_module, None, {"username": " Alice ", "password": "bad"})
        self._login(main_module, None, {"username": "ALICE", "password": "bad"})
        assert main_module.failed_login_attempts["alice"]["count"] == 2

    def test_eligibility_rejections_count_as_failed_logins(self, main_module):
        main_module.failed_login_attempts.clear()
        self._login(
            main_module, _abs_user(type="guest"), {"username": "listener", "password": "pw"}
        )
        self._login(
            main_module,
            _abs_user(is_active=False),
            {"username": "listener", "password": "pw"},
        )
        assert main_module.failed_login_attempts["listener"]["count"] == 2

    @pytest.mark.parametrize("abs_type", ["root", "admin"])
    def test_root_and_admin_types_accepted_but_never_local_admin(
        self, main_module, abs_type
    ):
        resp, _, session_data = self._login(
            main_module,
            _abs_user(type=abs_type, id=f"abs-{abs_type}", username=f"u_{abs_type}"),
            {"username": f"u_{abs_type}", "password": "pw"},
        )
        assert resp.status_code == 200
        assert session_data.get("is_admin") is False
        db_user = main_module.user_db.get_user(abs_subject=f"abs-{abs_type}")
        assert db_user is not None
        assert db_user["role"] == "user"

    def test_data_endpoint_requires_session_in_abs_mode(self, main_module):
        # Fail-closed proof: abs mode (even with ABS unconfigured) must gate
        # data endpoints exactly like any authenticated mode.
        with patch.object(main_module, "get_auth_mode", return_value="abs"):
            resp = main_module.app.test_client().get("/api/status")
        assert resp.status_code == 401
        assert resp.get_json() == {"error": "Unauthorized"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/e2e/test_auth_endpoints.py::TestLoginAbsMode -v`
Expected: FAIL — every test hits `500 {"error": "Unknown authentication mode"}` (no abs branch yet) EXCEPT `test_data_endpoint_requires_session_in_abs_mode`, which passes already: the middleware gates every non-`none` mode, so that one is a fail-closed regression guard, not a red test.

- [ ] **Step 3: Implement in `shelfmark/main.py`**

1. Extend existing imports: add `normalize_auth_source` to the existing `from shelfmark.core.auth_modes import ...` line, and add near the `upsert_cwa_user` import:

```python
from shelfmark.core.external_user_linking import upsert_external_user
```

2. Insert the branch in `api_login` after the `cwa` branch's `except` block and before the `# Should not reach here` comment:

```python
        # Audiobookshelf authentication mode
        if auth_mode == "abs":
            # Canonical rate-limit key: case/whitespace variants of one
            # identifier must share a lockout counter (username is already
            # stripped above).
            rate_key = username.lower()
            if is_account_locked(rate_key):
                return jsonify(
                    {
                        "error": f"Account temporarily locked due to multiple failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
                    }
                ), 429

            # Local builtin path first: builtin passwords never leave
            # Shelfmark, and builtin admins keep working while ABS is down.
            # DISABLE_LOCAL_AUTH switches local passwords off here exactly
            # as it does for builtin/oidc modes.
            if not DISABLE_LOCAL_AUTH and user_db is not None:
                try:
                    local_user = user_db.get_user(username=username)
                except _OPERATIONAL_ERRORS as e:
                    logger.error_trace(f"ABS-mode local user lookup failed: {e}")
                    local_user = None
                if (
                    local_user
                    and normalize_auth_source(
                        local_user.get("auth_source"), local_user.get("oidc_subject")
                    )
                    == "builtin"
                    and local_user.get("password_hash")
                    and check_password_hash(local_user["password_hash"], password)
                ):
                    session["user_id"] = local_user["username"]
                    session["db_user_id"] = local_user["id"]
                    session["is_admin"] = local_user["role"] == "admin"
                    session.permanent = remember_me
                    clear_failed_logins(rate_key)
                    logger.info(
                        "Login successful for user '%s' from IP %s (abs mode, builtin fallback, is_admin=%s)",
                        username,
                        ip_address,
                        session["is_admin"],
                    )
                    return jsonify({"success": True})

            abs_url = ""
            if app_config.get("AUDIOBOOKSHELF_ENABLED", False):
                abs_url = str(app_config.get("AUDIOBOOKSHELF_URL", "") or "").strip()
            if not abs_url:
                logger.error(
                    "AUTH_METHOD=abs but the Audiobookshelf connection is not configured"
                )
                return jsonify({"error": "Authentication service unavailable"}), 503

            import requests as _requests

            from shelfmark.audiobookshelf.client import verify_abs_login

            try:
                abs_user = verify_abs_login(abs_url, username, password)
            except (_requests.exceptions.RequestException, ValueError) as e:
                logger.error_trace(f"Audiobookshelf login check failed: {e}")
                return jsonify({"error": "Authentication service unavailable"}), 503

            if (
                abs_user is None
                or abs_user.type not in ("root", "admin", "user")
                or not abs_user.is_active
            ):
                return _failed_login_response(rate_key, ip_address)

            if user_db is None:
                logger.error("User database not available for abs auth")
                return jsonify({"error": "Authentication service unavailable"}), 503

            # Never take over the builtin admin account: converting it to an
            # external identity would demote the only admin.
            collision_strategy = "takeover"
            try:
                collision_target = user_db.get_user(username=abs_user.username)
            except _OPERATIONAL_ERRORS:
                collision_target = None
            if (
                collision_target
                and collision_target.get("role") == "admin"
                and normalize_auth_source(
                    collision_target.get("auth_source"),
                    collision_target.get("oidc_subject"),
                )
                == "builtin"
            ):
                collision_strategy = "suffix"

            try:
                db_user, action = upsert_external_user(
                    user_db,
                    auth_source="abs",
                    username=abs_user.username,
                    role="user",
                    subject_field="abs_subject",
                    subject=abs_user.id,
                    collision_strategy=collision_strategy,
                    context="abs_login",
                )
            except _OPERATIONAL_ERRORS as e:
                # ValueError is already a member of _OPERATIONAL_ERRORS
                # (main.py:99), so no extra tuple composition is needed.
                logger.error_trace(f"ABS user provisioning failed: {e}")
                return jsonify({"error": "Authentication system error"}), 500
            if db_user is None or action == "not_found":
                logger.error("ABS user provisioning returned no user (action=%s)", action)
                return jsonify({"error": "Authentication system error"}), 500

            session["user_id"] = db_user["username"]
            session["db_user_id"] = db_user["id"]
            session["is_admin"] = db_user["role"] == "admin"
            session.permanent = remember_me
            clear_failed_logins(rate_key)
            logger.info(
                "Login successful for user '%s' from IP %s (ABS auth, local_username=%s, action=%s, remember_me=%s)",
                username,
                ip_address,
                db_user["username"],
                action,
                remember_me,
            )
            return jsonify({"success": True})
```

Notes for the implementer:
- `username`, `password`, `remember_me`, `ip_address`, `DISABLE_LOCAL_AUTH`, `user_db`, `app_config`, `is_account_locked`, `clear_failed_logins`, `_failed_login_response`, `check_password_hash`, `LOCKOUT_DURATION_MINUTES`, `_OPERATIONAL_ERRORS` all already exist in scope — reuse them, do not re-derive.
- `verify_abs_login` is imported *inside* the branch (tests patch it at `shelfmark.audiobookshelf.client.verify_abs_login`, which works precisely because the import happens at call time).
- Also update the `api_login` docstring's first lines to mention ABS support.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/e2e/test_auth_endpoints.py -v`
Expected: PASS — all `TestLoginAbsMode` plus every pre-existing test in the file

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format shelfmark tests
git add shelfmark/main.py tests/e2e/test_auth_endpoints.py
git commit -m "feat(auth): abs login branch with builtin-first fallback and admin takeover guard"
```

---

### Task 6: Security settings — option, hint, save validation

**Files:**
- Modify: `shelfmark/config/security.py` (`auth_method_options` ~line 83-89; hint fields ~line 102-138)
- Modify: `shelfmark/config/security_handlers.py` (`on_save_security` ~line 56; new message constants ~line 15)
- Test: `tests/config/test_security.py` (extend)

**Interfaces:**
- Consumes: `shelfmark.core.config.config` for `AUDIOBOOKSHELF_ENABLED` / `AUDIOBOOKSHELF_URL` (they live in the audiobookshelf plugin config, NOT the security file — do not use `_load_effective_security_values` for them).
- Produces: AUTH_METHOD option `{"label": "Audiobookshelf", "value": "abs"}`; save rejected when `abs` is selected without a local password admin (unless `DISABLE_LOCAL_AUTH`) or without the ABS connection; `logger.warning` on an `http://` ABS URL.

- [ ] **Step 1: Write the failing tests**

Append to `tests/config/test_security.py` (check its existing imports; it exercises `on_save_security` — follow the file's local conventions for any fixture reuse):

```python
from unittest.mock import patch

from shelfmark.config.security_handlers import on_save_security


def _abs_app_config(enabled=True, url="https://abs.example.com"):
    values = {"AUDIOBOOKSHELF_ENABLED": enabled, "AUDIOBOOKSHELF_URL": url}
    return lambda key, default=None: values.get(key, default)


class TestOnSaveSecurityAbs:
    def test_rejects_abs_without_local_admin(self):
        with (
            patch("shelfmark.config.security_handlers.DISABLE_LOCAL_AUTH", False),
            patch(
                "shelfmark.config.security_handlers._has_local_password_admin",
                return_value=False,
            ),
        ):
            result = on_save_security({"AUTH_METHOD": "abs"})
        assert result["error"] is True
        assert "local admin" in result["message"].lower()

    def test_rejects_abs_without_abs_connection(self):
        with (
            patch("shelfmark.config.security_handlers.DISABLE_LOCAL_AUTH", False),
            patch(
                "shelfmark.config.security_handlers._has_local_password_admin",
                return_value=True,
            ),
            patch(
                "shelfmark.core.config.config.get",
                side_effect=_abs_app_config(enabled=False, url=""),
            ),
        ):
            result = on_save_security({"AUTH_METHOD": "abs"})
        assert result["error"] is True
        assert "audiobookshelf" in result["message"].lower()

    def test_accepts_abs_with_admin_and_connection(self):
        with (
            patch("shelfmark.config.security_handlers.DISABLE_LOCAL_AUTH", False),
            patch(
                "shelfmark.config.security_handlers._has_local_password_admin",
                return_value=True,
            ),
            patch(
                "shelfmark.core.config.config.get",
                side_effect=_abs_app_config(),
            ),
        ):
            result = on_save_security({"AUTH_METHOD": "abs"})
        assert result["error"] is False

    def test_warns_on_plain_http_abs_url(self):
        with (
            patch("shelfmark.config.security_handlers.DISABLE_LOCAL_AUTH", False),
            patch(
                "shelfmark.config.security_handlers._has_local_password_admin",
                return_value=True,
            ),
            patch(
                "shelfmark.core.config.config.get",
                side_effect=_abs_app_config(url="http://abs.media.svc"),
            ),
            patch("shelfmark.config.security_handlers.logger.warning") as mock_warn,
        ):
            result = on_save_security({"AUTH_METHOD": "abs"})
        assert result["error"] is False
        assert mock_warn.called

    def test_abs_option_registered(self):
        from shelfmark.core.settings_registry import get_settings_tab

        import shelfmark.config.security  # noqa: F401  (ensures registration ran)

        tab = get_settings_tab("security")
        assert tab is not None  # keeps basedpyright clean on the Optional return
        auth_field = next(f for f in tab.fields if getattr(f, "key", "") == "AUTH_METHOD")
        values = [opt["value"] for opt in auth_field.options]
        assert "abs" in values
```

Note: if `get_settings_tab` does not exist under that name in `settings_registry.py`, find the accessor the registry actually exposes (`grep -n "def get_" shelfmark/core/settings_registry.py`) and use that; the assertion payload stays the same.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/config/test_security.py -k Abs -v`
Expected: FAIL — `on_save_security` returns `{"error": False}` for abs without prerequisites; option list lacks `"abs"`; `logger` attribute missing from `security_handlers`

- [ ] **Step 3: Implement**

In `shelfmark/config/security.py`, extend `auth_method_options`:

```python
    auth_method_options = [
        {"label": "No Authentication", "value": "none"},
        {"label": "Local", "value": "builtin"},
        {"label": "Proxy Authentication", "value": "proxy"},
        {"label": "OIDC (OpenID Connect)", "value": "oidc"},
        {"label": "Calibre-Web Database", "value": "cwa"},
        {"label": "Audiobookshelf", "value": "abs"},
    ]
```

And add hints after the cwa hint block (reusing the existing `oidc_admin_hint` component and `_auth_condition` helper). The admin-requirement hint is OMITTED under `DISABLE_LOCAL_AUTH`, exactly like the existing OIDC hint at `security.py:111-122` — under that flag no local admin is required, so the hint would be wrong:

```python
        CustomComponentField(
            key="abs_auth_requirement",
            component="oidc_admin_hint",
            label=(
                "Audiobookshelf users sign in with their ABS credentials and are "
                "created as regular users on first login. Requires the "
                "Audiobookshelf connection (Audiobookshelf tab)."
            ),
            show_when=_auth_condition("abs"),
        ),
        *(
            []
            if DISABLE_LOCAL_AUTH
            else [
                CustomComponentField(
                    key="abs_admin_requirement",
                    component="oidc_admin_hint",
                    label=(
                        "A local admin account with a password is required for "
                        "fallback access while Audiobookshelf is unavailable."
                    ),
                    show_when=_auth_condition("abs"),
                ),
            ]
        ),
```

In `shelfmark/config/security_handlers.py`:

1. Add a logger (the module has none):

```python
from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)
```

2. Add message constants next to `_OIDC_LOCKOUT_MESSAGE`:

```python
_ABS_LOCKOUT_MESSAGE = "A local admin account with a password is required before enabling Audiobookshelf authentication. Use the 'Go to Users' button above to create one. This ensures you can still sign in if Audiobookshelf is unavailable."
_ABS_NOT_CONFIGURED_MESSAGE = "Audiobookshelf authentication requires the Audiobookshelf connection to be enabled and its URL configured in the Audiobookshelf settings tab."
```

3. In `on_save_security`, after the `if auth_method == "oidc":` block:

```python
    if auth_method == "abs":
        if not DISABLE_LOCAL_AUTH and not _has_local_password_admin():
            return {"error": True, "message": _ABS_LOCKOUT_MESSAGE, "values": normalized_values}

        from shelfmark.core.config import config as app_config

        abs_enabled = bool(app_config.get("AUDIOBOOKSHELF_ENABLED", False))
        abs_url = str(app_config.get("AUDIOBOOKSHELF_URL", "") or "").strip()
        if not abs_enabled or not abs_url:
            return {
                "error": True,
                "message": _ABS_NOT_CONFIGURED_MESSAGE,
                "values": normalized_values,
            }
        if abs_url.lower().startswith("http://"):
            logger.warning(
                "AUTH_METHOD=abs with a plain-HTTP Audiobookshelf URL (%s): "
                "credentials are forwarded unencrypted — acceptable only on a "
                "trusted in-cluster network",
                abs_url,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/config/test_security.py -v`
Expected: PASS (new + pre-existing)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check shelfmark tests && uv run ruff format shelfmark tests
git add shelfmark/config/security.py shelfmark/config/security_handlers.py tests/config/test_security.py
git commit -m "feat(auth): abs auth method option with lockout-safe save validation"
```

---

### Task 7: Frontend auth-source additions

**Files:**
- Modify: `src/frontend/src/services/api.ts:813` (`AdminAuthSource` union)
- Modify: `src/frontend/src/components/settings/users/useUserMutations.ts:47` (`authSourceLabel`)
- Modify: `src/frontend/src/components/settings/users/types.ts:52-69` (`AUTH_SOURCE_LABEL`, `AUTH_SOURCE_BADGE_CLASSES`, `canCreateLocalUsersForAuthMode`)

**Interfaces:**
- Consumes: nothing new from the backend at compile time; the backend now emits `auth_source: "abs"` on user records.
- Produces: TypeScript compiles with `'abs'` everywhere `AdminAuthSource` is exhaustively mapped. The two `Record<AuthSource, string>` maps make omissions a **compile error**, so `npm run typecheck` is the failing test here.

- [ ] **Step 1: Make the type change first (this is the "failing test")**

In `src/frontend/src/services/api.ts`:

```typescript
export type AdminAuthSource = 'builtin' | 'oidc' | 'proxy' | 'cwa' | 'abs';
```

Run: `cd src/frontend && npm run typecheck`
Expected: FAIL — `authSourceLabel`, `AUTH_SOURCE_LABEL`, and `AUTH_SOURCE_BADGE_CLASSES` are `Record<AuthSource, string>` missing the `abs` key

- [ ] **Step 2: Fix the compile errors**

`src/frontend/src/components/settings/users/useUserMutations.ts`:

```typescript
const authSourceLabel: Record<AdminUser['auth_source'], string> = {
  builtin: 'Local',
  oidc: 'OIDC',
  proxy: 'Proxy',
  cwa: 'CWA',
  abs: 'Audiobookshelf',
};
```

`src/frontend/src/components/settings/users/types.ts`:

```typescript
export const AUTH_SOURCE_LABEL: Record<AuthSource, string> = {
  builtin: 'Local',
  oidc: 'OIDC',
  proxy: 'Proxy',
  cwa: 'CWA',
  abs: 'Audiobookshelf',
};

export const AUTH_SOURCE_BADGE_CLASSES: Record<AuthSource, string> = {
  builtin: 'bg-zinc-500/15 opacity-70',
  oidc: 'bg-sky-500/15 text-sky-600 dark:text-sky-400',
  proxy: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  cwa: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  abs: 'bg-violet-500/15 text-violet-600 dark:text-violet-400',
};
```

Also in `types.ts`, extend `canCreateLocalUsersForAuthMode` — builtin fallback is active in abs mode, so admins must still be able to create local users:

```typescript
export const canCreateLocalUsersForAuthMode = (authMode?: string): boolean => {
  const normalized = (authMode || 'none').toLowerCase();
  return (
    normalized === 'none' ||
    normalized === 'builtin' ||
    normalized === 'oidc' ||
    normalized === 'abs'
  );
};
```

- [ ] **Step 3: Run the full frontend verification**

Run (from `src/frontend`): `npm run typecheck && npx oxlint src/ && npm run format:check && npm run test:unit && npm run build`
Expected: all clean/pass

- [ ] **Step 4: Commit**

```bash
git add src/frontend/src/services/api.ts src/frontend/src/components/settings/users/useUserMutations.ts src/frontend/src/components/settings/users/types.ts
git commit -m "feat(auth): surface abs auth source in admin UI"
```

---

### Task 8: Full verification sweep

**Files:** none (verification only; fix anything it surfaces)

- [ ] **Step 1: Targeted Python suites**

Run: `uv run pytest tests/core tests/audiobookshelf tests/config tests/e2e/test_auth_endpoints.py -q`
Expected: PASS (except any failures already present on `main` — verify by name against the known pre-existing macOS failures before treating one as yours)

- [ ] **Step 2: Full non-e2e-platform suite comparison**

Run: `uv run pytest tests/ -q --ignore=tests/e2e/platform 2>&1 | tail -5`
Expected: failure count/list identical to the `main` baseline (~24 pre-existing on this machine) — investigate ANY new name

- [ ] **Step 3: Static checks**

Run: `uv run ruff check shelfmark tests && uv run ruff format --check shelfmark tests && uv run basedpyright`
Expected: clean (basedpyright: only the pre-existing seleniumbase errors, if the browser extra isn't installed)

- [ ] **Step 4: Frontend suite (again, post-merge of all tasks)**

Run (from `src/frontend`): `npm run typecheck && npx oxlint src/ && npm run format:check && npm run test:unit && npm run build`
Expected: clean

- [ ] **Step 5: Commit any fixes; update SESSION_STATE.md phase status**

```bash
git add -A
git commit -m "chore: abs auth source verification sweep"
```

---

## Post-plan (not tasks — session-level follow-ups)

1. `/security-review` on `feat/abs-auth-source` (spec requires it before merge).
2. Merge to `main`; release via `scripts/release-local.sh` (fallback: `gh workflow run -R DrNgo/shelfmark-fork build-and-publish-docker-image.yml --ref <tag>` — tag pushes do NOT auto-trigger). Never re-push an existing image tag.
3. Deploy: bump image tag in `fleet-infra/clusters/my-cluster/media/media.shelfmark.yaml` (drift gate: re-read notes, `drift link clusters/my-cluster/media/CLAUDE.md --doc-is-still-accurate`), push, `flux reconcile source git flux-system && flux reconcile kustomization flux-system`, `kubectl -n media rollout status deploy/shelfmark`.
4. Post-deploy verification: an unauthenticated **data endpoint** on shelfmark.drngos.net returns 401 (SPA-shell 200s prove nothing); ABS user can log in; builtin admin can log in; `kubectl exec -n media deploy/shelfmark -- ls /opt/abs-enrich/` still lists the enrichment hook.
