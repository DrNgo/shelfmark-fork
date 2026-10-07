"""Tests for the book identity carried onto a download task (fork-only)."""

from shelfmark.core.book_identity import (
    BookIdentity,
    fill_identity_from_book_data,
    normalize_book_identity,
)

ISBN_13 = "9780316005142"  # check digit valid
ISBN_10 = "0316005142"  # the same book as ISBN-10
OTHER_ISBN_13 = "9780593135204"  # a different, valid book


class TestNormalizeBookIdentity:
    def test_keeps_a_complete_identity(self):
        identity = normalize_book_identity(
            {
                "provider": "hardcover",
                "provider_id": "886465",
                "isbn_13": ISBN_13,
                "asin": "B0BSHZ1234",
            }
        )

        assert identity == BookIdentity(
            provider="hardcover", provider_id="886465", isbn_13=ISBN_13, asin="B0BSHZ1234"
        )

    def test_trims_strings_and_blanks_become_none(self):
        identity = normalize_book_identity(
            {"provider": "  hardcover ", "provider_id": " 886465 ", "asin": "   "}
        )

        assert identity == BookIdentity(provider="hardcover", provider_id="886465")

    def test_a_numeric_provider_id_becomes_text(self):
        identity = normalize_book_identity({"provider": "hardcover", "provider_id": 886465})

        assert identity.provider_id == "886465"

    def test_provider_without_provider_id_drops_both(self):
        assert normalize_book_identity({"provider": "hardcover"}) == BookIdentity()
        assert normalize_book_identity({"provider_id": "886465"}) == BookIdentity()
        assert normalize_book_identity({"provider": "hardcover", "provider_id": " "}) == (
            BookIdentity()
        )

    def test_the_pair_rule_keeps_the_isbn_and_asin(self):
        identity = normalize_book_identity({"provider": "hardcover", "isbn_13": ISBN_13})

        assert identity == BookIdentity(isbn_13=ISBN_13)

    def test_manual_books_carry_no_provider_identity(self):
        identity = normalize_book_identity({"provider": "Manual", "provider_id": "manual-1"})

        assert identity == BookIdentity()

    def test_an_isbn_10_is_canonicalized_to_isbn_13(self):
        assert normalize_book_identity({"isbn_13": ISBN_10}).isbn_13 == ISBN_13
        assert normalize_book_identity({"isbn_10": ISBN_10}).isbn_13 == ISBN_13

    def test_hyphens_and_spaces_in_the_isbn_are_ignored(self):
        assert normalize_book_identity({"isbn_13": " 978-0-316-00514-2 "}).isbn_13 == ISBN_13

    def test_an_invalid_isbn_is_dropped(self):
        # Review Focus #2: a bad check digit, a placeholder, junk and non-strings.
        for value in (
            "9780316005143",
            "1234567890128",  # checksum-valid EAN, but not a 978/979 ISBN
            "0000000000",
            "N/A",
            9780316005142,
            True,
            "",
        ):
            assert normalize_book_identity({"isbn_13": value}).isbn_13 is None, repr(value)

    def test_a_bad_isbn_13_falls_back_to_a_valid_isbn_10(self):
        identity = normalize_book_identity({"isbn_13": "N/A", "isbn_10": ISBN_10})

        assert identity.isbn_13 == ISBN_13

    def test_non_string_values_are_ignored(self):
        identity = normalize_book_identity(
            {"provider": ["hardcover"], "provider_id": True, "asin": 12}
        )

        assert identity == BookIdentity()

    def test_as_dict_lists_the_four_fields(self):
        assert BookIdentity(provider="hardcover", provider_id="1").as_dict() == {
            "provider": "hardcover",
            "provider_id": "1",
            "isbn_13": None,
            "asin": None,
        }


