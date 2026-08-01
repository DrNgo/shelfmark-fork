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
