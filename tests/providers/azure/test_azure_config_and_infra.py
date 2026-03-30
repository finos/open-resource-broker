"""Tests for the Azure configuration, exceptions, resilience, and adapters."""

import sys
import types

import pytest
from unittest.mock import MagicMock, Mock, patch

from orb.bootstrap.infrastructure_services import register_infrastructure_services
from orb.config import PerformanceConfig
from orb.domain.base.exceptions import ConfigurationError
from orb.domain.base.value_objects import InstanceId, InstanceType
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.di.container import DIContainer
from orb.domain.machine.aggregate import Machine
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.configuration.validator import (
    validate_azure_config,
    validate_azure_template,
)
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig
from orb.providers.azure.exceptions.azure_exceptions import (
    AuthenticationError,
    AzureError,
    AzureConfigurationError,
    VMNotFoundError,
)
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.adapters.machine_adapter import AzureMachineAdapter
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.azure_handler_factory import AzureHandlerFactory
from orb.providers.azure.managers.azure_resource_manager import AzureResourceManager
from orb.providers.azure.registration import (
    create_azure_config,
    create_azure_validator,
    register_azure_provider,
)
from orb.providers.azure.infrastructure.adapters.azure_validation_adapter import (
    AzureValidationAdapter,
)
from orb.providers.azure.resilience.azure_retry_strategy import (
    AzureRetryStrategy,
    is_retryable_azure_error,
)
from orb.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestAzureProviderConfig:
    def test_location_alias_populates_region(self):
        config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            location="westeurope",
        )

        assert config.region == "westeurope"
        assert config.location == "westeurope"

    def test_region_still_populates_location_accessor(self):
        config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            region="westeurope",
        )

        assert config.region == "westeurope"
        assert config.location == "westeurope"

    def test_conflicting_location_and_region_are_rejected(self):
        with pytest.raises(ValueError, match="conflicting 'location' and 'region'"):
            AzureProviderConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                location="westeurope",
                region="eastus2",
            )

    def test_invalid_subscription_id(self):
        with pytest.raises(ValueError, match="subscription_id"):
            AzureProviderConfig(subscription_id="not-a-uuid")

    def test_invalid_resource_group_chars(self):
        with pytest.raises(ValueError, match="resource_group"):
            AzureProviderConfig(resource_group="rg with spaces!")

    def test_create_azure_config_rejects_invalid_subscription(self):
        with pytest.raises(RuntimeError, match="subscription_id"):
            create_azure_config({
                "provider_type": "azure",
                "subscription_id": "not-a-uuid",
            })

    def test_unknown_top_level_provider_fields_are_rejected(self):
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            AzureProviderConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                unexpected_option="value",
            )

    def test_cyclecloud_config_rejects_inline_basic_auth(self):
        with pytest.raises(ValueError, match="credential_path"):
            AzureProviderConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                cyclecloud={
                    "url": "https://cc.example.com",
                    "username": "admin",
                    "password": "secret",
                },
            )

    def test_cyclecloud_config_accepts_credential_path_auth(self):
        config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            cyclecloud={
                "url": "https://cc.example.com",
                "credential_path": "config/cyclecloud-credentials.json",
            },
        )

        assert config.cyclecloud is not None
        assert config.cyclecloud.credential_path == "config/cyclecloud-credentials.json"

    def test_cyclecloud_config_accepts_typed_tls_and_auth_fields(self):
        config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            cyclecloud={
                "url": "https://cc.example.com",
                "credential_path": "config/cyclecloud-credentials.json",
                "verify_ssl": False,
                "auth_mode": "bearer",
                "aad_scope": "https://cc.example.com/.default",
            },
        )

        assert config.cyclecloud is not None
        assert config.cyclecloud.verify_ssl is False
        assert config.cyclecloud.auth_mode == "bearer"
        assert config.cyclecloud.aad_scope == "https://cc.example.com/.default"

    def test_cyclecloud_config_rejects_unknown_fields(self):
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            AzureProviderConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                cyclecloud={
                    "url": "https://cc.example.com",
                    "credential_path": "config/cyclecloud-credentials.json",
                    "extra_transport_option": "value",
                },
            )


# ---------------------------------------------------------------------------
# Template validation
# ---------------------------------------------------------------------------


