"""Auth-mode resolution must never FAIL OPEN.

`determine_auth_mode` historically ended in a bare `return "none"`, and `"none"` is the
wide-open mode: every consumer treats it as "no accounts, allow anonymous"
(`request_routes.py:75`, `activity_routes.py:141/245`, `audiobookshelf/routes.py:27`,
and the inverse `!= "none"` enforcement in `admin_routes.py:166` /
`self_user_routes.py:196`).

That made `"none"` mean two incompatible things at once:

  1. "the operator explicitly disabled authentication"  (AUTH_METHOD=none)
  2. "a configured mode's precondition failed, or something threw"

Meaning (2) is a security defect: a missing `OIDC_CLIENT_ID`, an unreadable `users.db`,
or a transient `sqlite3.Error` silently opened an app that commands the download clients
and writes into the Audiobookshelf library.

These tests pin the invariant: **when a real auth mode is configured, resolution may never
produce the open mode** — it degrades to deny, not to allow. Meaning (1) is preserved.
"""

import sqlite3
from unittest.mock import patch

from shelfmark.core.auth_modes import determine_auth_mode, load_active_auth_mode

OPEN_MODE = "none"


class TestConfiguredModeNeverDegradesToOpen:
    """A named mode whose precondition fails must deny, never open."""

    def test_oidc_missing_client_id_does_not_open(self):
        # The originally-reported defect: OIDC configured, one var absent.
        result = determine_auth_mode(
            {
                "AUTH_METHOD": "oidc",
                "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
                "OIDC_CLIENT_ID": "",
            },
            None,
            has_local_admin=True,
        )
        assert result != OPEN_MODE

    def test_oidc_missing_discovery_url_does_not_open(self):
        result = determine_auth_mode(
            {"AUTH_METHOD": "oidc", "OIDC_DISCOVERY_URL": "", "OIDC_CLIENT_ID": "shelfmark"},
            None,
            has_local_admin=True,
        )
        assert result != OPEN_MODE

    def test_abs_without_local_admin_does_not_open(self):
        # `abs` was believed to fail closed. It does so only w.r.t. the ABS connection
        # config -- it still fell through to the open mode when no local admin existed.
        result = determine_auth_mode({"AUTH_METHOD": "abs"}, None, has_local_admin=False)
        assert result != OPEN_MODE

    def test_builtin_without_local_admin_does_not_open(self):
        result = determine_auth_mode({"AUTH_METHOD": "builtin"}, None, has_local_admin=False)
        assert result != OPEN_MODE

    def test_proxy_without_user_header_does_not_open(self):
        result = determine_auth_mode(
            {"AUTH_METHOD": "proxy", "PROXY_AUTH_USER_HEADER": ""},
            None,
            has_local_admin=True,
        )
        assert result != OPEN_MODE

    def test_cwa_without_db_path_does_not_open(self):
        result = determine_auth_mode({"AUTH_METHOD": "cwa"}, None, has_local_admin=True)
        assert result != OPEN_MODE


class TestExplicitNoneIsStillHonoured:
    """Meaning (1) must survive: disabling auth on purpose still works."""

    def test_explicit_none_resolves_to_open(self):
        assert determine_auth_mode({"AUTH_METHOD": "none"}, None, has_local_admin=True) == OPEN_MODE

    def test_absent_auth_method_resolves_to_open(self):
        # No AUTH_METHOD key at all -- the documented default for a fresh install.
        assert determine_auth_mode({}, None, has_local_admin=True) == OPEN_MODE

    def test_unrecognised_auth_method_resolves_to_open(self):
        # An unknown string is not a "configured mode whose precondition failed";
        # it is indistinguishable from unset, so it keeps the historical default.
        assert (
            determine_auth_mode({"AUTH_METHOD": "wat"}, None, has_local_admin=True) == OPEN_MODE
        )


class TestRuntimeFailureDoesNotOpen:
    """An exception while RESOLVING the mode must not open the app."""

    def test_sqlite_error_reading_config_does_not_open(self):
        with patch("shelfmark.core.config.config.get", side_effect=sqlite3.Error("db locked")):
            result = load_active_auth_mode(None)
        assert result != OPEN_MODE

    def test_os_error_reading_config_does_not_open(self):
        with patch("shelfmark.core.config.config.get", side_effect=OSError("disk gone")):
            result = load_active_auth_mode(None)
        assert result != OPEN_MODE
