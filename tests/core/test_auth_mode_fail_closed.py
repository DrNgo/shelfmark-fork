"""Auth-mode resolution must never FAIL OPEN.

`"none"` is the wide-open mode: every consumer treats it as "no accounts, allow
anonymous". Only an explicit AUTH_METHOD=none may produce it; a configured mode whose
prerequisites are missing, an unrecognised value, or a fault while reading the config
must all deny instead. The mode resolution itself is covered in test_oidc_integration;
these pin the fault paths, including the sqlite3.Error that once opened the app.
"""

import sqlite3
from unittest.mock import patch

import pytest

from shelfmark.core.auth_modes import AUTH_MODE_UNAVAILABLE, load_active_auth_mode

OPEN_MODE = "none"


@pytest.mark.parametrize(
    "error", [sqlite3.Error("db locked"), OSError("disk gone"), RuntimeError("boom")]
)
def test_fault_reading_config_does_not_open(error):
    with patch("shelfmark.core.config.config.get", side_effect=error):
        assert load_active_auth_mode() == AUTH_MODE_UNAVAILABLE


@pytest.mark.parametrize("auth_method", ["oidc", "builtin", "proxy", "cwa", "wat"])
def test_configured_or_unknown_method_never_resolves_open(auth_method):
    with patch("shelfmark.core.config.config.get", return_value=auth_method):
        assert load_active_auth_mode() != OPEN_MODE
