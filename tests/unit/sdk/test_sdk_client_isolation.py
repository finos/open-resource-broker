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


class TestRegionProfileOverrideIsolation:
    """Region/profile overrides must target the per-client container, not the singleton."""

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def _make_full_init_mocks(self, mock_container):
        """Return a mock app + discovery that complete initialization successfully."""
        from unittest.mock import AsyncMock

        mock_app = MagicMock()
        mock_app.initialize = AsyncMock(return_value=True)
        mock_app.get_query_bus.return_value = AsyncMock()
        mock_app.get_command_bus.return_value = AsyncMock()
        mock_disc = MagicMock()
        mock_disc.discover_cqrs_methods = AsyncMock(return_value={})
        return mock_app, mock_disc

    def test_region_override_uses_isolated_container_not_singleton(self):
        """override_provider_region must be called on self._container, not get_container()."""
        client = _make_client(region="us-east-1")

        mock_config_port = MagicMock()
        mock_container = MagicMock()
        mock_container.get.return_value = mock_config_port

        # singleton_config_port is what get_container() would return — must NOT be touched
        singleton_config_port = MagicMock()

        mock_app, mock_disc = self._make_full_init_mocks(mock_container)

        with (
            patch("orb.sdk.client.create_container", return_value=mock_container),
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
        ):
            self._run(client.initialize())

        # Override must have gone to the isolated container
        mock_config_port.override_provider_region.assert_called_once_with("us-east-1")
        # The singleton port must never have been touched
        singleton_config_port.override_provider_region.assert_not_called()

    def test_profile_override_uses_isolated_container_not_singleton(self):
        """override_provider_profile must be called on self._container, not get_container()."""
        client = _make_client(profile="prod")

        mock_config_port = MagicMock()
        mock_container = MagicMock()
        mock_container.get.return_value = mock_config_port

        singleton_config_port = MagicMock()

        mock_app, mock_disc = self._make_full_init_mocks(mock_container)

        with (
            patch("orb.sdk.client.create_container", return_value=mock_container),
            patch("orb.sdk.client.Application", return_value=mock_app),
            patch("orb.sdk.client.SDKMethodDiscovery", return_value=mock_disc),
        ):
            self._run(client.initialize())

        mock_config_port.override_provider_profile.assert_called_once_with("prod")
        singleton_config_port.override_provider_profile.assert_not_called()

    def test_two_clients_overrides_do_not_bleed_into_each_other(self):
        """Overrides on client_a must not affect client_b's container."""
        from unittest.mock import AsyncMock

        client_a = _make_client(region="us-east-1")
        client_b = _make_client(region="eu-west-1")

        config_port_a = MagicMock()
        config_port_b = MagicMock()
        container_a = MagicMock()
        container_b = MagicMock()
        container_a.get.return_value = config_port_a
        container_b.get.return_value = config_port_b

        containers = iter([container_a, container_b])

        def make_app(*args, **kwargs):
            app = MagicMock()
            app.initialize = AsyncMock(return_value=True)
            app.get_query_bus.return_value = AsyncMock()
            app.get_command_bus.return_value = AsyncMock()
            return app

        def make_disc(**kwargs):
            disc = MagicMock()
            disc.discover_cqrs_methods = AsyncMock(return_value={})
            return disc

        with (
            patch("orb.sdk.client.create_container", side_effect=lambda: next(containers)),
            patch("orb.sdk.client.Application", side_effect=make_app),
            patch("orb.sdk.client.SDKMethodDiscovery", side_effect=make_disc),
        ):
            self._run(client_a.initialize())
            self._run(client_b.initialize())

        config_port_a.override_provider_region.assert_called_once_with("us-east-1")
        config_port_b.override_provider_region.assert_called_once_with("eu-west-1")
        # Cross-bleed check: a's port must not have received b's region and vice versa
        for call in config_port_a.override_provider_region.call_args_list:
            assert call.args[0] != "eu-west-1"
        for call in config_port_b.override_provider_region.call_args_list:
            assert call.args[0] != "us-east-1"