class TestTemplateValidation:
    def test_region_alias_is_accepted_for_location(self):
        result = validate_azure_template({
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "region": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })
        assert result["valid"] is True

    def test_conflicting_location_and_region_is_invalid(self):
        result = validate_azure_template({
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "region": "westeurope",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })

        assert result["valid"] is False
        assert any("conflicting 'location' and 'region'" in e for e in result["errors"])

    def test_valid_template(self):
        result = validate_azure_template({
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })
        assert result["valid"] is True

    def test_missing_image_source_is_invalid(self):
        result = validate_azure_template({
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        })
        assert result["valid"] is False
        assert any("image source is required" in e for e in result["errors"])

    def test_missing_required_fields(self):
        result = validate_azure_template({})
        assert result["valid"] is False
        assert any("vm_size" in e for e in result["errors"])
        assert any("resource_group" in e for e in result["errors"])
        assert any("location" in e for e in result["errors"])

    def test_uncommon_vm_size_warning(self):
        result = validate_azure_template({
            "template_id": "t1",
            "vm_size": "custom_vm_size",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })
        assert result["valid"] is True
        assert any("Uncommon" in w for w in result["warnings"])

    def test_spot_regular_conflict(self):
        result = validate_azure_template({
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
            "priority": "Regular",
            "eviction_policy": "Delete",
        })
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_valid_config(self):
        c = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg",
        )
        result = validate_azure_config(c)
        assert result["valid"] is True

    def test_missing_subscription(self):
        c = AzureProviderConfig()
        result = validate_azure_config(c)
        assert result["valid"] is False


