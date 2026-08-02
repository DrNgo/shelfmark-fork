"""Tests for the Grimmory tab-move and enablement migrations."""

import json

import pytest

# Importing this runs the @register_settings decorator in shelfmark.grimmory.settings,
# registering the "grimmory" tab. Without it, running this file alone leaves
# get_all_settings_tabs() without "grimmory", so initialize_default_configs() never
# creates grimmory.json, and TestMigrationOrdering passes even if the migrations are
# wired in after initialize_default_configs() — the exact bug this test exists to catch.
# Same idiom as tests/config/test_security.py:678 for the "security" tab.
import shelfmark.grimmory.settings  # noqa: F401
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
            {
                "BOOKLORE_LIBRARY_ID": "7",
                "BOOKLORE_PATH_ID": "3",
                "BOOKLORE_DESTINATION": "library",
            },
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
