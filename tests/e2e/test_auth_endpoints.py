"""Unit tests for authentication endpoints.

These tests exercise the Flask route functions in `shelfmark.main` using Flask
request contexts. They do not require the full application stack.
"""

from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock, patch

import pytest

from shelfmark.audiobookshelf.client import AbsLoginUser

pytestmark = pytest.mark.e2e

_ABS_CONFIG = {"AUDIOBOOKSHELF_ENABLED": True, "AUDIOBOOKSHELF_URL": "http://abs.test"}


def _abs_config_get(key, default=None):
    return _ABS_CONFIG.get(key, default)


def _abs_user(**overrides):
    fields = {"id": "abs-1", "username": "listener", "type": "user", "is_active": True}
    fields.update(overrides)
    return AbsLoginUser(**fields)


def _as_response(result: Any):
    """Normalize Flask view return values to a Response-like object."""
    if isinstance(result, tuple) and len(result) == 2:
        resp, status = result
        resp.status_code = status
        return resp
    return result


def _config_getter(values: dict[str, Any]):
    def _get(key: str, default: Any = None, user_id: Any = None):
        return values.get(key, default)

    return _get


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background thread startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        # Reload to ensure patched orchestrator.start is used even if imported elsewhere.
        importlib.reload(main)
        return main


class TestGetAuthMode:
    def test_get_auth_mode_none(self, main_module):
        with patch.object(
            main_module.app_config, "get", side_effect=_config_getter({"AUTH_METHOD": "none"})
        ):
            assert main_module.get_auth_mode() == "none"

    def test_get_auth_mode_builtin(self, main_module):
        with (
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"AUTH_METHOD": "builtin"}),
            ),
            patch("shelfmark.core.auth_modes.has_local_password_admin", return_value=True),
        ):
            assert main_module.get_auth_mode() == "builtin"

    def test_get_auth_mode_builtin_without_local_admin_falls_back_to_none(self, main_module):
        with (
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"AUTH_METHOD": "builtin"}),
            ),
            patch("shelfmark.core.auth_modes.has_local_password_admin", return_value=False),
        ):
            assert main_module.get_auth_mode() == "none"

    def test_get_auth_mode_proxy(self, main_module):
        with patch.object(
            main_module.app_config,
            "get",
            side_effect=_config_getter(
                {"AUTH_METHOD": "proxy", "PROXY_AUTH_USER_HEADER": "X-Auth-User"}
            ),
        ):
            assert main_module.get_auth_mode() == "proxy"

    def test_get_auth_mode_cwa(self, main_module):
        with (
            patch.object(
                main_module.app_config, "get", side_effect=_config_getter({"AUTH_METHOD": "cwa"})
            ),
            patch.object(main_module, "CWA_DB_PATH", object()),
        ):
            assert main_module.get_auth_mode() == "cwa"

    def test_get_auth_mode_default_on_error(self, main_module):
        with patch.object(main_module.app_config, "get", side_effect=RuntimeError("boom")):
            assert main_module.get_auth_mode() == "none"


class TestAuthCheckEndpoint:
    def test_auth_check_no_auth(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="none"),
            main_module.app.test_request_context("/api/auth/check"),
        ):
            resp = _as_response(main_module.api_auth_check())
            data = resp.get_json()

        assert resp.status_code == 200
        assert data == {
            "authenticated": True,
            "auth_required": False,
            "auth_mode": "none",
            "is_admin": True,
        }

    def test_auth_check_builtin_not_authenticated(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/auth/check"),
        ):
            resp = _as_response(main_module.api_auth_check())
            data = resp.get_json()

        assert resp.status_code == 200
        assert data == {
            "authenticated": False,
            "auth_required": True,
            "auth_mode": "builtin",
            "is_admin": False,
            "username": None,
            "display_name": None,
        }

    def test_auth_check_builtin_authenticated(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/auth/check"),
        ):
            main_module.session["user_id"] = "admin"
            main_module.session["is_admin"] = True
            resp = _as_response(main_module.api_auth_check())
            data = resp.get_json()

        assert resp.status_code == 200
        assert data == {
            "authenticated": True,
            "auth_required": True,
            "auth_mode": "builtin",
            "is_admin": True,
            "username": "admin",
            "display_name": None,
        }

    def test_auth_check_proxy_includes_logout_url(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter(
                    {
                        "PROXY_AUTH_USER_HEADER": "X-Auth-User",
                        "PROXY_AUTH_LOGOUT_URL": "https://auth.example.com/logout",
                    }
                ),
            ),
            main_module.app.test_request_context("/api/auth/check"),
        ):
            main_module.session["user_id"] = "proxyuser"
            main_module.session["is_admin"] = True
            resp = _as_response(main_module.api_auth_check())
            data = resp.get_json()

        assert resp.status_code == 200
        assert data == {
            "authenticated": True,
            "auth_required": True,
            "auth_mode": "proxy",
            "is_admin": True,
            "username": "proxyuser",
            "display_name": None,
            "logout_url": "https://auth.example.com/logout",
        }