class TestAzureHandlerFactory:
    def test_create_handler_for_template_accepts_enum_provider_api(self):
        factory = AzureHandlerFactory(
            azure_client=MagicMock(),
            logger=MagicMock(),
            machine_adapter=MagicMock(),
        )
        template = MagicMock()
        template.provider_api = AzureProviderApi.VMSS

        handler = factory.create_handler_for_template(template)

        assert handler is factory._handlers[AzureProviderApi.VMSS.value]

    def test_azure_client_uses_typed_config_when_active_provider_is_not_azure(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        config_port = MagicMock()
        config_port.get_typed.return_value = azure_config
        config_port.get_provider_config.return_value = None

        selection_service = MagicMock()
        selection_service.select_active_provider.return_value = Mock(
            provider_type="aws",
            provider_instance="aws-default",
        )
        container = MagicMock()
        container.get.return_value = selection_service

        with patch("orb.infrastructure.di.container.get_container", return_value=container):
            client = AzureClient(config=config_port, logger=MagicMock())

        assert client.subscription_id == azure_config.subscription_id
        assert client.resource_group == "rg-explicit"
        assert client.region_name == "westeurope"

    def test_bootstrap_template_factory_preserves_azure_image_templates(self):
        container = DIContainer()
        container.register_instance(LoggingPort, MagicMock())
        register_infrastructure_services(container)

        factory = container.get(TemplateFactory)
        template = factory.create_template({
            "template_id": "azure-cheapest-vmss",
            "provider_type": "azure",
            "provider_api": "VMSS",
            "vm_size": "Standard_B1s",
            "resource_group": "orb-test-rg",
            "location": "eastus2",
            "image": {
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts-gen2",
                "version": "latest",
            },
            "admin_username": "azureuser",
            "ssh_public_keys": [
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"
            ],
            "network_config": {
                "subnet_id": (
                    "/subscriptions/a6eb5b32-65bb-47c0-a2b8-34fa90400a4b/"
                    "resourceGroups/orb-test-rg/providers/Microsoft.Network/"
                    "virtualNetworks/orb-test-vnet/subnets/default"
                )
            },
        })

        assert factory.supports_provider("azure") is True
        assert template.provider_type == "azure"
        assert template.image is not None
        assert template.image.publisher == "Canonical"

    def test_registry_created_strategy_gets_azure_client_resolver(self):
        registry = ProviderRegistry()
        registry.clear_registrations()
        register_azure_provider(registry=registry)

        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        config_port = MagicMock()
        config_port.get_typed.return_value = azure_config
        config_port.get_provider_config.return_value = None
        azure_client = AzureClient(config=config_port, logger=MagicMock())
        container = MagicMock()
        container.get.return_value = azure_client

        with patch("orb.infrastructure.di.container.get_container", return_value=container):
            strategy = registry.create_strategy(
                "azure",
                {
                    "provider_type": "azure",
                    "subscription_id": "12345678-1234-1234-1234-123456789012",
                    "resource_group": "rg-explicit",
                    "region": "westeurope",
                },
            )

            assert isinstance(strategy.azure_client, AzureClient)
            assert strategy.azure_client.subscription_id == "12345678-1234-1234-1234-123456789012"
            assert strategy.azure_client.resource_group == "rg-explicit"
            assert "VMSS" in strategy.handlers

    def test_azure_client_passes_managed_identity_client_id_when_configured(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
            client_id="managed-identity-client-id",
        )
        config_port = MagicMock()
        config_port.get_typed.return_value = azure_config
        config_port.get_provider_config.return_value = None

        client = AzureClient(config=config_port, logger=MagicMock())

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
        config_port = MagicMock()
        config_port.get_typed.return_value = azure_config
        config_port.get_provider_config.return_value = None

        client = AzureClient(config=config_port, logger=MagicMock())

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
        config_port = MagicMock()
        config_port.get_typed.return_value = azure_config
        config_port.get_provider_config.return_value = None

        client = AzureClient(config=config_port, logger=MagicMock())
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
        config_port = MagicMock()
        config_port.get_typed.return_value = azure_config
        config_port.get_provider_config.return_value = None

        client = AzureClient(config=config_port, logger=MagicMock())
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

    def test_azure_client_rejects_non_integer_timeout_values(self):
        config_port = MagicMock()
        config_port.get_typed.return_value = types.SimpleNamespace(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
            max_retries=3,
            connect_timeout="fast",
            read_timeout=22,
        )
        config_port.get_provider_config.return_value = None

        with pytest.raises(
            AzureConfigurationError,
            match=r"connect_timeout.*must be an integer",
        ):
            AzureClient(config=config_port, logger=MagicMock())

    def test_azure_client_rejects_timeout_values_below_minimum(self):
        config_port = MagicMock()
        config_port.get_typed.return_value = types.SimpleNamespace(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
            max_retries=3,
            connect_timeout=0,
            read_timeout=22,
        )
        config_port.get_provider_config.return_value = None

        with pytest.raises(
            AzureConfigurationError,
            match=r"connect_timeout.*must be >= 1",
        ):
            AzureClient(config=config_port, logger=MagicMock())


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

    def test_default_azure_access_token_provider_passes_managed_identity_client_id_when_configured(self):
        from orb.providers.azure.infrastructure.credential_factory import (
            DefaultAzureAccessTokenProvider,
        )

        fake_identity = types.ModuleType("azure.identity")
        fake_credential = MagicMock()
        fake_credential.get_token.return_value = types.SimpleNamespace(token="access-token")
        fake_ctor = MagicMock(return_value=fake_credential)
        fake_identity.DefaultAzureCredential = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.identity": fake_identity,
            },
        ):
            provider = DefaultAzureAccessTokenProvider(
                logger=MagicMock(),
                client_id="managed-identity-client-id",
            )
            token = provider.get_access_token("https://management.azure.com/.default")

        assert token == "access-token"

        fake_ctor.assert_called_once_with(
            managed_identity_client_id="managed-identity-client-id"
        )
        fake_credential.close.assert_called_once_with()

    def test_default_azure_access_token_provider_omits_managed_identity_client_id_when_unset(self):
        from orb.providers.azure.infrastructure.credential_factory import (
            DefaultAzureAccessTokenProvider,
        )

        fake_identity = types.ModuleType("azure.identity")
        fake_credential = MagicMock()
        fake_credential.get_token.return_value = types.SimpleNamespace(token="access-token")
        fake_ctor = MagicMock(return_value=fake_credential)
        fake_identity.DefaultAzureCredential = fake_ctor

        with patch.dict(
            sys.modules,
            {
                "azure": types.ModuleType("azure"),
                "azure.identity": fake_identity,
            },
        ):
            provider = DefaultAzureAccessTokenProvider(
                logger=MagicMock(),
                client_id=None,
            )
            token = provider.get_access_token("https://management.azure.com/.default")

        assert token == "access-token"

        fake_ctor.assert_called_once_with()
        fake_credential.close.assert_called_once_with()

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
    def _build_client(logger: MagicMock | None = None) -> AzureClient:
        config_port = MagicMock()
        config_port.get_typed.side_effect = lambda config_type: (
            AzureProviderConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                resource_group="rg-explicit",
                region="westeurope",
            )
            if config_type is AzureProviderConfig
            else PerformanceConfig()
        )
        return AzureClient(config=config_port, logger=logger or MagicMock())

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
        config_port = MagicMock()
        config_port.get_typed.side_effect = lambda config_type: (
            azure_config if config_type is AzureProviderConfig else perf_config
        )

        client = AzureClient(config=config_port, logger=MagicMock())

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

    def test_azure_client_falls_back_to_default_performance_config_on_config_error(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg-explicit",
            region="westeurope",
        )
        config_port = MagicMock()

        def get_typed(config_type):
            if config_type is AzureProviderConfig:
                return azure_config
            if config_type is PerformanceConfig:
                raise ConfigurationError("perf config missing")
            raise AssertionError(f"unexpected config type {config_type}")

        config_port.get_typed.side_effect = get_typed

        client = AzureClient(config=config_port, logger=MagicMock())

        assert client.perf_config["enable_batching"] is True
        assert client.perf_config["enable_parallel"] is True
        assert client.perf_config["max_workers"] == 10
        assert client.perf_config["enable_caching"] is True
        assert client.perf_config["cache_ttl"] == 300

    def test_validate_credentials_returns_false_for_authentication_error(self):
        client = object.__new__(AzureClient)
        client._logger = MagicMock()
        client._credentials_validated = False
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
        client = object.__new__(AzureClient)
        client._logger = MagicMock()
        client._credentials_validated = False
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

        client = object.__new__(AzureClient)
        client._logger = MagicMock()
        client.subscription_id = "12345678-1234-1234-1234-123456789012"
        client._subscription_client = MagicMock()
        client._subscription_client.subscriptions.get.side_effect = ResourceNotFoundError("missing")

        assert AzureClient.validate_subscription(client) is False

    def test_validate_subscription_reraises_unexpected_errors(self):
        client = object.__new__(AzureClient)
        client._logger = MagicMock()
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
        client._resource_cache = {"vm": "cached"}
        client._batch_history = {"describe_vms": [25]}
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
        assert client._resource_cache == {}
        assert client._batch_history == {}
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
        client._resource_cache = {}
        client._batch_history = {}
        client._closed = False

        AzureClient.close(client)
        AzureClient.close(client)

        subscription_client.close.assert_called_once_with()

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
        client._resource_cache = {}
        client._batch_history = {}
        client._closed = False

        with client as scoped_client:
            assert scoped_client is client

        resource_client.close.assert_called_once_with()
        credential.close.assert_called_once_with()
        assert client._closed is True


