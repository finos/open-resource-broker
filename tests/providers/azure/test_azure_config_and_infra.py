"""Tests for the Azure configuration, exceptions, resilience, and adapters."""

import sys
import types

import pytest
from unittest.mock import MagicMock, Mock, patch

from domain.base.value_objects import InstanceId, InstanceType
from domain.machine.aggregate import Machine
from providers.azure.configuration.config import AzureProviderConfig
from providers.azure.configuration.validator import (
    validate_azure_config,
    validate_azure_template,
)
from providers.azure.configuration.template_extension import AzureTemplateExtensionConfig
from providers.azure.exceptions.azure_exceptions import (
    AzureError,
    ResourceCleanupError,
    VMNotFoundError,
)
from providers.azure.infrastructure.adapters.machine_adapter import AzureMachineAdapter
from providers.azure.infrastructure.azure_client import AzureClient
from providers.azure.registration import (
    create_azure_config,
    register_azure_provider,
)
from providers.azure.infrastructure.adapters.azure_validation_adapter import (
    AzureValidationAdapter,
)
from providers.azure.resilience.azure_retry_strategy import (
    AzureRetryStrategy,
    is_retryable_azure_error,
)
from infrastructure.registry.provider_registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestAzureProviderConfig:
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


# ---------------------------------------------------------------------------
# Template validation
# ---------------------------------------------------------------------------


class TestTemplateValidation:
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
        assert any("subscription_id" in e for e in result["errors"])

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

        with patch("infrastructure.di.container.get_container", return_value=container):
            client = AzureClient(config=config_port, logger=MagicMock())

        assert client.subscription_id == azure_config.subscription_id
        assert client.resource_group == "rg-explicit"
        assert client.region_name == "westeurope"

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

        with patch("infrastructure.di.container.get_container", return_value=container):
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


class TestAzureAuthStrategy:
    def test_auth_strategy_passes_managed_identity_client_id_when_configured(self):
        from providers.azure.auth.azure_auth_strategy import AzureAuthStrategy

        class ConcreteAzureAuthStrategy(AzureAuthStrategy):
            def is_enabled(self) -> bool:
                return self.enabled

        strategy = ConcreteAzureAuthStrategy(
            logger=MagicMock(),
            client_id="managed-identity-client-id",
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
            _ = strategy._get_credential()

        fake_ctor.assert_called_once_with(
            managed_identity_client_id="managed-identity-client-id"
        )

    def test_auth_strategy_omits_managed_identity_client_id_when_unset(self):
        from providers.azure.auth.azure_auth_strategy import AzureAuthStrategy

        class ConcreteAzureAuthStrategy(AzureAuthStrategy):
            def is_enabled(self) -> bool:
                return self.enabled

        strategy = ConcreteAzureAuthStrategy(logger=MagicMock())

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
            _ = strategy._get_credential()

        fake_ctor.assert_called_once_with()


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

        with patch("config.manager.get_config_manager", side_effect=Exception("no config")):
            assert adapter.validate_provider_api("VMSSUniform") is True

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
        azure_client.compute_client.virtual_machines.get.side_effect = Exception("NotFound")
        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())
        machine = Machine(
            instance_id=InstanceId(value="vm-1"),
            template_id="tpl-1",
            provider_type="azure",
            instance_type=InstanceType(value="Standard_D4s_v5"),
            image_id="img-1",
            provider_data={"resource_group": "rg"},
        )

        with pytest.raises(VMNotFoundError):
            adapter.perform_health_check(machine)

    def test_cleanup_machine_resources_deletes_known_dependencies(self):
        azure_client = MagicMock()
        azure_client.resource_group = "rg"
        nic_poller = MagicMock()
        disk_poller = MagicMock()
        azure_client.network_client.network_interfaces.begin_delete.return_value = nic_poller
        azure_client.compute_client.disks.begin_delete.return_value = disk_poller

        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())
        machine = Machine(
            instance_id=InstanceId(value="vm-1"),
            template_id="tpl-1",
            provider_type="azure",
            instance_type=InstanceType(value="Standard_D4s_v5"),
            image_id="img-1",
            provider_data={
                "resource_group": "rg",
                "nic_name": "nic-vm-1",
                "disk_names": ["osdisk-vm-1"],
            },
        )

        result = adapter.cleanup_machine_resources(machine)

        assert result["network_interfaces"]["success"] == ["nic-vm-1"]
        assert result["disks"]["success"] == ["osdisk-vm-1"]

    def test_cleanup_machine_resources_raises_on_partial_failure(self):
        azure_client = MagicMock()
        azure_client.resource_group = "rg"
        azure_client.network_client.network_interfaces.begin_delete.side_effect = Exception(
            "delete failed"
        )
        azure_client.compute_client.disks.begin_delete.return_value = MagicMock()

        adapter = AzureMachineAdapter(azure_client=azure_client, logger=MagicMock())
        machine = Machine(
            instance_id=InstanceId(value="vm-1"),
            template_id="tpl-1",
            provider_type="azure",
            instance_type=InstanceType(value="Standard_D4s_v5"),
            image_id="img-1",
            provider_data={
                "resource_group": "rg",
                "nic_name": "nic-vm-1",
            },
        )

        with pytest.raises(ResourceCleanupError):
            adapter.cleanup_machine_resources(machine)


class TestAzureClientNetworkResolution:
    def test_resolve_network_identity_from_vm_populates_ips_and_subnet(self):
        from providers.azure.infrastructure.azure_client import AzureClient

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
                "providers.azure.registration._register_azure_components_with_di",
                return_value=None,
            ),
            patch(
                "providers.azure.registration._create_azure_strategy_with_di",
                return_value=strategy,
            ) as mock_create_strategy,
        ):
            from domain.base.ports import LoggingPort
            from providers.azure.registration import register_azure_provider_with_di

            container.get.side_effect = lambda key: logger if key is LoggingPort else Mock()

            assert register_azure_provider_with_di(provider_instance, container) is True

            created = ProviderRegistry().create_strategy_from_instance(
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
