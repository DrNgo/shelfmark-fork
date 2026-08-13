"""Tests for the strict title+author matcher behind the "in library" badge.

The badge is advisory, so a miss costs today's status quo — but a false
"already in library" silently suppresses a legitimate request. Every rule here
is therefore exact-after-normalization; nothing scores or fuzzy-matches.
"""

from shelfmark.library.matching import (
    KEY_SEPARATOR,
    asin_match_key,
    author_match_keys,
    build_match_keys,
    edition_qualifiers,
    normalize_asin,
    normalize_author,
    normalize_title,
    title_match_keys,
)


class TestNormalizeTitle:
    """Title normalization: strip presentation, keep the work's identity."""

    def test_casefolds_and_collapses_whitespace(self):
        assert normalize_title("  The   HOUSEMAID  ") == "housemaid"

    def test_strips_a_leading_article(self):
        assert normalize_title("The Housemaid") == normalize_title("Housemaid")

    def test_strips_leading_a_and_an(self):
        assert normalize_title("A Court of Thorns") == "court of thorns"
        assert normalize_title("An Ember in the Ashes") == "ember in the ashes"

    def test_keeps_an_article_that_is_not_leading(self):
        """ "The" mid-title is part of the work; only the leading one is noise."""
        assert normalize_title("Ember in the Ashes") == "ember in the ashes"

    def test_strips_diacritics(self):
        assert normalize_title("Les Misérables") == normalize_title("Les Miserables")

    def test_strips_punctuation(self):
        assert normalize_title("The Housemaid's Secret") == "housemaids secret"

    def test_strips_bracketed_edition_noise(self):
        """Audible/ABS decorate titles with (Unabridged), [Dramatized], etc."""
        assert normalize_title("The Housemaid (Unabridged)") == "housemaid"
        assert normalize_title("The Housemaid [Dramatized Adaptation]") == "housemaid"

    def test_keeps_the_subtitle(self):
        """Four distinct Housemaid titles differ only by suffix — never truncate."""
        assert normalize_title("The Housemaid's Secret") != normalize_title("The Housemaid")

    def test_returns_empty_for_unusable_input(self):
        assert normalize_title("") == ""
        assert normalize_title("   ") == ""
        assert normalize_title(None) == ""


class TestNormalizeAuthor:
    """Author normalization operates on one author name at a time."""

    def test_casefolds_and_strips_punctuation(self):
        assert normalize_author("Freida McFadden") == "freida mcfadden"
        assert normalize_author("J.R.R. Tolkien") == "jrr tolkien"

    def test_strips_diacritics(self):
        assert normalize_author("Gabriel García Márquez") == "gabriel garcia marquez"

    def test_returns_empty_for_unusable_input(self):
        assert normalize_author("") == ""
        assert normalize_author(None) == ""


class TestAuthorMatchKeys:
    """A name written "Last, First" must still match "First Last"."""

    def test_yields_the_plain_normalized_name(self):
        assert author_match_keys("Freida McFadden") == {"freida mcfadden"}

    def test_yields_both_orderings_for_a_single_comma(self):
        assert author_match_keys("McFadden, Freida") == {
            "mcfadden freida",
            "freida mcfadden",
        }

    def test_does_not_flip_a_multi_author_string(self):
        """Two commas means a list of authors, not an inverted single name."""
        keys = author_match_keys("King, Stephen, Straub")

        assert keys == {"king stephen straub"}

    def test_yields_nothing_for_unusable_input(self):
        assert author_match_keys("") == set()


class TestTitleMatchKeys:
    """Audiobookshelf splits title and subtitle; Shelfmark often has them joined."""

    def test_yields_the_title_alone_when_there_is_no_subtitle(self):
        assert title_match_keys("The Housemaid") == {"housemaid"}

    def test_yields_the_title_with_and_without_the_subtitle_joined(self):
        keys = title_match_keys("The Housemaid", subtitle="A Novel")

        assert keys == {"housemaid", "housemaid a novel"}

    def test_ignores_a_blank_subtitle(self):
        assert title_match_keys("The Housemaid", subtitle="   ") == {"housemaid"}