class TestFillIdentityFromBookData:
    """An approved request fills identity the release lacks from its stored book data."""

    BOOK_DATA = {
        "title": "Overlord",
        "author": "Kugane Maruyama",
        "provider": "hardcover",
        "provider_id": "886465",
        "isbn_13": ISBN_13,
        "asin": "B0BSHZ1234",
    }

    def test_fills_every_field_a_bare_release_lacks(self):
        filled = fill_identity_from_book_data({"source": "prowlarr"}, self.BOOK_DATA)

        assert filled == {
            "source": "prowlarr",
            "provider": "hardcover",
            "provider_id": "886465",
            "isbn_13": ISBN_13,
            "asin": "B0BSHZ1234",
        }

    def test_the_release_keeps_its_own_values(self):
        release = {"provider": "hardcover", "provider_id": "886465", "isbn_13": ISBN_10}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled["isbn_13"] == ISBN_10
        assert filled["asin"] == "B0BSHZ1234"

    def test_a_release_for_a_different_book_takes_nothing(self):
        release = {"provider": "openlibrary", "provider_id": "OL1W"}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled == release

    def test_provider_names_compare_case_insensitively(self):
        release = {"provider": "Hardcover", "provider_id": "886465"}

        assert fill_identity_from_book_data(release, self.BOOK_DATA)["asin"] == "B0BSHZ1234"

    def test_a_same_provider_half_pair_is_replaced_as_a_pair(self):
        release = {"provider": "Hardcover"}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert (filled["provider"], filled["provider_id"]) == ("hardcover", "886465")
        assert filled["asin"] == "B0BSHZ1234"

    def test_a_half_pair_naming_another_provider_imports_nothing(self):
        """The release says Open Library but lost its id; the request is Hardcover.

        Adopting the request's pair would pin this release's ISBN to another
        book's Hardcover id, so the half pair is dropped and nothing is imported.
        """
        release = {"provider": "openlibrary", "isbn_13": OTHER_ISBN_13}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled["provider"] is None
        assert filled["provider_id"] is None
        assert filled["isbn_13"] == OTHER_ISBN_13
        assert "asin" not in filled

    def test_a_same_provider_half_pair_with_a_conflicting_isbn_imports_nothing(self):
        """The release names Hardcover without an id and carries another book's ISBN.

        Adopting the request's Hardcover id would pair it with this release's
        ISBN and the request's ASIN: three identifiers from two books.
        """
        release = {"provider": "hardcover", "isbn_13": OTHER_ISBN_13}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled["provider"] is None
        assert filled["provider_id"] is None
        assert filled["isbn_13"] == OTHER_ISBN_13
        assert "asin" not in filled

    def test_a_lone_provider_id_that_conflicts_imports_nothing(self):
        release = {"provider_id": "999999", "isbn_13": OTHER_ISBN_13}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled["provider"] is None
        assert filled["provider_id"] is None
        assert filled["isbn_13"] == OTHER_ISBN_13
        assert "asin" not in filled

    def test_a_conflicting_asin_imports_nothing(self):
        release = {"provider": "hardcover", "asin": "B0OTHER999"}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert filled["provider"] is None
        assert filled["provider_id"] is None
        assert filled["asin"] == "B0OTHER999"
        assert "isbn_13" not in filled

    def test_a_half_pair_whose_shared_identifiers_agree_is_completed(self):
        release = {"provider_id": "886465", "isbn_13": ISBN_10}

        filled = fill_identity_from_book_data(release, self.BOOK_DATA)

        assert (filled["provider"], filled["provider_id"]) == ("hardcover", "886465")
        assert filled["asin"] == "B0BSHZ1234"

    def test_an_isbn_10_in_book_data_is_used(self):
        book_data = {**self.BOOK_DATA, "isbn_13": None, "isbn_10": ISBN_10}

        assert fill_identity_from_book_data({}, book_data)["isbn_13"] == ISBN_13

    def test_book_data_without_identity_changes_nothing(self):
        release = {"source": "prowlarr", "provider": "hardcover"}

        assert fill_identity_from_book_data(release, {"title": "x"}) == release

    def test_non_dict_book_data_changes_nothing(self):
        assert fill_identity_from_book_data({"source": "x"}, None) == {"source": "x"}

    def test_the_input_is_not_mutated(self):
        release = {"source": "prowlarr"}

        fill_identity_from_book_data(release, self.BOOK_DATA)

        assert release == {"source": "prowlarr"}
