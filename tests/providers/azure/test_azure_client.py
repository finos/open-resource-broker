"""Focused tests for Azure client and auth behavior."""

import asyncio
import sys
import threading
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.config import PerformanceConfig
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import (
    AuthenticationError,
    AzureConfigurationError,
)
from orb.providers.azure.infrastructure.azure_client import (
    AzureClient,
    AzureClientRuntimeConfig,
)
from orb.providers.azure.infrastructure.services.arm_resource_id_parser import (
    ArmResourceIdParser,
)
from orb.providers.azure.infrastructure.services.azure_network_identity_resolver import (
    AzureNetworkIdentityResolver,
)


class TestAzureAuthStrategy:
    @staticmethod
    def _build_auth_context():
        from orb.infrastructure.adapters.ports.auth import AuthContext

        return AuthContext(
            method="GET",
            path="/providers/azure/health",
            headers={},
            query_params={},
        )

    @pytest.mark.asyncio
    async def test_auth_strategy_returns_failed_result_for_expected_azure_auth_error(self):
        from orb.providers.azure.auth.azure_auth_strategy import AzureAuthStrategy

        token_provider = MagicMock()
        strategy = AzureAuthStrategy(logger=MagicMock(), token_provider=token_provider)
        token_provider.get_auth_error_types.return_value = (RuntimeError,)
        token_provider.get_access_token.side_effect = RuntimeError("credential unavailable")

        result = await strategy.authenticate(self._build_auth_context())

        assert result.status.name == "FAILED"
        assert "credential unavailable" in result.error_message

    @pytest.mark.asyncio
    async def test_auth_strategy_propagates_unexpected_errors(self):
        from orb.providers.azure.auth.azure_auth_strategy import AzureAuthStrategy

        token_provider = MagicMock()
        strategy = AzureAuthStrategy(logger=MagicMock(), token_provider=token_provider)
        token_provider.get_auth_error_types.return_value = (ValueError,)
        token_provider.get_access_token.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await strategy.authenticate(self._build_auth_context())

    @pytest.mark.asyncio
    async def test_auth_strategy_delegates_to_token_provider(self):
        from orb.providers.azure.auth.azure_auth_strategy import AzureAuthStrategy

        token_provider = MagicMock()
        token_provider.get_auth_error_types.return_value = (RuntimeError,)
        token_provider.get_access_token.return_value = "access-token"
        strategy = AzureAuthStrategy(logger=MagicMock(), token_provider=token_provider)

        result = await strategy.authenticate(self._build_auth_context())

        assert result.status.name == "SUCCESS"
        assert result.token == "access-token"
        assert result.permissions == []
        token_provider.get_access_token.assert_called_once_with(
            "https://management.azure.com/.default"
        )

    @pytest.mark.asyncio
    async def test_auth_strategy_awaits_async_token_provider(self):
        from orb.providers.azure.auth.azure_auth_strategy import AzureAuthStrategy

        async_token_provider = MagicMock()
        async_token_provider.get_auth_error_types.return_value = (RuntimeError,)
        async_token_provider.get_access_token = AsyncMock(return_value="access-token")
        strategy = AzureAuthStrategy(
            logger=MagicMock(),
            async_token_provider=async_token_provider,
        )

        result = await strategy.authenticate(self._build_auth_context())

        assert result.status.name == "SUCCESS"
        assert result.token == "access-token"
        async_token_provider.get_access_token.assert_awaited_once_with(
            "https://management.azure.com/.default"
        )


