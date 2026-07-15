"""Tests for Azure configuration and registration behavior."""

from unittest.mock import MagicMock, Mock, patch

import pytest
from pydantic import ValidationError

from orb.application.dto.template import TemplateDTO
from orb.bootstrap.infrastructure_services import register_infrastructure_services
from orb.config import PerformanceConfig
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.di.container import DIContainer
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.configuration.template_extension import AzureTemplateExtensionConfig
from orb.providers.azure.configuration.validator import (
    validate_azure_config,
    validate_azure_template,
)
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.infrastructure.azure_client import AzureClient
from orb.providers.azure.infrastructure.azure_handler_factory import AzureHandlerFactory
from orb.providers.azure.registration import (
    _build_azure_client_runtime_config,
    create_azure_config,
    create_azure_strategy,
    register_azure_provider,
    register_azure_services_with_di,
)
from orb.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestAzureProviderConfig:
    def test_loads_provider_settings_from_azure_environment_prefix(self, monkeypatch):
        monkeypatch.setenv("ORB_AZURE_REGION", "westeurope")

        assert AzureProviderConfig().region == "westeurope"

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
            create_azure_config(
                {
                    "provider_type": "azure",
                    "subscription_id": "not-a-uuid",
                }
            )

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
        result = validate_azure_template(
            {
                "template_id": "t1",
                "vm_size": "Standard_D4s_v5",
                "resource_group": "rg",
                "region": "eastus2",
                "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
                "image": {"publisher": "C", "offer": "o", "sku": "s"},
            }
        )
        assert result["valid"] is True

    def test_conflicting_location_and_region_is_invalid(self):
        result = validate_azure_template(
            {
                "template_id": "t1",
                "vm_size": "Standard_D4s_v5",
                "resource_group": "rg",
                "location": "eastus2",
                "region": "westeurope",
                "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
                "image": {"publisher": "C", "offer": "o", "sku": "s"},
            }
        )

        assert result["valid"] is False
        assert any("conflicting 'location' and 'region'" in e for e in result["errors"])

    def test_valid_template(self):
        result = validate_azure_template(
            {
                "template_id": "t1",
                "vm_size": "Standard_D4s_v5",
                "resource_group": "rg",
                "location": "eastus2",
                "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
                "image": {"publisher": "C", "offer": "o", "sku": "s"},
            }
        )
        assert result["valid"] is True

    def test_missing_image_source_is_invalid(self):
        result = validate_azure_template(
            {
                "template_id": "t1",
                "vm_size": "Standard_D4s_v5",
                "resource_group": "rg",
                "location": "eastus2",
                "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            }
        )
        assert result["valid"] is False
        assert any("image source is required" in e for e in result["errors"])

    def test_missing_required_fields(self):
        result = validate_azure_template({})
        assert result["valid"] is False
        assert any("vm_size" in e for e in result["errors"])
        assert any("resource_group" in e for e in result["errors"])
        assert any("location" in e for e in result["errors"])

    def test_uncommon_vm_size_warning(self):
        result = validate_azure_template(
            {
                "template_id": "t1",
                "vm_size": "custom_vm_size",
                "resource_group": "rg",
                "location": "eastus2",
                "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
                "image": {"publisher": "C", "offer": "o", "sku": "s"},
            }
        )
        assert result["valid"] is True
        assert any("Uncommon" in w for w in result["warnings"])

    def test_spot_regular_conflict(self):
        result = validate_azure_template(
            {
                "vm_size": "Standard_D4s_v5",
                "resource_group": "rg",
                "location": "eastus2",
                "image": {"publisher": "C", "offer": "o", "sku": "s"},
                "priority": "Regular",
                "eviction_policy": "Delete",
            }
        )
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
    def test_bootstrap_template_factory_preserves_azure_image_templates(self):
        container = DIContainer()
        container.register_instance(LoggingPort, MagicMock())
        register_infrastructure_services(container)

        factory = container.get(TemplateFactory)
        template = factory.create_template(
            {
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
                "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
                "network_config": {
                    "subnet_id": (
                        "/subscriptions/a6eb5b32-65bb-47c0-a2b8-34fa90400a4b/"
                        "resourceGroups/orb-test-rg/providers/Microsoft.Network/"
                        "virtualNetworks/orb-test-vnet/subnets/default"
                    )
                },
            }
        )

        assert template.image.publisher == "Canonical"

    def test_template_factory_accepts_azure_template_dto_shape(self):
        container = DIContainer()
        container.register_instance(LoggingPort, MagicMock())
        register_infrastructure_services(container)

        template_dto = TemplateDTO(
            template_id="azure-cheapest-vmss",
            provider_type="azure",
            provider_api="VMSS",
            provider_config=AzureTemplateExtensionConfig(
                vm_size="Standard_B1s",
                resource_group="orb-test-rg",
                location="eastus2",
                image={
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            ),
            provider_data={},
        )

        template = container.get(TemplateFactory).create_template(template_dto.to_dict())

        assert isinstance(template, AzureTemplate)
        assert template.vm_size == "Standard_B1s"
        assert template.provider_data == {}

    def test_template_factory_does_not_downgrade_invalid_azure_template(self):
        container = DIContainer()
        container.register_instance(LoggingPort, MagicMock())
        register_infrastructure_services(container)

        with pytest.raises(ValidationError, match="vm_size"):
            container.get(TemplateFactory).create_template(
                {
                    "template_id": "invalid-azure-template",
                    "provider_type": "azure",
                    "provider_api": "VMSS",
                }
            )

    def test_registry_created_strategy_gets_azure_client_resolver(self):
        registry = ProviderRegistry()
        registry.clear_registrations()
        perf_config = PerformanceConfig(
            enable_batching=False,
            enable_parallel=False,
            max_workers=6,
            caching={"request_status": {"enabled": False, "ttl_seconds": 17}},
        )
        config_port = MagicMock()
        config_port.get_typed.side_effect = lambda config_type: (
            perf_config if config_type is PerformanceConfig else None
        )
        register_azure_provider(registry=registry, config_port=config_port)

        strategy = registry.create_strategy(
            "azure",
            {
                "provider_type": "azure",
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "resource_group": "rg-explicit",
                "region": "westeurope",
            },
        )

        assert strategy.azure_client.subscription_id == "12345678-1234-1234-1234-123456789012"
        assert strategy.azure_client.resource_group == "rg-explicit"
        assert strategy.azure_client.perf_config["max_workers"] == 6
        assert strategy.azure_client.perf_config["cache_ttl"] == 17

    def test_create_azure_strategy_resolves_performance_config_per_strategy_creation(self):
        perf_configs = [
            PerformanceConfig(
                enable_batching=False,
                enable_parallel=False,
                max_workers=6,
                caching={"request_status": {"enabled": False, "ttl_seconds": 17}},
            ),
            PerformanceConfig(
                enable_batching=True,
                enable_parallel=True,
                max_workers=9,
                caching={"request_status": {"enabled": True, "ttl_seconds": 44}},
            ),
        ]
        config_port = MagicMock()
        config_port.get_typed.side_effect = list(perf_configs)
        first_strategy = create_azure_strategy(
            {
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "resource_group": "rg-explicit",
                "region": "westeurope",
            },
            provider_instance_name="azure-test",
            config_port=config_port,
        )
        second_strategy = create_azure_strategy(
            {
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "resource_group": "rg-explicit",
                "region": "westeurope",
            },
            provider_instance_name="azure-test",
            config_port=config_port,
        )

        assert first_strategy.azure_client.perf_config["max_workers"] == 6
        assert second_strategy.azure_client.perf_config["max_workers"] == 9
        assert config_port.get_typed.call_count == 2

    def test_vmss_handler_receives_explicit_optional_services_from_factory(self):
        azure_client = MagicMock(spec=AzureClient)
        logger = MagicMock()
        azure_native_spec_service = MagicMock()
        azure_resource_manager = MagicMock()

        factory = AzureHandlerFactory(
            azure_client=azure_client,
            logger=logger,
            azure_native_spec_service=azure_native_spec_service,
            azure_resource_manager=azure_resource_manager,
        )

        handler = factory.create_handler(AzureProviderApi.VMSS)

        assert handler.azure_native_spec_service is azure_native_spec_service
        assert handler.azure_resource_manager is azure_resource_manager

    def test_single_vm_handler_receives_explicit_native_spec_service_from_factory(self):
        azure_client = MagicMock(spec=AzureClient)
        logger = MagicMock()
        azure_native_spec_service = MagicMock()

        factory = AzureHandlerFactory(
            azure_client=azure_client,
            logger=logger,
            azure_native_spec_service=azure_native_spec_service,
        )

        handler = factory.create_handler(AzureProviderApi.SINGLE_VM)

        assert handler.azure_native_spec_service is azure_native_spec_service

    def test_create_azure_strategy_uses_explicit_native_spec_service(self):
        azure_native_spec_service = MagicMock()

        strategy = create_azure_strategy(
            {
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "resource_group": "rg-explicit",
                "region": "westeurope",
            },
            provider_instance_name="azure-test",
            azure_native_spec_service=azure_native_spec_service,
        )

        assert strategy._azure_native_spec_service is azure_native_spec_service


# ---------------------------------------------------------------------------
# Template extension
# ---------------------------------------------------------------------------


class TestTemplateExtension:
    def test_defaults_do_not_include_vm_size_without_explicit_config(self):
        ext = AzureTemplateExtensionConfig()
        defaults = ext.to_template_defaults()
        assert "vm_size" not in defaults

    def test_defaults_include_explicit_vm_size(self):
        ext = AzureTemplateExtensionConfig(vm_size="Standard_B1s")
        defaults = ext.to_template_defaults()
        assert defaults["vm_size"] == "Standard_B1s"

    def test_defaults_include_structured_os_disk(self):
        ext = AzureTemplateExtensionConfig(
            os_disk={
                "storage_account_type": "StandardSSD_LRS",
                "caching": "ReadOnly",
                "ephemeral_os_disk": True,
            }
        )

        defaults = ext.to_template_defaults()

        assert defaults["os_disk"] == {
            "storage_account_type": "StandardSSD_LRS",
            "caching": "ReadOnly",
            "ephemeral_os_disk": True,
            "ephemeral_placement": "CacheDisk",
        }

    def test_defaults_include_legacy_os_disk_type_without_size(self):
        ext = AzureTemplateExtensionConfig(os_disk_type="StandardSSD_LRS")

        defaults = ext.to_template_defaults()

        assert defaults["os_disk"] == {"storage_account_type": "StandardSSD_LRS"}

    def test_defaults_preserve_spot_placement_score_fields(self):
        ext = AzureTemplateExtensionConfig(
            spot_placement_score_enabled=True,
            placement_split_strategy="hybrid",
            placement_primary_share_percent=80,
            placement_regions=["eastus2"],
            placement_zones=["1", "2", "3"],
        )

        defaults = ext.to_template_defaults()

        assert defaults["spot_placement_score_enabled"] is True
        assert defaults["placement_split_strategy"] == "hybrid"
        assert defaults["placement_primary_share_percent"] == 80
        assert defaults["placement_regions"] == ["eastus2"]
        assert defaults["placement_zones"] == ["1", "2", "3"]


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

    def test_build_azure_client_runtime_config_forwards_performance_config(self):
        azure_config = AzureProviderConfig(
            subscription_id="12345678-1234-1234-1234-123456789012",
            resource_group="rg",
            region="westeurope",
        )
        perf_config = PerformanceConfig(
            enable_batching=False,
            enable_parallel=False,
            max_workers=4,
            caching={"request_status": {"enabled": False, "ttl_seconds": 21}},
        )
        fallback_config = Mock()
        fallback_config.get_typed.side_effect = lambda config_type: (
            perf_config if config_type is PerformanceConfig else None
        )

        runtime_config = _build_azure_client_runtime_config(
            azure_config,
            logger=Mock(),
            config_port=fallback_config,
        )

        assert runtime_config.azure_config is azure_config
        assert runtime_config.performance_config is perf_config
        fallback_config.get_typed.assert_called_once_with(PerformanceConfig)

    def test_create_azure_strategy_forwards_runtime_performance_config_to_client(self):
        perf_config = PerformanceConfig(
            enable_batching=False,
            enable_parallel=False,
            max_workers=4,
            caching={"request_status": {"enabled": False, "ttl_seconds": 21}},
        )
        strategy = create_azure_strategy(
            {
                "subscription_id": "12345678-1234-1234-1234-123456789012",
                "resource_group": "rg",
                "region": "westeurope",
            },
            provider_instance_name="azure-test",
            performance_config=perf_config,
        )

        assert strategy.azure_client.perf_config["enable_batching"] is False
        assert strategy.azure_client.perf_config["enable_parallel"] is False
        assert strategy.azure_client.perf_config["max_workers"] == 4
        assert strategy.azure_client.perf_config["enable_caching"] is False
        assert strategy.azure_client.perf_config["cache_ttl"] == 21

    def test_register_services_populates_template_example_generator_registry(self):
        from orb.infrastructure.registry.template_example_generator_registry import (
            TemplateExampleGeneratorRegistry,
        )

        TemplateExampleGeneratorRegistry.clear()
        logger = Mock()
        container = Mock()
        container.get.return_value = logger
        container.is_registered.return_value = True

        register_azure_services_with_di(container)

        generator = TemplateExampleGeneratorRegistry.get("azure")
        assert generator is not None
        assert generator.generate_example_templates("azure-default")

        TemplateExampleGeneratorRegistry.clear()


def test_azure_template_example_generator_filters_by_provider_api() -> None:
    from orb.providers.azure.infrastructure.adapters.template_example_generator_adapter import (
        AzureTemplateExampleGeneratorAdapter,
    )

    examples = AzureTemplateExampleGeneratorAdapter().generate_example_templates(
        "azure-default", provider_api="SingleVM"
    )

    assert len(examples) == 1
    assert examples[0]["provider_api"] == "SingleVM"