class TestLoginEndpoint:
    def test_login_proxy_mode_disabled(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"anything": "x"},
            ),
        ):
            resp = _as_response(main_module.api_login())
            data = resp.get_json()

        assert resp.status_code == 401
        assert data == {"error": "Proxy authentication is enabled"}

    def test_login_no_auth_success(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="none"),
            patch.object(main_module, "is_account_locked", return_value=False),
            main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"username": "anyuser", "password": "anypass", "remember_me": True},
            ),
        ):
            resp = _as_response(main_module.api_login())
            data = resp.get_json()
            assert main_module.session.get("user_id") == "anyuser"
            assert main_module.session.permanent is True

        assert resp.status_code == 200
        assert data == {"success": True}

    def test_login_builtin_success(self, main_module):
        mock_user_db = Mock()
        mock_user_db.get_user.return_value = {
            "id": 1,
            "username": "admin",
            "password_hash": "hash",
            "role": "admin",
        }
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            patch.object(main_module, "is_account_locked", return_value=False),
            patch.object(main_module, "user_db", mock_user_db),
            patch.object(main_module, "check_password_hash", return_value=True),
            main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"username": "admin", "password": "correct", "remember_me": False},
            ),
        ):
            resp = _as_response(main_module.api_login())
            data = resp.get_json()
            assert main_module.session.get("user_id") == "admin"

        assert resp.status_code == 200
        assert data == {"success": True}

    def test_login_cwa_provisions_db_user(self, main_module, tmp_path):
        cwa_db_path = tmp_path / "app.db"
        username = "cwa_test_user"

        conn = sqlite3.connect(cwa_db_path)
        conn.execute(
            "CREATE TABLE user (name TEXT PRIMARY KEY, password TEXT, role INTEGER, email TEXT)"
        )
        conn.execute(
            "INSERT INTO user (name, password, role, email) VALUES (?, ?, ?, ?)",
            (username, "hashed_password", 1, "cwa@example.com"),
        )
        conn.commit()
        conn.close()

        with (
            patch.object(main_module, "get_auth_mode", return_value="cwa"),
            patch.object(main_module, "is_account_locked", return_value=False),
            patch.object(main_module, "CWA_DB_PATH", cwa_db_path),
            patch.object(main_module, "check_password_hash", return_value=True),
            main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"username": username, "password": "correct", "remember_me": False},
            ),
        ):
            resp = _as_response(main_module.api_login())
            data = resp.get_json()
            assert main_module.session.get("user_id") == username
            assert main_module.session.get("is_admin") is True
            assert main_module.session.get("db_user_id") is not None

        assert resp.status_code == 200
        assert data == {"success": True}
        db_user = main_module.user_db.get_user(username=username)
        assert db_user["email"] == "cwa@example.com"
        assert db_user["role"] == "admin"
        assert db_user["auth_source"] == "cwa"

    def test_login_cwa_avoids_overwriting_local_username_collision(self, main_module, tmp_path):
        cwa_db_path = tmp_path / "app.db"
        username = "collision_admin"
        external_email = "collision.cwa@example.com"

        local_user = main_module.user_db.create_user(
            username=username,
            email="collision.local@example.com",
            role="admin",
            auth_source="builtin",
        )

        conn = sqlite3.connect(cwa_db_path)
        conn.execute(
            "CREATE TABLE user (name TEXT PRIMARY KEY, password TEXT, role INTEGER, email TEXT)"
        )
        conn.execute(
            "INSERT INTO user (name, password, role, email) VALUES (?, ?, ?, ?)",
            (username, "hashed_password", 1, external_email),
        )
        conn.commit()
        conn.close()

        with (
            patch.object(main_module, "get_auth_mode", return_value="cwa"),
            patch.object(main_module, "is_account_locked", return_value=False),
            patch.object(main_module, "CWA_DB_PATH", cwa_db_path),
            patch.object(main_module, "check_password_hash", return_value=True),
            main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"username": username, "password": "correct", "remember_me": False},
            ),
        ):
            resp = _as_response(main_module.api_login())
            data = resp.get_json()

            assert resp.status_code == 200
            assert data == {"success": True}
            assert main_module.session.get("user_id") == username
            assert main_module.session.get("db_user_id") is not None

        local_after = main_module.user_db.get_user(user_id=local_user["id"])
        assert local_after is not None
        assert local_after["auth_source"] == "builtin"
        assert local_after["email"] == "collision.local@example.com"

        provisioned_cwa_user = next(
            user
            for user in main_module.user_db.list_users()
            if user.get("auth_source") == "cwa" and user.get("email") == external_email
        )
        assert provisioned_cwa_user["username"].startswith(f"{username}__cwa")


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
            main_module.app.test_request_context("/api/auth/login", method="POST", json=json_body),
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
        self._login(main_module, _abs_user(), {"username": "listener", "password": "pw"})
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
        resp, _, _ = self._login(main_module, None, {"username": "listener", "password": "wrong"})
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
            resp, _, _ = self._login(main_module, None, {"username": "localonly", "password": "pw"})
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
        with patch.object(main_module, "upsert_external_user", side_effect=ValueError("boom")):
            resp, data, session_data = self._login(
                main_module, _abs_user(), {"username": "listener", "password": "pw"}
            )
        assert resp.status_code == 500
        assert data == {"error": "Authentication system error"}
        assert "user_id" not in session_data

    def test_not_found_provisioning_result_returns_500(self, main_module):
        with patch.object(main_module, "upsert_external_user", return_value=(None, "not_found")):
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

    def test_locked_account_returns_429_and_skips_abs_verification(self, main_module):
        # This test deliberately does NOT patch is_account_locked (unlike
        # _login()), so it actually exercises the abs branch's own lockout
        # re-check. That re-check is the only lockout enforcement reachable
        # for non-lowercase usernames, since the route-level check (~line
        # 2096) keys on the raw-case username while this branch records
        # failures under username.lower().
        main_module.failed_login_attempts.clear()
        main_module.failed_login_attempts["alice"] = {
            "count": main_module.MAX_LOGIN_ATTEMPTS,
            "lockout_until": datetime.now(UTC) + timedelta(minutes=30),
        }
        verify_mock = Mock()
        with (
            patch.object(main_module, "get_auth_mode", return_value="abs"),
            patch.object(main_module.app_config, "get", side_effect=_abs_config_get),
            patch("shelfmark.audiobookshelf.client.verify_abs_login", verify_mock),
            main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"username": "ALICE", "password": "x"},
            ),
        ):
            resp = _as_response(main_module.api_login())

        assert resp.status_code == 429
        verify_mock.assert_not_called()

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
    def test_root_and_admin_types_accepted_but_never_local_admin(self, main_module, abs_type):
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


