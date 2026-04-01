"""Focused tests for Azure client and auth behavior."""

import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

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
        client._credential = None
        client._compute_client = None
        client._network_client = None
        client._resource_client = None
        client._msi_client = None
        client._authorization_client = None
        client._monitor_client = None
        client._subscription_client = None
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

    def test_azure_client_passes_managed_identity_client_id_when_configured(self):
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

        fake_identity = types.ModuleType("azure.identity")
        fake_ctor = MagicMock(return_value=MagicMock())
        fake_identity.DefaultAzureCredential = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.identity": fake_identity,
            },
        ):
            _ = client.credential

        fake_ctor.assert_called_once_with(
            managed_identity_client_id="managed-identity-client-id"
        )

    def test_azure_client_omits_managed_identity_client_id_when_unset(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        client = AzureClient(
            runtime_config=self._build_runtime_config(azure_config=azure_config),
            logger=MagicMock(),
        )

        fake_identity = types.ModuleType("azure.identity")
        fake_ctor = MagicMock(return_value=MagicMock())
        fake_identity.DefaultAzureCredential = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.identity": fake_identity,
            },
        ):
            _ = client.credential

        fake_ctor.assert_called_once_with()

    def test_azure_client_passes_retry_and_timeout_kwargs_to_compute_client(self):
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
        client._credential = fake_credential

        fake_compute_module = types.ModuleType("azure.mgmt.compute")
        fake_ctor = MagicMock(return_value=MagicMock())
        fake_compute_module.ComputeManagementClient = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.mgmt": types.ModuleType("azure.mgmt"),
                "azure.mgmt.compute": fake_compute_module,
            },
        ):
            _ = client.compute_client

        fake_ctor.assert_called_once_with(
            credential=fake_credential,
            subscription_id="12345678-1234-1234-1234-123456789012",
            retry_total=7,
            connection_timeout=11,
            read_timeout=22,
        )

    def test_azure_client_passes_retry_and_timeout_kwargs_to_subscription_client(self):
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
        client._credential = fake_credential

        fake_subscription_module = types.ModuleType("azure.mgmt.resource.subscriptions")
        fake_ctor = MagicMock(return_value=MagicMock())
        fake_subscription_module.SubscriptionClient = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.mgmt": types.ModuleType("azure.mgmt"),
                "azure.mgmt.resource": types.ModuleType("azure.mgmt.resource"),
                "azure.mgmt.resource.subscriptions": fake_subscription_module,
            },
        ):
            _ = client.subscription_client

        fake_ctor.assert_called_once_with(
            credential=fake_credential,
            retry_total=4,
            connection_timeout=9,
            read_timeout=19,
        )

    def test_build_management_client_maps_missing_package_to_configuration_error(self):
        client = self._build_client()

        with patch(
            "orb.providers.azure.infrastructure.azure_client.import_module",
            side_effect=ImportError("missing azure package"),
        ):
            with pytest.raises(
                AzureConfigurationError,
                match="azure-mgmt-network package is not installed",
            ):
                client._build_management_client(
                    module_path="azure.mgmt.network",
                    class_name="NetworkManagementClient",
                    missing_package_message="azure-mgmt-network package is not installed",
                    requires_subscription_id=True,
                )

    def test_collect_error_types_deduplicates_base_and_imported_types(self):
        error_types = AzureClient._collect_error_types(
            AuthenticationError,
            AuthenticationError,
            optional_error_imports=(),
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

    def test_validate_credentials_returns_false_for_authentication_error(self):
        client = self._build_partial_client()
        type(client).credential = property(
            lambda _self: MagicMock(
                get_token=MagicMock(side_effect=AuthenticationError("bad credential"))
            )
        )

        try:
            assert AzureClient.validate_credentials(client) is False
        finally:
            del type(client).credential

    def test_validate_credentials_reraises_unexpected_errors(self):
        client = self._build_partial_client()
        type(client).credential = property(
            lambda _self: MagicMock(get_token=MagicMock(side_effect=RuntimeError("boom")))
        )

        try:
            with pytest.raises(RuntimeError, match="boom"):
                AzureClient.validate_credentials(client)
        finally:
            del type(client).credential

    def test_validate_subscription_returns_false_for_known_azure_errors(self):
        from azure.core.exceptions import ResourceNotFoundError

        client = self._build_partial_client()
        client.subscription_id = "12345678-1234-1234-1234-123456789012"
        client._subscription_client = MagicMock()
        client._subscription_client.subscriptions.get.side_effect = ResourceNotFoundError("missing")

        assert AzureClient.validate_subscription(client) is False

    def test_validate_subscription_reraises_unexpected_errors(self):
        client = self._build_partial_client()
        client.subscription_id = "12345678-1234-1234-1234-123456789012"
        client._subscription_client = MagicMock()
        client._subscription_client.subscriptions.get.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            AzureClient.validate_subscription(client)

    def test_close_releases_owned_azure_resources_and_prevents_reuse(self):
        logger = MagicMock()
        client = self._build_client(logger=logger)
        subscription_client = MagicMock()
        monitor_client = MagicMock()
        authorization_client = MagicMock()
        msi_client = MagicMock()
        resource_client = MagicMock()
        network_client = MagicMock()
        compute_client = MagicMock()
        credential = MagicMock()
        client._subscription_client = subscription_client
        client._monitor_client = monitor_client
        client._authorization_client = authorization_client
        client._msi_client = msi_client
        client._resource_client = resource_client
        client._network_client = network_client
        client._compute_client = compute_client
        client._credential = credential
        client._credentials_validated = True
        client._closed = False

        AzureClient.close(client)

        subscription_client.close.assert_called_once_with()
        monitor_client.close.assert_called_once_with()
        authorization_client.close.assert_called_once_with()
        msi_client.close.assert_called_once_with()
        resource_client.close.assert_called_once_with()
        network_client.close.assert_called_once_with()
        compute_client.close.assert_called_once_with()
        credential.close.assert_called_once_with()
        assert client._credentials_validated is False
        assert client._closed is True

        with pytest.raises(RuntimeError, match="AzureClient has been closed"):
            client._ensure_open()

    def test_close_is_idempotent(self):
        client = self._build_client()
        subscription_client = MagicMock()
        client._subscription_client = subscription_client
        client._monitor_client = None
        client._authorization_client = None
        client._msi_client = None
        client._resource_client = None
        client._network_client = None
        client._compute_client = None
        client._credential = None
        client._credentials_validated = False
        client._closed = False

        AzureClient.close(client)
        AzureClient.close(client)

        subscription_client.close.assert_called_once_with()

    def test_close_continues_after_subclient_failure_and_marks_client_closed(self):
        client = self._build_client()
        failing_subscription_client = MagicMock()
        failing_subscription_client.close.side_effect = RuntimeError("subscription close failed")
        compute_client = MagicMock()
        credential = MagicMock()
        client._subscription_client = failing_subscription_client
        client._monitor_client = None
        client._authorization_client = None
        client._msi_client = None
        client._resource_client = None
        client._network_client = None
        client._compute_client = compute_client
        client._credential = credential
        client._credentials_validated = True
        client._closed = False

        with pytest.raises(RuntimeError, match="subscription close failed"):
            AzureClient.close(client)

        compute_client.close.assert_called_once_with()
        credential.close.assert_called_once_with()
        assert client._compute_client is None
        assert client._credential is None
        assert client._credentials_validated is False
        assert client._closed is True

    def test_compute_client_lazy_initialization_is_thread_safe(self):
        client = self._build_client()
        created_client = MagicMock()
        build_calls = 0
        start_barrier = threading.Barrier(5)

        def build_compute_client():
            nonlocal build_calls
            build_calls += 1
            time.sleep(0.02)
            return created_client

        client._build_compute_client = build_compute_client
        results: list[object] = []
        errors: list[Exception] = []

        def access_compute_client():
            try:
                start_barrier.wait()
                results.append(client.compute_client)
            except Exception as exc:  # pragma: no cover - failure capture for thread assertion
                errors.append(exc)

        threads = [threading.Thread(target=access_compute_client) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert build_calls == 1
        assert results == [created_client] * 5

    def test_context_manager_closes_owned_resources_on_exit(self):
        client = self._build_client()
        client._subscription_client = None
        client._monitor_client = None
        client._authorization_client = None
        client._msi_client = None
        resource_client = MagicMock()
        credential = MagicMock()
        client._resource_client = resource_client
        client._network_client = None
        client._compute_client = None
        client._credential = credential
        client._credentials_validated = False
        client._closed = False

        with client as scoped_client:
            assert scoped_client is client

        resource_client.close.assert_called_once_with()
        credential.close.assert_called_once_with()
        assert client._closed is True


class TestAzureClientNetworkResolution:
    @staticmethod
    def _build_partial_client() -> AzureClient:
        client = object.__new__(AzureClient)
        client._logger = MagicMock()
        client._lazy_init_lock = threading.RLock()
        client._closed = False
        client._credentials_validated = False
        client._credential = None
        client._compute_client = None
        client._network_client = None
        client._resource_client = None
        client._msi_client = None
        client._authorization_client = None
        client._monitor_client = None
        client._subscription_client = None
        return client

    def test_extract_resource_group_and_name_from_arm_id_rejects_incomplete_ids(self):
        assert (
            AzureClient.extract_resource_group_and_name_from_arm_id(
                "/subscriptions/sub/resourceGroups"
            )
            is None
        )

    def test_extract_resource_group_and_name_from_arm_id_rejects_malformed_ids(self):
        assert (
            AzureClient.extract_resource_group_and_name_from_arm_id(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/networkInterfaces//nic-vm-1"
            )
            is None
        )
        assert (
            AzureClient.extract_resource_group_and_name_from_arm_id(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/networkInterfaces"
            )
            is None
        )

    def test_extract_resource_group_and_name_from_arm_id_accepts_child_resources(self):
        assert AzureClient.extract_resource_group_and_name_from_arm_id(
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
        ) == ("test-rg", "default")

    def test_subnet_id_to_vnet_id_rejects_malformed_or_non_subnet_ids(self):
        assert (
            AzureClient.subnet_id_to_vnet_id(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/virtualNetworks/test-vnet/subnets"
            )
            is None
        )
        assert (
            AzureClient.subnet_id_to_vnet_id(
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/networkInterfaces/nic-vm-1"
            )
            is None
        )

    def test_resolve_network_identity_from_vm_populates_ips_and_subnet(self):
        azure_client = self._build_partial_client()
        azure_client._network_client = MagicMock()

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
        azure_client.network_client.network_interfaces.get.return_value = nic

        pip = MagicMock()
        pip.ip_address = "52.1.2.3"
        azure_client.network_client.public_ip_addresses.get.return_value = pip

        result = AzureClient.resolve_network_identity_from_vm(azure_client, vm)

        assert result["private_ip"] == "10.0.0.4"
        assert result["public_ip"] == "52.1.2.3"
        assert result["subnet_id"].endswith("/subnets/default")
        assert result["vnet_id"].endswith("/virtualNetworks/test-vnet")
        assert result["nic_name"] == "nic-vm-1"

    def test_resolve_network_identity_tolerates_missing_nested_property_bags(self):
        azure_client = self._build_partial_client()
        azure_client._network_client = MagicMock()

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties = None

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
        ip_cfg.properties = None

        nic = MagicMock()
        nic.ip_configurations = [ip_cfg]
        azure_client.network_client.network_interfaces.get.return_value = nic

        pip = MagicMock()
        pip.ip_address = "52.1.2.3"
        azure_client.network_client.public_ip_addresses.get.return_value = pip

        result = AzureClient.resolve_network_identity_from_vm(azure_client, vm)

        assert result["private_ip"] == "10.0.0.4"
        assert result["public_ip"] == "52.1.2.3"
        assert result["subnet_id"].endswith("/subnets/default")
        assert result["vnet_id"].endswith("/virtualNetworks/test-vnet")
        assert result["nic_name"] == "nic-vm-1"

    def test_resolve_network_identity_skips_known_nic_lookup_errors(self):
        from azure.core.exceptions import ResourceNotFoundError

        azure_client = self._build_partial_client()
        azure_client._network_client = MagicMock()

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties.primary = True

        azure_client.network_client.network_interfaces.get.side_effect = ResourceNotFoundError(
            "missing"
        )

        result = AzureClient.resolve_network_identity_from_nic_refs(azure_client, [nic_ref])

        assert result == {
            "private_ip": None,
            "public_ip": None,
            "subnet_id": None,
            "vnet_id": None,
            "nic_id": None,
            "nic_name": None,
        }

    def test_resolve_network_identity_reraises_unexpected_nic_lookup_errors(self):
        azure_client = self._build_partial_client()
        azure_client._network_client = MagicMock()

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties.primary = True

        azure_client.network_client.network_interfaces.get.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            AzureClient.resolve_network_identity_from_nic_refs(azure_client, [nic_ref])
