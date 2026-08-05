"""Tests for search mode settings definitions."""

from unittest.mock import patch

from shelfmark.config import settings as settings_module
from shelfmark.config.settings import search_mode_settings
from shelfmark.core.settings_registry import CustomComponentField, TagListField


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
