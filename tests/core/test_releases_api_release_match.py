"""/api/releases annotates ebook releases with how they match the requested book.

Each release from every source gets ``extra["release_match"]`` (version 1) for an ebook
search of a metadata-provider book with no manual query. Audiobook searches, manual
queries and the manual provider get none, and a classifier failure on one release only
leaves that release unannotated.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release
from shelfmark.release_sources.irc.parser import parse_result_line
from shelfmark.release_sources.irc.source import IRCReleaseSource
from shelfmark.release_sources.prowlarr.source import _prowlarr_result_to_release

DXD = "High School DxD (Light Novel)"
DXD5_TITLE = f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp"
MAM_M4B_NAME = (
    "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp "
    "by Ichiei Ishibumi [ENG / M4B]"
)
IRC_LINE = (
    "!Bsk Ichiei Ishibumi - High School DxD Vol 5 Hellcat of the Underworld Training Camp.epub"
    " ::INFO:: 1.2MB"
)


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    test_client = main_module.app.test_client()
    with test_client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7
    return test_client


def _mam_m4b_release() -> Release:
    """A raw MyAnonamouse result in an ebook category, converted the way Prowlarr does."""
    return _prowlarr_result_to_release(
        {
            "title": MAM_M4B_NAME,
            "bookTitle": "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp",
            "guid": "https://www.myanonamouse.net/t/555",
            "indexer": "MyAnonamouse",
            "indexerId": 1,
            "protocol": "torrent",
            "size": 300_000_000,
            "seeders": 10,
            "leechers": 0,
            "categories": [{"id": 7020}],
        },
        "ebook",
        enable_format_detection=True,
    )


def _other_volume_release() -> Release:
    return Release(
        source="prowlarr",
        source_id="p-25",
        title="High School DxD - Volume 25 (epub)",
        content_type="book",
    )


def _irc_release(line: str = IRC_LINE) -> Release:
    source = IRCReleaseSource()
    source._online_servers = set()
    result = parse_result_line(line)
    assert result is not None
    return source._convert_to_releases([result], content_type="ebook")[0]


class _Source:
    def __init__(self, releases: list[Release]) -> None:
        self._releases = releases

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        return list(self._releases)

    def get_column_config(self):
        from shelfmark.release_sources import _default_column_config

        return _default_column_config()


class _Provider:
    def __init__(self, book: BookMetadata) -> None:
        self._book = book

    def get_book(self, book_id):
        return self._book


def _dxd5_book(title: str = DXD5_TITLE) -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id="dxd5",
        title=title,
        search_title="Hellcat of the Underworld Training Camp",
        authors=["Ichiei Ishibumi"],
        series_name=DXD,
        series_position=5,
    )


def _search(
    client,
    main_module,
    query: dict[str, str],
    book: BookMetadata | None = None,
    irc_releases: list[Release] | None = None,
):
    sources = {
        "prowlarr": _Source([_mam_m4b_release(), _other_volume_release()]),
        "irc": _Source(irc_releases if irc_releases is not None else [_irc_release()]),
    }
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch(
            "shelfmark.metadata_providers.get_provider",
            return_value=_Provider(book or _dxd5_book()),
        ),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[
                {"name": "prowlarr", "enabled": True},
                {"name": "irc", "enabled": True},
            ],
        ),
        patch("shelfmark.release_sources.get_source", side_effect=sources.__getitem__),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        response = client.get(
            "/api/releases", query_string={"provider": "hardcover", "book_id": "dxd5", **query}
        )
    assert response.status_code == 200
    return {release["source_id"]: release for release in response.get_json()["releases"]}


class TestEbookSearchAnnotates:
    def test_releases_from_two_sources_are_annotated(self, client, main_module):
        releases = _search(client, main_module, {"content_type": "ebook", "title": DXD5_TITLE})
        mam = next(r for r in releases.values() if r["indexer"] == "MyAnonamouse")

        # The raw MAM name says M4B: audio, whatever bookTitle and the ebook category say.
        assert mam["title"] == "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp"
        assert mam["extra"]["release_name"] == MAM_M4B_NAME
        assert mam["extra"]["release_match"] == {
            "v": 1,
            "volume": "match",
            "other_volume": None,
            "medium": "audio",
            "compatible": False,
            "fan_marker": False,
        }
        assert releases["p-25"]["extra"]["release_match"] == {
            "v": 1,
            "volume": "other",
            "other_volume": 25,
            "medium": "ebook",
            "compatible": True,
            "fan_marker": False,
        }
        # IRC: a clean title with the format declared separately.
        assert releases[IRC_LINE]["extra"]["release_match"] == {
            "v": 1,
            "volume": "match",
            "other_volume": None,
            "medium": "ebook",
            "compatible": True,
            "fan_marker": False,
        }

    def test_the_identity_comes_from_the_book_after_the_title_override(self, client, main_module):
        provider_book = _dxd5_book(title="Hellcat of the Underworld Training Camp")
        seen: list[dict[str, object]] = []
        real_build = main_module.build_ranking_identity

        def _spy(**kwargs):
            seen.append(kwargs)
            return real_build(**kwargs)

        with patch.object(main_module, "build_ranking_identity", _spy):
            _search(
                client,
                main_module,
                {"content_type": "ebook", "title": DXD5_TITLE},
                book=provider_book,
            )

        assert seen == [
            {
                "title": DXD5_TITLE,
                "current_query": "Hellcat of the Underworld Training Camp",
                "series_name": DXD,
                "series_position": 5,
                "authors": ["Ichiei Ishibumi"],
            }
        ]


class TestIrcEvidence:
    """IRC is classified on its original line, and only a detailed-pattern author counts."""

    OVERLORD2 = "Overlord (Light Novel), Vol. 2: The Dark Warrior"

    def _overlord_book(self) -> BookMetadata:
        return BookMetadata(
            provider="hardcover",
            provider_id="ol2",
            title=self.OVERLORD2,
            search_title="The Dark Warrior",
            authors=["Kugane Maruyama"],
            series_name="Overlord (Light Novel)",
            series_position=2,
        )

    @pytest.mark.parametrize(
        "line",
        [
            # Series-prefix layout: the parser splits "Overlord" off as the author.
            "!Bsk Overlord - Volume 2.epub",
            # Authorless layout: only the fallback pattern matches.
            "!Bsk Overlord Vol 2.epub ::INFO:: 1.1MB",
            "!Bsk Kugane Maruyama - Overlord 02 - The Dark Warrior.epub ::INFO:: 1.1MB",
        ],
    )
    def test_the_requested_volume_matches_in_every_layout(self, client, main_module, line):
        releases = _search(
            client,
            main_module,
            {"content_type": "ebook", "title": self.OVERLORD2},
            book=self._overlord_book(),
            irc_releases=[_irc_release(line)],
        )

        assert releases[line]["extra"]["release_match"]["volume"] == "match"

    def test_a_detailed_author_still_conflicts(self, client, main_module):
        line = "!Bsk James Patterson - Overlord 02.epub ::INFO:: 1.1MB"
        releases = _search(
            client,
            main_module,
            {"content_type": "ebook", "title": self.OVERLORD2},
            book=self._overlord_book(),
            irc_releases=[_irc_release(line)],
        )

        assert releases[line]["extra"]["release_match"]["volume"] == "unknown"

    def test_a_release_without_a_result_line_ignores_the_parsers_author(self, client, main_module):
        release = _irc_release()
        release.extra["full_line"] = ""
        release.extra["author"] = "James Patterson"

        releases = _search(
            client,
            main_module,
            {"content_type": "ebook", "title": DXD5_TITLE},
            irc_releases=[release],
        )

        assert releases[release.source_id]["extra"]["release_match"]["volume"] == "match"


class TestNoAnnotation:
    def test_an_audiobook_search_carries_no_release_match(self, client, main_module):
        releases = _search(client, main_module, {"content_type": "audiobook"})

        assert releases
        assert all("release_match" not in r["extra"] for r in releases.values())

    def test_a_manual_query_carries_no_release_match(self, client, main_module):
        releases = _search(
            client, main_module, {"content_type": "ebook", "manual_query": "dxd volume 5"}
        )

        assert releases
        assert all("release_match" not in r["extra"] for r in releases.values())

    def test_the_manual_provider_carries_no_release_match(self, client, main_module):
        source = _Source([_other_volume_release()])
        with (
            patch.object(main_module, "get_auth_mode", return_value="none"),
            patch(
                "shelfmark.release_sources.list_available_sources",
                return_value=[{"name": "prowlarr", "enabled": True}],
            ),
            patch("shelfmark.release_sources.get_source", return_value=source),
            patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
        ):
            response = client.get(
                "/api/releases",
                query_string={
                    "provider": "manual",
                    "book_id": "abc",
                    "title": "High School DxD Vol. 5",
                },
            )

        body = response.get_json()
        assert response.status_code == 200
        assert [r["extra"].get("release_match") for r in body["releases"]] == [None]


class TestFailureTolerance:
    def test_a_classifier_failure_leaves_only_that_release_unannotated(self, client, main_module):
        real_classify = main_module.classify_release

        def _flaky(**kwargs):
            if "Volume 25" in str(kwargs["name"]):
                raise RuntimeError("boom")
            return real_classify(**kwargs)

        with patch.object(main_module, "classify_release", _flaky):
            releases = _search(client, main_module, {"content_type": "ebook", "title": DXD5_TITLE})

        assert "release_match" not in releases["p-25"]["extra"]
        assert releases[IRC_LINE]["extra"]["release_match"]["volume"] == "match"


class TestHardening:
    def test_the_sources_release_objects_are_not_mutated(self, client, main_module):
        irc = _irc_release()

        releases = _search(
            client,
            main_module,
            {"content_type": "ebook", "title": DXD5_TITLE},
            irc_releases=[irc],
        )
        assert all("release_match" in r["extra"] for r in releases.values())

        assert "release_match" not in irc.extra

    def test_an_identity_failure_means_no_annotation_not_a_failed_request(
        self, client, main_module
    ):
        with patch.object(main_module, "build_ranking_identity", side_effect=RuntimeError("boom")):
            releases = _search(client, main_module, {"content_type": "ebook", "title": DXD5_TITLE})

        assert releases
        assert all("release_match" not in r["extra"] for r in releases.values())
