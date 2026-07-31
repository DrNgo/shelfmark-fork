"""Tests that only admins can route a download to a specific library."""

from shelfmark.audiobookshelf.destinations import authorize_destination_key


class TestAuthorizeDestinationKey:
    """Multi-library routing is admin-only in v1, enforced server-side."""

    def test_keeps_the_key_for_an_admin(self):
        payload = {"source": "direct", "destination_key": "lib-kids"}

        assert authorize_destination_key(payload, is_admin=True) == payload

    def test_strips_the_key_for_a_non_admin(self):
        """A requester could otherwise POST any library id straight to the queue."""
        payload = {"source": "direct", "destination_key": "lib-kids"}

        authorized = authorize_destination_key(payload, is_admin=False)

        assert "destination_key" not in authorized
        assert authorized["source"] == "direct"

    def test_does_not_mutate_the_caller_payload(self):
        payload = {"source": "direct", "destination_key": "lib-kids"}

        authorize_destination_key(payload, is_admin=False)

        assert payload["destination_key"] == "lib-kids"

    def test_leaves_a_payload_without_a_key_untouched(self):
        payload = {"source": "direct"}

        assert authorize_destination_key(payload, is_admin=False) is payload

    def test_strips_a_key_nested_in_extra(self):
        payload = {"source": "direct", "extra": {"destination_key": "lib-kids", "author": "Ada"}}

        authorized = authorize_destination_key(payload, is_admin=False)

        assert "destination_key" not in authorized["extra"]
        assert authorized["extra"]["author"] == "Ada"