class TestAzureValidationAdapter:
    def test_validate_template_configuration_uses_azure_rules(self):
        adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

        result = adapter.validate_template_configuration({
            "provider_api": "VMSS",
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })

        assert result["valid"] is True
        assert "provider_api" in result["validated_fields"]

    def test_validate_template_configuration_rejects_unsupported_provider_api(self):
        adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

        result = adapter.validate_template_configuration({
            "provider_api": "BogusApi",
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })

        assert result["valid"] is False
        assert any("Unsupported provider API" in error for error in result["errors"])

    def test_validate_provider_api_fallback_accepts_vmss_uniform(self):
        adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

        assert adapter.validate_provider_api("VMSSUniform") is True

    def test_validate_provider_api_does_not_accept_dead_config_only_api_names(self):
        adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

        assert adapter.validate_provider_api("AzureFleet") is False
        assert "AzureFleet" not in adapter.get_supported_provider_apis()

    def test_get_api_capabilities_reports_spot_support_for_vmss(self):
        adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

        capabilities = adapter.get_api_capabilities("VMSS")

        assert capabilities["supports_spot"] is True
        assert capabilities["supports_on_demand"] is True
        assert capabilities["max_instances"] == 1000

    def test_validate_template_configuration_rejects_spot_percentage_for_uniform(self):
        adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

        result = adapter.validate_template_configuration({
            "provider_api": "VMSS",
            "template_id": "t1",
            "orchestration_mode": "Uniform",
            "spot_percentage": 70,
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })

        assert result["valid"] is False
        assert any("spot_percentage requires Flexible orchestration mode" in error for error in result["errors"])

    def test_validate_template_configuration_accepts_spot_percentage_without_spot_priority(self):
        adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

        result = adapter.validate_template_configuration({
            "provider_api": "VMSS",
            "template_id": "t1",
            "spot_percentage": 70,
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        })

        assert result["valid"] is True
        assert result["errors"] == []

    def test_create_azure_validator_returns_adapter(self):
        validator = create_azure_validator(
            {
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "resource_group": "rg",
                "region": "eastus2",
            }
        )

        assert isinstance(validator, AzureValidationAdapter)


