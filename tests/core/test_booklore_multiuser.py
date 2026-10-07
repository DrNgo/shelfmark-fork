"""Tests for per-user BookLore library/path support."""

import pytest

from shelfmark.download.outputs.booklore import BookloreError, build_booklore_config


class TestBuildBookloreConfigWithOverrides:
    """build_booklore_config should resolve per-user library/path via config."""

    BASE_SETTINGS = {
        "BOOKLORE_HOST": "http://booklore:6060",
        "BOOKLORE_USERNAME": "admin",
        "BOOKLORE_PASSWORD": "secret",
        "BOOKLORE_DESTINATION": "library",
        "BOOKLORE_LIBRARY_ID": 1,
        "BOOKLORE_PATH_ID": 10,
    }

    def test_global_config_without_user_context(self):
        config = build_booklore_config(self.BASE_SETTINGS)
        assert config.library_id == 1
        assert config.path_id == 10

    def test_override_library_and_path_with_user_context(self, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 2
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 20
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.library_id == 2
        assert config.path_id == 20

    def test_override_library_only(self, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 3
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.library_id == 3
        assert config.path_id == 10  # falls back to global

    def test_override_path_only(self, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 30
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.library_id == 1  # falls back to global
        assert config.path_id == 30

    def test_none_user_context_uses_global(self):
        config = build_booklore_config(self.BASE_SETTINGS, user_id=None)
        assert config.library_id == 1
        assert config.path_id == 10
        assert config.upload_to_bookdrop is False

    def test_auth_fields_remain_global(self, monkeypatch):
        """Only Booklore library/path should be resolved with user context."""

        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 5
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 15
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.base_url == "http://booklore:6060"
        assert config.username == "admin"
        assert config.library_id == 5

    def test_bookdrop_destination_ignores_library_and_path_values(self):
        settings = {
            "BOOKLORE_HOST": "http://booklore:6060",
            "BOOKLORE_USERNAME": "admin",
            "BOOKLORE_PASSWORD": "secret",
            "BOOKLORE_DESTINATION": "bookdrop",
        }

        config = build_booklore_config(settings)

        assert config.upload_to_bookdrop is True
        assert config.library_id == 0
        assert config.path_id == 0
        assert config.refresh_after_upload is False


class FakeGrimmory:
    """Stands in for login + `GET /api/v1/libraries` during target resolution."""

    def __init__(self, libraries=None, error=None):
        self.libraries = libraries if libraries is not None else []
        self.error = error
        self.list_calls = 0
        self.login_configs = []

    def login(self, booklore_config):
        self.login_configs.append(booklore_config)
        if self.error is not None:
            raise self.error
        return "token"

    def list_libraries(self, booklore_config, token):
        self.list_calls += 1
        assert token == "token"
        return self.libraries


LIBRARIES = [
    {"id": 3, "name": "Fiction", "paths": [{"id": 3, "path": "/books/fiction"}]},
    {"id": 5, "name": "Light Novels", "paths": [{"id": 8, "path": "/books/ln"}]},
]


class TestExplicitDestinationKey:
    """An admin's ebook library choice is verified fresh, and never falls back."""

    BASE_SETTINGS = TestBuildBookloreConfigWithOverrides.BASE_SETTINGS

    @pytest.fixture
    def grimmory(self, monkeypatch):
        fake = FakeGrimmory(libraries=LIBRARIES)
        monkeypatch.setattr("shelfmark.download.outputs.booklore.booklore_login", fake.login)
        monkeypatch.setattr(
            "shelfmark.download.outputs.booklore.booklore_list_libraries", fake.list_libraries
        )
        return fake

    def test_a_verified_key_sets_the_upload_target(self, grimmory):
        config = build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:5:8")

        assert (config.library_id, config.path_id) == (5, 8)
        assert config.upload_to_bookdrop is False
        assert grimmory.list_calls == 1

    def test_the_key_beats_a_user_override(self, grimmory, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 3
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 3
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)

        config = build_booklore_config(
            self.BASE_SETTINGS, user_id=7, destination_key="grimmory:5:8"
        )

        assert (config.library_id, config.path_id) == (5, 8)

    def test_verification_uses_the_upload_credentials(self, grimmory):
        build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:5:8")

        used = grimmory.login_configs[0]
        assert (used.base_url, used.username, used.password) == (
            "http://booklore:6060",
            "admin",
            "secret",
        )

    def test_a_key_works_without_any_default_configured(self, grimmory):
        settings = {
            key: value
            for key, value in self.BASE_SETTINGS.items()
            if key not in {"BOOKLORE_LIBRARY_ID", "BOOKLORE_PATH_ID"}
        }

        config = build_booklore_config(settings, destination_key="grimmory:3:3")

        assert (config.library_id, config.path_id) == (3, 3)

    def test_a_missing_library_fails_naming_the_key(self, grimmory):
        with pytest.raises(BookloreError, match=r"grimmory:9:9"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:9:9")

    def test_a_path_from_another_library_fails(self, grimmory):
        with pytest.raises(BookloreError, match=r"grimmory:3:8"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:3:8")

    def test_a_malformed_key_fails_without_calling_grimmory(self, grimmory):
        # Review Focus #1: an audiobook key on an ebook task is malformed here.
        with pytest.raises(BookloreError, match=r"lib-kids"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="lib-kids")

        assert grimmory.login_configs == []

    def test_grimmory_down_fails_instead_of_using_the_default(self, monkeypatch):
        fake = FakeGrimmory(error=BookloreError("Could not connect to Grimmory"))
        monkeypatch.setattr("shelfmark.download.outputs.booklore.booklore_login", fake.login)

        with pytest.raises(BookloreError, match=r"grimmory:5:8.*Could not connect"):
            build_booklore_config(self.BASE_SETTINGS, destination_key="grimmory:5:8")

    def test_no_key_never_calls_grimmory(self, grimmory):
        for blank in (None, "", "   "):
            config = build_booklore_config(self.BASE_SETTINGS, destination_key=blank)
            assert (config.library_id, config.path_id) == (1, 10)

        assert grimmory.login_configs == []

    def test_bookdrop_ignores_the_key(self, grimmory):
        settings = {**self.BASE_SETTINGS, "BOOKLORE_DESTINATION": "bookdrop"}

        config = build_booklore_config(settings, destination_key="grimmory:9:9")

        assert config.upload_to_bookdrop is True
        assert (config.library_id, config.path_id) == (0, 0)
        assert grimmory.login_configs == []