class TestLogoutEndpoint:
    def test_logout_proxy_returns_logout_url(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter(
                    {"PROXY_AUTH_LOGOUT_URL": "https://auth.example.com/logout"}
                ),
            ),
            main_module.app.test_request_context("/api/auth/logout", method="POST"),
        ):
            main_module.session["user_id"] = "proxyuser"
            resp = _as_response(main_module.api_logout())
            data = resp.get_json()
            assert "user_id" not in main_module.session

        assert resp.status_code == 200
        assert data == {
            "success": True,
            "logout_url": "https://auth.example.com/logout",
        }

    def test_logout_basic(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/auth/logout", method="POST"),
        ):
            main_module.session["user_id"] = "admin"
            resp = _as_response(main_module.api_logout())
            data = resp.get_json()
            assert "user_id" not in main_module.session

        assert resp.status_code == 200
        assert data == {"success": True}


class TestRateLimiting:
    def test_record_failed_login_increments_count(self, main_module):
        main_module.failed_login_attempts.clear()

        is_locked = main_module.record_failed_login("testuser", "127.0.0.1")

        assert is_locked is False
        assert main_module.failed_login_attempts["testuser"]["count"] == 1

    def test_account_locked_after_max_attempts(self, main_module):
        main_module.failed_login_attempts.clear()

        for _ in range(main_module.MAX_LOGIN_ATTEMPTS):
            is_locked = main_module.record_failed_login("testuser", "127.0.0.1")

        assert is_locked is True
        assert "lockout_until" in main_module.failed_login_attempts["testuser"]

    def test_is_account_locked(self, main_module):
        main_module.failed_login_attempts.clear()
        main_module.failed_login_attempts["testuser"] = {
            "count": 10,
            "lockout_until": datetime.now(UTC) + timedelta(hours=1),
        }

        assert main_module.is_account_locked("testuser") is True

    def test_clear_failed_logins(self, main_module):
        main_module.failed_login_attempts["testuser"] = {"count": 5}

        main_module.clear_failed_logins("testuser")

        assert "testuser" not in main_module.failed_login_attempts