class TestBuildMatchKeys:
    """A match key pairs one title variant with one author variant."""

    def test_pairs_every_title_variant_with_every_author_variant(self):
        keys = build_match_keys("The Housemaid", "McFadden, Freida", subtitle="A Novel")

        assert keys == {
            "housemaid|mcfadden freida",
            "housemaid|freida mcfadden",
            "housemaid a novel|mcfadden freida",
            "housemaid a novel|freida mcfadden",
        }

    def test_the_same_book_written_differently_shares_a_key(self):
        """The whole point: an ABS item and a search result meet on one key."""
        library_keys = build_match_keys("The Housemaid", "McFadden, Freida", subtitle="A Novel")
        search_keys = build_match_keys("the housemaid: a novel", "Freida McFadden")

        assert library_keys & search_keys

    def test_different_books_by_one_author_share_nothing(self):
        housemaid = build_match_keys("The Housemaid", "Freida McFadden")
        secret = build_match_keys("The Housemaid's Secret", "Freida McFadden")

        assert not (housemaid & secret)

    def test_yields_nothing_without_both_a_title_and_an_author(self):
        """Half a key would match every other half-key — never emit one."""
        assert build_match_keys("The Housemaid", "") == set()
        assert build_match_keys("", "Freida McFadden") == set()


class TestNormalizeAsin:
    """ASINs are opaque identifiers, so the only safe rule is a strict shape."""

    def test_uppercases_and_strips(self):
        assert normalize_asin("  b0bshz1234 ") == "B0BSHZ1234"

    def test_accepts_an_isbn_10_used_as_an_asin(self):
        """Amazon reuses ISBN-10s as ASINs for print editions; still exact."""
        assert normalize_asin("0439023483") == "0439023483"

    def test_rejects_anything_that_is_not_ten_alphanumerics(self):
        """`N/A`, a URL, or a truncated field must never become a match key."""
        for junk in ("", None, "N/A", "B0BSHZ12", "B0BSHZ12345", "B0BS-Z1234", 7):
            assert normalize_asin(junk) == ""


class TestAsinMatchKey:
    """The ASIN key must live in its own namespace, clear of title keys."""

    def test_namespaces_the_key(self):
        assert asin_match_key("B0BSHZ1234") == "asin:B0BSHZ1234"

    def test_cannot_collide_with_a_title_key(self):
        """Title keys always contain the separator; ASIN keys never do."""
        assert KEY_SEPARATOR not in asin_match_key("B0BSHZ1234")

    def test_yields_nothing_for_a_malformed_asin(self):
        assert asin_match_key("N/A") == ""


class TestBuildMatchKeysWithAsin:
    """ASIN is an *additional* exact key, never a replacement."""

    def test_adds_the_asin_key_alongside_the_title_keys(self):
        keys = build_match_keys("The Housemaid", "Freida McFadden", asin="B0BSHZ1234")

        assert keys == {"housemaid|freida mcfadden", "asin:B0BSHZ1234"}

    def test_an_asin_alone_is_a_complete_identity(self):
        """A book with no usable author is still matchable by ASIN."""
        assert build_match_keys("", "", asin="B0BSHZ1234") == {"asin:B0BSHZ1234"}

    def test_two_editions_of_one_book_still_meet_on_title_and_author(self):
        """Different ASINs must not stop the title+author key from matching."""
        us = build_match_keys("The Housemaid", "Freida McFadden", asin="B0BSHZ1234")
        uk = build_match_keys("The Housemaid", "Freida McFadden", asin="B0BUKUK999")

        assert us & uk

    def test_a_malformed_asin_is_simply_ignored(self):
        keys = build_match_keys("The Housemaid", "Freida McFadden", asin="N/A")

        assert keys == {"housemaid|freida mcfadden"}


from shelfmark.library.matching import ISBN_KEY_PREFIX, isbn_match_key, normalize_isbn


