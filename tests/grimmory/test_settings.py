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

    def test_connection_fields_use_grimmory_labels(self):
        import shelfmark.grimmory.settings  # noqa: F401

        keys = _fields("grimmory")
        assert keys["BOOKLORE_HOST"].label == "Grimmory URL"

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
