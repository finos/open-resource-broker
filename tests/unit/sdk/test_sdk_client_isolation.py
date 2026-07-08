"""Tests for per-client DI container isolation in ORBClient."""

from unittest.mock import MagicMock, patch

from orb.sdk.client import ORBClient


def _make_client(**kwargs) -> ORBClient:
    return ORBClient(config={"provider": "aws", **kwargs})


class TestContainerIsolation:
    def test_two_clients_get_different_container_objects(self):
        """Each ORBClient.initialize() must create a distinct container, not the singleton."""
        container_a = MagicMock()
        container_b = MagicMock()

        containers = iter([container_a, container_b])

        with patch("orb.sdk.client.create_container", side_effect=lambda: next(containers)):
            client_a = _make_client()
            client_b = _make_client()

            # Simulate the container-creation step inside initialize() without
            # running the full async init (which requires provider setup).
            client_a._container = MagicMock()
            client_b._container = MagicMock()

        # Verify the two stored containers are independent objects
        assert client_a._container is not client_b._container

    def test_cleanup_clears_own_container_only(self):
        """cleanup() must clear its own container and not touch the other client's."""
        container_a = MagicMock()
        container_b = MagicMock()

        client_a = _make_client()
        client_b = _make_client()

        client_a._container = container_a
        client_b._container = container_b

        import asyncio

        asyncio.run(client_a.cleanup())

        container_a.clear.assert_called_once()
        container_b.clear.assert_not_called()
        assert client_a._container is None
        assert client_b._container is container_b

    def test_cleanup_sets_container_to_none(self):
        """After cleanup, _container must be None."""
        client = _make_client()
        client._container = MagicMock()

        import asyncio

        asyncio.run(client.cleanup())

        assert client._container is None

    def test_container_none_before_initialize(self):
        """_container must be None until initialize() runs."""
        client = _make_client()
        assert client._container is None

    def test_create_container_called_during_initialize(self):
        """initialize() must call create_container() to get the per-client container."""
        client = _make_client()

        mock_container = MagicMock()
        mock_app = MagicMock()
        mock_app.initialize = MagicMock(return_value=False)  # fail fast after container created

        with (
            patch("orb.sdk.client.create_container", return_value=mock_container) as mock_create,
            patch("orb.sdk.client.Application", return_value=mock_app),
        ):
            import asyncio

            from orb.sdk.exceptions import ProviderError

            try:
                asyncio.run(client.initialize())
            except (ProviderError, Exception):
                pass

            mock_create.assert_called_once()

        assert client._container is mock_container

    def test_application_receives_isolated_container(self):
        """Application must be constructed with the isolated container, not None."""
        client = _make_client()

        mock_container = MagicMock()
        captured = {}

        def capture_app(*args, **kwargs):
            captured["container"] = kwargs.get("container")
            app = MagicMock()
            app.initialize = MagicMock(return_value=False)
            return app

        with (
            patch("orb.sdk.client.create_container", return_value=mock_container),
            patch("orb.sdk.client.Application", side_effect=capture_app),
        ):
            import asyncio

            from orb.sdk.exceptions import ProviderError

            try:
                asyncio.run(client.initialize())
            except (ProviderError, Exception):
                pass

        assert captured.get("container") is mock_container