class TestNormalizeIsbn:
    def test_keeps_a_valid_isbn13(self):
        assert normalize_isbn("9780593135204") == "9780593135204"

    def test_strips_hyphens_and_spaces(self):
        assert normalize_isbn("978-0-593-13520-4") == "9780593135204"
        assert normalize_isbn("  9780593135204 ") == "9780593135204"

    def test_converts_isbn10_to_isbn13(self):
        assert normalize_isbn("0306406152") == "9780306406157"

    def test_accepts_an_x_check_digit(self):
        assert normalize_isbn("080442957X") == "9780804429573"

    def test_rejects_a_bad_isbn13_check_digit(self):
        assert normalize_isbn("9780593135205") == ""

    def test_rejects_a_bad_isbn10_check_digit(self):
        assert normalize_isbn("0306406153") == ""

    def test_passes_through_a_979_isbn13(self):
        # 979 ISBNs have no ISBN-10 equivalent and must survive untouched.
        assert normalize_isbn("9791234567896") == "9791234567896"

    def test_rejects_junk(self):
        for junk in ["", "N/A", "none", "97805931352", "97805931352049", None, 12345]:
            assert normalize_isbn(junk) == ""

    def test_rejects_zero_filled_placeholders(self):
        # Both zero-filled forms pass their own check-digit arithmetic, so they
        # need rejecting by hand or every placeholder row matches every other.
        assert normalize_isbn("0000000000") == ""
        assert normalize_isbn("0000000000000") == ""


class TestIsbnMatchKey:
    def test_namespaces_the_key(self):
        assert isbn_match_key("0306406152") == f"{ISBN_KEY_PREFIX}9780306406157"

    def test_returns_empty_for_junk(self):
        assert isbn_match_key("N/A") == ""


class TestBuildMatchKeysWithIsbn:
    def test_an_isbn_is_enough_on_its_own(self):
        keys = build_match_keys(None, None, isbn="9780593135204")
        assert keys == {f"{ISBN_KEY_PREFIX}9780593135204"}

    def test_isbn10_and_isbn13_produce_the_same_key(self):
        assert build_match_keys(None, None, isbn="0306406152") == build_match_keys(
            None, None, isbn="9780306406157"
        )

    def test_isbn_adds_to_title_author_keys_rather_than_replacing_them(self):
        keys = build_match_keys("The Housemaid", "Freida McFadden", isbn="9780593135204")
        assert f"{ISBN_KEY_PREFIX}9780593135204" in keys
        assert any(KEY_SEPARATOR in key for key in keys)

    def test_a_bad_isbn_contributes_no_key(self):
        assert build_match_keys(None, None, isbn="9780593135205") == set()


class TestEditionQualifiers:
    """Bracketed markers the title keys deliberately throw away.

    `normalize_title` deletes brackets so an edition marker cannot break a
    match. That is right for keying and wrong for ranking: once two items key
    together, the marker is the only thing left saying they are different
    recordings.
    """

    def test_a_title_with_no_brackets_has_no_qualifiers(self):
        assert edition_qualifiers("Dungeon Crawler Carl") == frozenset()

    def test_extracts_a_bracketed_marker(self):
        assert edition_qualifiers("Dungeon Crawler Carl (Audio Immersion Tunnel)") == frozenset(
            {"audio immersion tunnel"}
        )

    def test_extracts_every_marker_in_a_title(self):
        assert edition_qualifiers("The Final Empire (Part 1 of 3) (Dramatized Adaptation)") == (
            frozenset({"part 1 of 3", "dramatized adaptation"})
        )

    def test_square_brackets_count_too(self):
        assert edition_qualifiers("Preservation [Dramatized Adaptation]") == frozenset(
            {"dramatized adaptation"}
        )

    def test_normalizes_the_marker(self):
        """Same marker, different punctuation and case, must compare equal."""
        assert edition_qualifiers("Harry Potter (Full-Cast Edition)") == edition_qualifiers(
            "Harry Potter (full cast edition)"
        )

    def test_ignores_unabridged_and_abridged(self):
        """These describe completeness, not edition, and are near-universal noise."""
        assert edition_qualifiers("The Song Rising (Unabridged)") == frozenset()
        assert edition_qualifiers("The Song Rising (Abridged)") == frozenset()

    def test_keeps_a_real_marker_alongside_an_ignored_one(self):
        assert edition_qualifiers("Riftborne (Unabridged) (Dramatized Adaptation)") == frozenset(
            {"dramatized adaptation"}
        )

    def test_an_empty_bracket_contributes_nothing(self):
        assert edition_qualifiers("Something ()") == frozenset()

    def test_a_non_string_has_no_qualifiers(self):
        assert edition_qualifiers(None) == frozenset()
