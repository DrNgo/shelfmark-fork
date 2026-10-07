"""Tests for Torznab XML parsing helpers."""

import pytest

from shelfmark.release_sources.prowlarr.torznab import (
    TorznabError,
    parse_torznab_error,
    parse_torznab_xml,
)


def test_parse_torznab_xml_parses_basic_item():
    xml_text = """<?xml version="1.0"?>
<rss>
  <channel>
    <item>
      <title>Example Release</title>
      <guid>abc-123</guid>
      <link>https://example.com/download</link>
      <size>12345</size>
      <enclosure type="application/x-bittorrent" url="https://example.com/file.torrent" />
      <prowlarrindexer id="42">Test Indexer</prowlarrindexer>
      <newznab:attr xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"
                    name="seeders" value="10" />
      <newznab:attr xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"
                    name="peers" value="15" />
    </item>
  </channel>
</rss>
"""
    results = parse_torznab_xml(xml_text)

    assert len(results) == 1
    result = results[0]
    assert result["title"] == "Example Release"
    assert result["guid"] == "abc-123"
    assert result["indexerId"] == 42
    assert result["indexer"] == "Test Indexer"
    assert result["protocol"] == "torrent"
    assert result["seeders"] == 10
    assert result["leechers"] == 5


def test_parse_torznab_xml_rejects_entity_expansion_payload():
    xml_text = """<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<rss>
  <channel>
    <item>
      <title>&xxe;</title>
    </item>
  </channel>
</rss>
"""
    assert parse_torznab_xml(xml_text) == []


class TestParseTorznabError:
    @pytest.mark.parametrize(
        "body",
        [
            '<?xml version="1.0"?><error code="500" description="Request limit reached"/>',
            "<error code='500' description='Request limit reached'/>",
            '<error\n    code = "500"\n    description = "Request limit reached" />',
            '<nn:error xmlns:nn="http://www.newznab.com/DTD/2010/feeds/attributes/" '
            'code="500" description="Request limit reached"/>',
        ],
    )
    def test_quotes_whitespace_and_namespaces(self, body):
        assert parse_torznab_error(body) == TorznabError("500", "Request limit reached")

    @pytest.mark.parametrize(
        "body",
        [
            "",
            "not xml",
            '<?xml version="1.0"?><rss><channel></channel></rss>',
            '<rss><channel><item><title>About &lt;error code="500"&gt;</title></item></channel></rss>',
        ],
    )
    def test_anything_else_is_not_an_error(self, body):
        assert parse_torznab_error(body) is None

    @pytest.mark.parametrize(
        ("code", "description", "rate_limited"),
        [
            ("500", "", True),
            ("501", "", True),
            ("429", "", True),
            ("900", "API limit exceeded", True),
            ("100", "Incorrect user credentials", False),
        ],
    )
    def test_rate_limits(self, code, description, rate_limited):
        assert TorznabError(code, description).rate_limited is rate_limited
