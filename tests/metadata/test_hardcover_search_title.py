from shelfmark.metadata_providers.hardcover import _compute_search_title


class TestHardcoverComputeSearchTitle:
    def test_prefers_subtitle_when_title_contains_subtitle(self):
        assert (
            _compute_search_title("Mistborn: The Final Empire", "The Final Empire")
            == "The Final Empire"
        )

    def test_prefers_main_title_when_subtitle_is_descriptive(self):
        assert (
            _compute_search_title(
                "The Cuckoo's Egg: Tracking a Spy Through the Maze of Computer Espionage",
                "Tracking a Spy Through the Maze of Computer Espionage",
            )
            == "The Cuckoo's Egg"
        )

    def test_does_not_use_subtitle_when_it_looks_like_series_position(self):
        assert _compute_search_title("The Stormlight Archive: Book 1", "Book 1") is None
        assert _compute_search_title("Some Series: Volume II", "Volume II") is None

    def test_strips_series_prefix_when_series_name_available(self):
        assert (
            _compute_search_title("Mistborn: The Final Empire", None, series_name="Mistborn")
            == "The Final Empire"
        )

    def test_strips_parenthetical_suffix(self):
        assert _compute_search_title("The Martian (Unabridged)", None) == "The Martian"
        assert _compute_search_title("The Martian", None) is None

    def test_returns_none_when_no_useful_simplification(self):
        assert _compute_search_title("Dune", None) is None

    def test_strips_a_medium_label_when_falling_back_to_the_full_title(self):
        # No subtitle: today's query was the full title, "(Light Novel)" included,
        # which no release name carries.
        assert (
            _compute_search_title(
                "High School DxD (Light Novel), Vol. 5: Hellcat of the Underworld Training Camp",
                None,
                series_name="High School DxD (Light Novel)",
            )
            == "High School DxD Vol. 5 Hellcat of the Underworld Training Camp"
        )
        assert (
            _compute_search_title(
                "The Rising of the Shield Hero (Light Novel), Vol. 8",
                None,
                series_name="The Rising of the Shield Hero (Light Novel)",
            )
            == "The Rising of the Shield Hero Vol. 8"
        )

    def test_keeps_a_manga_label(self):
        assert _compute_search_title("Overlord (Manga), Vol. 5", None) is None

    def test_a_chosen_subtitle_is_unchanged(self):
        assert (
            _compute_search_title(
                "High School DxD (Light Novel), Vol. 4: Vampire of the Suspended Classroom",
                "Vampire of the Suspended Classroom",
                series_name="High School DxD (Light Novel)",
            )
            == "Vampire of the Suspended Classroom"
        )