class TestAzureClientOperationalBehavior:
    @staticmethod
    def _build_runtime_config(
        azure_config: AzureProviderConfig | None = None,
        perf_config: PerformanceConfig | None = None,
    ) -> AzureClientRuntimeConfig:
        return AzureClientRuntimeConfig(
            azure_config=azure_config
            or AzureProviderConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                resource_group="rg-explicit",
                region="westeurope",
            ),
            performance_config=perf_config or PerformanceConfig(),
        )

    @classmethod
    def _build_client(cls, logger: MagicMock | None = None) -> AzureClient:
        return AzureClient(
            runtime_config=cls._build_runtime_config(),
            logger=logger or MagicMock(),
        )

    @staticmethod
    def _build_partial_client() -> AzureClient:
        client = object.__new__(AzureClient)
        client._logger = MagicMock()
        client._lazy_init_lock = threading.RLock()
        client._closed = False
        client._credentials_validated = False
        client._async_credential = None
        client._async_compute_client = None
        client._async_network_client = None
        client._async_resource_client = None
        client._async_subscription_client = None
        client._pending_async_close_task = None
        client._arm_resource_id_parser = ArmResourceIdParser()
        client._network_identity_resolver = AzureNetworkIdentityResolver(
            async_network_client_getter=client.get_async_network_client,
            logger=client._logger,
            arm_resource_id_parser=client._arm_resource_id_parser,
            network_lookup_error_types=client._network_lookup_error_types,
        )
        return client

    def test_azure_client_uses_explicit_runtime_config(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(azure_config=azure_config),
            logger=MagicMock(),
        )

        assert client.subscription_id == azure_config.subscription_id
        assert client.resource_group == "rg-explicit"
        assert client.region_name == "westeurope"

    @pytest.mark.asyncio
    async def test_azure_client_passes_managed_identity_client_id_when_configured(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
            client_id="managed-identity-client-id",
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(azure_config=azure_config),
            logger=MagicMock(),
        )

        fake_ctor = MagicMock(return_value=MagicMock())

        with patch(
            "orb.providers.azure.infrastructure.azure_client.create_default_azure_credential_async",
            fake_ctor,
        ):
            await client.get_async_credential()

        fake_ctor.assert_called_once_with(
            client_id="managed-identity-client-id",
            logger=client._logger,
        )

    @pytest.mark.asyncio
    async def test_azure_client_omits_managed_identity_client_id_when_unset(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(azure_config=azure_config),
            logger=MagicMock(),
        )

        fake_ctor = MagicMock(return_value=MagicMock())

        with patch(
            "orb.providers.azure.infrastructure.azure_client.create_default_azure_credential_async",
            fake_ctor,
        ):
            await client.get_async_credential()

        fake_ctor.assert_called_once_with(
            client_id=None,
            logger=client._logger,
        )

    @pytest.mark.asyncio
    async def test_get_async_credential_preserves_nested_import_error_details(self):
        client = self._build_client()

        with patch(
            "orb.providers.azure.infrastructure.azure_client.create_default_azure_credential_async",
            side_effect=ImportError("aiohttp package is not installed"),
        ):
            with pytest.raises(
                AuthenticationError,
                match="azure-identity dependency error: aiohttp package is not installed",
            ):
                await client.get_async_credential()

    @pytest.mark.asyncio
    async def test_azure_client_passes_retry_and_timeout_kwargs_to_compute_client(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
            max_retries=7,
            connect_timeout=11,
            read_timeout=22,
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(azure_config=azure_config),
            logger=MagicMock(),
        )
        fake_credential = MagicMock()
        client.get_async_credential = AsyncMock(return_value=fake_credential)

        fake_compute_module = types.ModuleType("azure.mgmt.compute.aio")
        fake_ctor = MagicMock(return_value=MagicMock())
        fake_compute_module.ComputeManagementClient = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.mgmt": types.ModuleType("azure.mgmt"),
                "azure.mgmt.compute.aio": fake_compute_module,
            },
        ):
            await client.get_async_compute_client()

        fake_ctor.assert_called_once_with(
            credential=fake_credential,
            subscription_id="12345678-1234-1234-1234-123456789012",
            retry_total=7,
            connection_timeout=11,
            read_timeout=22,
        )

    @pytest.mark.asyncio
    async def test_azure_client_passes_retry_and_timeout_kwargs_to_subscription_client(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
            max_retries=4,
            connect_timeout=9,
            read_timeout=19,
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(azure_config=azure_config),
            logger=MagicMock(),
        )
        fake_credential = MagicMock()
        client.get_async_credential = AsyncMock(return_value=fake_credential)

        fake_subscription_module = types.ModuleType("azure.mgmt.resource.subscriptions.aio")
        fake_ctor = MagicMock(return_value=MagicMock())
        fake_subscription_module.SubscriptionClient = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.mgmt": types.ModuleType("azure.mgmt"),
                "azure.mgmt.resource": types.ModuleType("azure.mgmt.resource"),
                "azure.mgmt.resource.subscriptions.aio": fake_subscription_module,
            },
        ):
            await client.get_async_subscription_client()

        fake_ctor.assert_called_once_with(
            credential=fake_credential,
            retry_total=4,
            connection_timeout=9,
            read_timeout=19,
        )

    def test_collect_error_types_deduplicates_base_and_imported_types(self):
        error_types = AzureClient._collect_error_types(
            AuthenticationError,
            AuthenticationError,
            optional_error_loaders=(),
        )

        assert error_types == (AuthenticationError,)

    def test_azure_client_rejects_non_integer_timeout_values(self):
        with pytest.raises(
            AzureConfigurationError,
            match=r"connect_timeout.*must be an integer",
        ):
            AzureClient(
                runtime_config=AzureClientRuntimeConfig(
                    azure_config=types.SimpleNamespace(
                        subscription_id="12345678-1234-1234-1234-123456789012",
                        resource_group="rg-explicit",
                        region="westeurope",
                        max_retries=3,
                        connect_timeout="fast",
                        read_timeout=22,
                    ),
                    performance_config=PerformanceConfig(),
                ),
                logger=MagicMock(),
            )

    def test_azure_client_rejects_timeout_values_below_minimum(self):
        with pytest.raises(
            AzureConfigurationError,
            match=r"connect_timeout.*must be >= 1",
        ):
            AzureClient(
                runtime_config=AzureClientRuntimeConfig(
                    azure_config=types.SimpleNamespace(
                        subscription_id="12345678-1234-1234-1234-123456789012",
                        resource_group="rg-explicit",
                        region="westeurope",
                        max_retries=3,
                        connect_timeout=0,
                        read_timeout=22,
                    ),
                    performance_config=PerformanceConfig(),
                ),
                logger=MagicMock(),
            )

    def test_azure_client_maps_typed_performance_config(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        perf_config = PerformanceConfig(
            enable_batching=False,
            enable_parallel=False,
            max_workers=3,
            caching={"request_status": {"enabled": False, "ttl_seconds": 42}},
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(
                azure_config=azure_config,
                perf_config=perf_config,
            ),
            logger=MagicMock(),
        )

        assert client.perf_config["enable_batching"] is False
        assert client.perf_config["enable_parallel"] is False
        assert client.perf_config["max_workers"] == 3
        assert client.perf_config["enable_caching"] is False
        assert client.perf_config["cache_ttl"] == 42
        assert client.perf_config["batch_sizes"] == {
            "deallocate_vms": 25,
            "create_tags": 20,
            "describe_vms": 25,
        }

    def test_azure_client_uses_default_performance_config_when_not_overridden(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(
                azure_config=azure_config,
                perf_config=PerformanceConfig(),
            ),
            logger=MagicMock(),
        )

        assert client.perf_config["enable_batching"] is True
        assert client.perf_config["enable_parallel"] is True
        assert client.perf_config["max_workers"] == 10
        assert client.perf_config["enable_caching"] is True
        assert client.perf_config["cache_ttl"] == 300

    @pytest.mark.asyncio
    async def test_validate_credentials_async_returns_true_and_sets_validation_flag(self):
        client = self._build_partial_client()
        async_credential = MagicMock()
        async_credential.get_token = AsyncMock(return_value=MagicMock())
        client.get_async_credential = AsyncMock(return_value=async_credential)

        assert await AzureClient.validate_credentials_async(client) is True
        async_credential.get_token.assert_awaited_once_with(
            "https://management.azure.com/.default"
        )
        assert client._credentials_validated is True

    @pytest.mark.asyncio
    async def test_validate_subscription_async_returns_false_for_known_azure_errors(self):
        from azure.core.exceptions import ResourceNotFoundError

        client = self._build_partial_client()
        client.subscription_id = "12345678-1234-1234-1234-123456789012"
        async_subscription_client = MagicMock()
        async_subscription_client.subscriptions.get = AsyncMock(
            side_effect=ResourceNotFoundError("missing")
        )
        client.get_async_subscription_client = AsyncMock(return_value=async_subscription_client)

        assert await AzureClient.validate_subscription_async(client) is False

    @pytest.mark.asyncio
    async def test_validate_credentials_async_returns_false_for_authentication_error(self):
        client = self._build_partial_client()
        async_credential = MagicMock()
        async_credential.get_token = AsyncMock(
            side_effect=AuthenticationError("bad credential")
        )
        client.get_async_credential = AsyncMock(return_value=async_credential)

        assert await AzureClient.validate_credentials_async(client) is False

    @pytest.mark.asyncio
    async def test_validate_credentials_async_reraises_unexpected_errors(self):
        client = self._build_partial_client()
        async_credential = MagicMock()
        async_credential.get_token = AsyncMock(side_effect=RuntimeError("boom"))
        client.get_async_credential = AsyncMock(return_value=async_credential)

        with pytest.raises(RuntimeError, match="boom"):
            await AzureClient.validate_credentials_async(client)

    @pytest.mark.asyncio
    async def test_validate_subscription_async_reraises_unexpected_errors(self):
        client = self._build_partial_client()
        client.subscription_id = "12345678-1234-1234-1234-123456789012"
        async_subscription_client = MagicMock()
        async_subscription_client.subscriptions.get = AsyncMock(side_effect=RuntimeError("boom"))
        client.get_async_subscription_client = AsyncMock(return_value=async_subscription_client)

        with pytest.raises(RuntimeError, match="boom"):
            await AzureClient.validate_subscription_async(client)

    def test_close_marks_client_closed_and_prevents_reuse(self):
        logger = MagicMock()
        client = self._build_client(logger=logger)
        client._credentials_validated = True
        client._closed = False

        AzureClient.close(client)

        assert client._credentials_validated is False
        assert client._closed is True

        with pytest.raises(RuntimeError, match="AzureClient has been closed"):
            client._ensure_open()

    def test_close_is_idempotent(self):
        client = self._build_client()
        client._credentials_validated = False
        client._closed = False

        AzureClient.close(client)
        AzureClient.close(client)

    @pytest.mark.asyncio
    async def test_close_schedules_async_cleanup_when_event_loop_is_running(self):
        logger = MagicMock()
        client = self._build_client(logger=logger)
        async_compute_client = MagicMock()
        async_compute_client.close = AsyncMock()
        client._async_compute_client = async_compute_client
        client._closed = False

        AzureClient.close(client)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        async_compute_client.close.assert_awaited_once()
        assert client._pending_async_close_task is None

    @pytest.mark.asyncio
    async def test_aclose_raises_first_error_after_attempting_all_async_resources(self):
        client = self._build_partial_client()
        async_compute_client = MagicMock()
        async_network_client = MagicMock()
        async_compute_client.close = AsyncMock(side_effect=RuntimeError("compute close failed"))
        async_network_client.close = AsyncMock(side_effect=RuntimeError("network close failed"))
        client._async_compute_client = async_compute_client
        client._async_network_client = async_network_client

        with pytest.raises(RuntimeError, match="network close failed"):
            await AzureClient.aclose(client)

        async_compute_client.close.assert_awaited_once()
        async_network_client.close.assert_awaited_once()
        assert client._async_compute_client is None
        assert client._async_network_client is None

    @pytest.mark.asyncio
    async def test_get_async_compute_client_returns_live_client_when_other_task_wins_race(self):
        client = self._build_partial_client()
        existing_client = MagicMock()
        created_client = MagicMock()
        created_client.close = AsyncMock()
        client._async_compute_client = None

        async def build_management_client_async(**kwargs):
            _ = kwargs
            client._async_compute_client = existing_client
            return created_client

        client._build_management_client_async = AsyncMock(side_effect=build_management_client_async)
        client._close_async_resource = AsyncMock()

        resolved = await client.get_async_compute_client()

        assert resolved is existing_client
        client._close_async_resource.assert_awaited_once_with(
            "async_compute_client",
            created_client,
        )

    def test_context_manager_closes_on_exit(self):
        client = self._build_client()
        client._credentials_validated = False
        client._closed = False

        with client as scoped_client:
            assert scoped_client is client

        assert client._closed is True


class TestAzureClientNetworkResolution:
    @staticmethod
    def _build_partial_client() -> AzureClient:
        client = object.__new__(AzureClient)
        client._logger = MagicMock()
        client._lazy_init_lock = threading.RLock()
        client._closed = False
        client._credentials_validated = False
        client._async_credential = None
        client._async_compute_client = None
        client._async_network_client = None
        client._async_resource_client = None
        client._async_subscription_client = None
        client._pending_async_close_task = None
        client._arm_resource_id_parser = ArmResourceIdParser()
        client._network_identity_resolver = AzureNetworkIdentityResolver(
            async_network_client_getter=client.get_async_network_client,
            logger=client._logger,
            arm_resource_id_parser=client._arm_resource_id_parser,
            network_lookup_error_types=client._network_lookup_error_types,
        )
        return client

    @pytest.mark.asyncio
    async def test_resolve_network_identity_from_vm_async_populates_ips_and_subnet(self):
        azure_client = self._build_partial_client()
        async_network_client = MagicMock()
        async_network_client.network_interfaces.get = AsyncMock()
        async_network_client.public_ip_addresses.get = AsyncMock()
        azure_client.get_async_network_client = AsyncMock(return_value=async_network_client)
        azure_client._network_identity_resolver = AzureNetworkIdentityResolver(
            async_network_client_getter=azure_client.get_async_network_client,
            logger=azure_client._logger,
            arm_resource_id_parser=azure_client._arm_resource_id_parser,
            network_lookup_error_types=azure_client._network_lookup_error_types,
        )

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties.primary = True

        vm = MagicMock()
        vm.network_profile.network_interfaces = [nic_ref]

        subnet = MagicMock()
        subnet.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
        )
        public_ip_ref = MagicMock()
        public_ip_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/publicIPAddresses/pip-vm-1"
        )
        ip_cfg = MagicMock()
        ip_cfg.private_ip_address = "10.0.0.4"
        ip_cfg.subnet = subnet
        ip_cfg.public_ip_address = public_ip_ref

        nic = MagicMock()
        nic.ip_configurations = [ip_cfg]
        async_network_client.network_interfaces.get.return_value = nic

        pip = MagicMock()
        pip.ip_address = "52.1.2.3"
        async_network_client.public_ip_addresses.get.return_value = pip

        result = await AzureClient.resolve_network_identity_from_vm_async(azure_client, vm)

        assert result["private_ip"] == "10.0.0.4"
        assert result["public_ip"] == "52.1.2.3"
        assert result["subnet_id"].endswith("/subnets/default")
        assert result["vnet_id"].endswith("/virtualNetworks/test-vnet")
        assert result["nic_name"] == "nic-vm-1"