class TestAzureMachineAdapter:
    def test_create_machine_from_normalized_instance_adds_metadata(self):
        azure_client = MagicMock()
        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())

        result = adapter.create_machine_from_azure_instance(
            {
                "instance_id": "vm-1",
                "status": "running",
                "private_ip": "10.0.0.4",
                "instance_type": "Standard_D4s_v5",
                "provider_data": {"resource_group": "rg"},
            },
            request_id="req-1",
            provider_api="SingleVM",
            resource_id="vm-1",
        )

        assert result["instance_id"] == "vm-1"
        assert result["request_id"] == "req-1"
        assert result["provider_api"] == "SingleVM"
        assert result["resource_id"] == "vm-1"
        assert result["status"] == "running"
        assert result["name"] == "10.0.0.4"

    def test_convert_normalized_dict_to_machine(self):
        azure_client = MagicMock()
        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())

        result = adapter.convert_azure_instance_to_machine({
            "instance_id": "vm-guid",
            "name": "vm-name",
            "status": "running",
            "instance_type": "Standard_D4s_v5",
            "availability_zone": "1",
            "provider_data": {"location": "eastus2"},
        })

        assert result["instance_id"] == "vm-guid"
        assert result["name"] == "vm-name"
        assert result["status"] == "running"
        assert result["instance_type"] == "Standard_D4s_v5"
        assert result["availability_zone"] == "1"

    def test_convert_rejects_missing_identifier(self):
        azure_client = MagicMock()
        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())

        with pytest.raises(AzureError, match="Missing required Azure instance identifier"):
            adapter.convert_azure_instance_to_machine({"status": "running"})

    def test_convert_rejects_non_dict_input(self):
        azure_client = MagicMock()
        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())

        with pytest.raises(AzureError, match="expects normalized dict instance data"):
            adapter.convert_azure_instance_to_machine(MagicMock())

    def test_perform_health_check_maps_power_and_provisioning_status(self):
        azure_client = MagicMock()
        azure_client.resource_group = "rg"
        vm = MagicMock()
        power = MagicMock()
        power.code = "PowerState/running"
        provisioning = MagicMock()
        provisioning.code = "ProvisioningState/succeeded"
        vm.instance_view.statuses = [provisioning, power]
        azure_client.compute_client.virtual_machines.get.return_value = vm

        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())
        machine = Machine(
            instance_id=InstanceId(value="vm-1"),
            template_id="tpl-1",
            provider_type="azure",
            provider_name="azure-default",
            instance_type=InstanceType(value="Standard_D4s_v5"),
            image_id="img-1",
            provider_data={"resource_group": "rg", "vm_name": "vm-1"},
        )

        result = adapter.perform_health_check(machine)

        assert result["system"]["status"] is True
        assert result["instance"]["status"] is True

    def test_perform_health_check_raises_vm_not_found(self):
        azure_client = MagicMock()
        azure_client.resource_group = "rg"
        from azure.core.exceptions import ResourceNotFoundError
        azure_client.compute_client.virtual_machines.get.side_effect = ResourceNotFoundError("NotFound")
        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())
        machine = Machine(
            instance_id=InstanceId(value="vm-1"),
            template_id="tpl-1",
            provider_type="azure",
            provider_name="azure-default",
            instance_type=InstanceType(value="Standard_D4s_v5"),
            image_id="img-1",
            provider_data={"resource_group": "rg"},
        )

        with pytest.raises(VMNotFoundError):
            adapter.perform_health_check(machine)

