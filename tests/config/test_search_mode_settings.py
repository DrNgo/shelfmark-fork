"""Tests for search mode settings definitions."""

import json
from unittest.mock import patch

import pytest

from shelfmark.config import settings as settings_module
from shelfmark.config.settings import search_mode_settings
from shelfmark.core.settings_registry import CustomComponentField, TagListField


def _search_mode_field(key: str):
    fields = {field.key: field for field in search_mode_settings() if hasattr(field, "key")}
    return fields[key]


def test_search_mode_settings_include_release_source_links_toggle():
    fields = {field.key: field for field in search_mode_settings() if hasattr(field, "key")}

    field = fields["SHOW_RELEASE_SOURCE_LINKS"]

    assert field.label == "Show Release Source Links"
    assert field.default is True
    assert field.user_overridable is False


def test_search_mode_settings_include_audible_topic_selector():
    fields = {field.key: field for field in search_mode_settings()}
    selector = fields["audible_topic_selector"]

    assert isinstance(selector, CustomComponentField)
    assert selector.component == "audible_topic_selector"
    assert len(selector.value_fields) == 1
    backing = selector.value_fields[0]
    assert isinstance(backing, TagListField)
    assert backing.key == "DEFAULT_DISCOVER_TOPIC"
    assert backing.default == []
    assert backing.env_supported is False
    assert backing.user_overridable is True


def test_on_save_search_mode_normalizes_topic_and_preserves_other_values():
    values = {
        "SEARCH_MODE": "universal",
        "DEFAULT_DISCOVER_TOPIC": [" Science Fiction & Fantasy ", " Fantasy "],
    }

    with patch(
        "shelfmark.config.settings.validate_audible_topic_path",
        return_value=(["Science Fiction & Fantasy", "Fantasy"], None),
    ):
        result = settings_module._on_save_search_mode(values)

    assert result == {
        "error": False,
        "values": {
            "SEARCH_MODE": "universal",
            "DEFAULT_DISCOVER_TOPIC": ["Science Fiction & Fantasy", "Fantasy"],
        },
    }


def test_on_save_search_mode_rejects_unverified_non_empty_topic_path():
    values = {"DEFAULT_DISCOVER_TOPIC": ["123"]}

    with patch(
        "shelfmark.config.settings.validate_audible_topic_path",
        return_value=(
            [],
            "The selected Audible topic is no longer available. Choose another topic.",
        ),
    ):
        result = settings_module._on_save_search_mode(values)

    assert result["error"] is True
    assert result["values"] == values
    assert "no longer available" in result["message"]


def test_on_save_search_mode_skips_topic_validation_when_not_present():
    values = {"SEARCH_MODE": "universal"}

    with patch(
        "shelfmark.config.settings.validate_audible_topic_path",
        side_effect=AssertionError("validator should not be called"),
    ):
        result = settings_module._on_save_search_mode(values)

    assert result == {"error": False, "values": values}
def test_book_language_is_user_overridable():
    fields = {field.key: field for field in search_mode_settings() if hasattr(field, "key")}

    field = fields["BOOK_LANGUAGE"]

    assert field.label == "Default Book Languages"
    assert field.default == ["en"]
    assert field.user_overridable is True


def test_book_language_stored_under_the_general_tab_still_resolves(tmp_path):
    """BOOK_LANGUAGE moved from the General tab to Search Mode with no migration.

    That is only safe because both tabs persist into the same settings.json, so an
    install that stored the value while the field lived on General keeps it. If the
    two tabs ever get separate files, every existing install silently falls back to
    the ["en"] default instead.
    """
    from shelfmark.core.settings_registry import _get_config_file_path, get_setting_value

    (tmp_path / "settings.json").write_text(json.dumps({"BOOK_LANGUAGE": ["de", "fr"]}))

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("BOOK_LANGUAGE", raising=False)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)

        assert _get_config_file_path("search_mode") == _get_config_file_path("general")
        assert get_setting_value(_search_mode_field("BOOK_LANGUAGE"), "search_mode") == ["de", "fr"]


def test_book_language_uses_its_default_on_a_fresh_install(tmp_path):
    from shelfmark.core.settings_registry import get_setting_value

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("BOOK_LANGUAGE", raising=False)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)

        assert get_setting_value(_search_mode_field("BOOK_LANGUAGE"), "search_mode") == ["en"]


def test_book_language_env_var_beats_the_stored_value(tmp_path):
    from shelfmark.core.settings_registry import get_setting_value, is_value_from_env

    (tmp_path / "settings.json").write_text(json.dumps({"BOOK_LANGUAGE": ["de", "fr"]}))
    field = _search_mode_field("BOOK_LANGUAGE")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("BOOK_LANGUAGE", "es,it")
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)

        assert is_value_from_env(field) is True
        assert get_setting_value(field, "search_mode") == ["es", "it"]