class TestAzureClientNetworkResolution:
    def test_extract_resource_group_and_name_from_arm_id_rejects_incomplete_ids(self):
        from orb.providers.azure.infrastructure.azure_client import AzureClient

        assert (
            AzureClient.extract_resource_group_and_name_from_arm_id(
                "/subscriptions/sub/resourceGroups"
            )
            is None
        )

    def test_resolve_network_identity_from_vm_populates_ips_and_subnet(self):
        from orb.providers.azure.infrastructure.azure_client import AzureClient

        azure_client = object.__new__(AzureClient)
        azure_client._logger = MagicMock()
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
        from orb.providers.azure.infrastructure.azure_client import AzureClient

        azure_client = object.__new__(AzureClient)
        azure_client._logger = MagicMock()
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

        azure_client = object.__new__(AzureClient)
        azure_client._logger = MagicMock()
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
        azure_client = object.__new__(AzureClient)
        azure_client._logger = MagicMock()
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


class TestAzureResourceManager:
    def test_tag_resource_submits_requested_tags(self):
        azure_client = MagicMock()
        logger = MagicMock()
        config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg",
        )
        manager = AzureResourceManager(azure_client=azure_client, config=config, logger=logger)

        azure_client.resource_client.tags.begin_create_or_update_at_scope.return_value = MagicMock()

        manager.tag_resource(
            "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm-1",
            {"env": "test"},
        )

        azure_client.resource_client.tags.begin_create_or_update_at_scope.assert_called_once_with(
            scope="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm-1",
            parameters={"properties": {"tags": {"env": "test"}}},
        )


# ---------------------------------------------------------------------------
# Template extension
# ---------------------------------------------------------------------------


class TestTemplateExtension:
    def test_defaults_include_vm_size(self):
        ext = AzureTemplateExtensionConfig()
        defaults = ext.to_template_defaults()
        assert "vm_size" in defaults
        assert defaults["vm_size"] == "Standard_D4s_v5"


class TestAzureRegistration:
    def setup_method(self):
        ProviderRegistry().clear_registrations()

    def test_register_azure_provider_with_di_uses_registry_instance_factory_contract(self):
        provider_instance = Mock()
        provider_instance.name = "azure-test"
        provider_instance.config = {
            "subscription_id": "12345678-1234-1234-1234-123456789012",
            "resource_group": "rg",
        }

        logger = Mock()
        container = Mock()
        container.get.return_value = logger

        strategy = object()

        with (
            patch(
                "orb.providers.azure.registration._register_azure_components_with_di",
                return_value=None,
            ),
            patch(
                "orb.providers.azure.registration._create_azure_strategy_with_di",
                return_value=strategy,
            ) as mock_create_strategy,
        ):
            from orb.domain.base.ports import LoggingPort
            from orb.providers.azure.registration import register_azure_provider_with_di

            container.get.side_effect = lambda key: logger if key is LoggingPort else Mock()

            assert register_azure_provider_with_di(provider_instance, container) is True

            created = ProviderRegistry().create_strategy_by_instance(
                "azure-test",
                {"ignored": "config"},
            )

            assert created is strategy
            mock_create_strategy.assert_called_once()


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


class TestRetryStrategy:
    def test_retryable_status_code(self):
        exc = MagicMock()
        exc.status_code = 429
        assert is_retryable_azure_error(exc) is True

    def test_non_retryable_status_code(self):
        exc = MagicMock()
        exc.status_code = 404
        exc.error_code = None
        exc.error = None
        assert is_retryable_azure_error(exc) is False

    def test_retryable_error_code(self):
        exc = MagicMock()
        exc.status_code = None
        exc.error_code = "TooManyRequests"
        assert is_retryable_azure_error(exc) is True

    def test_retryable_string_detection(self):
        exc = Exception("Request was throttled")
        assert is_retryable_azure_error(exc) is True

    def test_should_retry_within_limit(self):
        logger = MagicMock()
        rs = AzureRetryStrategy(logger=logger, max_attempts=3)
        exc = MagicMock()
        exc.status_code = 503
        assert rs.should_retry(0, exc) is True
        assert rs.should_retry(2, exc) is True
        assert rs.should_retry(3, exc) is False  # at limit

    def test_delay_exponential_backoff(self):
        logger = MagicMock()
        rs = AzureRetryStrategy(logger=logger, base_delay=1.0, max_delay=60.0, jitter=False)
        assert rs.get_delay(0) == 1.0
        assert rs.get_delay(1) == 2.0
        assert rs.get_delay(2) == 4.0
        assert rs.get_delay(10) == 60.0  # capped

    def test_on_retry_logs_warning(self):
        logger = MagicMock()
        rs = AzureRetryStrategy(logger=logger)
        rs.on_retry(1, Exception("test"))
        logger.warning.assert_called_once()
