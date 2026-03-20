"""Tests for the Azure Provider Strategy.

Uses mock objects (no real Azure SDK) to test the full strategy lifecycle:
initialise, execute each operation type, health checks, capabilities, cleanup.
"""

import asyncio

import pytest
from unittest.mock import MagicMock

from application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
)
from providers.azure.infrastructure.services.spot_placement_score_adapter import (
    AzureSpotPlacementScoreAdapter,
)
from providers.azure.configuration.config import AzureProviderConfig
from providers.azure.exceptions.azure_exceptions import CycleCloudConnectionError
from providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
from providers.base.strategy import (
    ProviderOperation,
    ProviderOperationType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def azure_config():
    return AzureProviderConfig(
        subscription_id="12345678-1234-1234-1234-123456789012",
        resource_group="test-rg",
        region="eastus2",
    )


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def strategy(azure_config, logger):
    s = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
    s.initialize()
    return s


def _run(coro):
    """Helper to run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInitialization:
    def test_requires_azure_config(self, logger):
        with pytest.raises(ValueError, match="AzureProviderConfig"):
            AzureProviderStrategy(
                config={"region": "x"},
                logger=logger,
                provider_instance_name="azure-default",
            )

    def test_not_initialized_returns_error(self, azure_config, logger):
        s = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        # Do NOT call s.initialize()
        op = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
        )
        result = _run(s.execute_operation(op))
        assert not result.success
        assert result.error_code == "NOT_INITIALIZED"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_supported_operations(self, strategy):
        caps = strategy.get_capabilities()
        assert caps.provider_type == "azure"
        assert ProviderOperationType.CREATE_INSTANCES in caps.supported_operations
        assert ProviderOperationType.TERMINATE_INSTANCES in caps.supported_operations
        assert ProviderOperationType.HEALTH_CHECK in caps.supported_operations

    def test_features(self, strategy):
        caps = strategy.get_capabilities()
        assert caps.features["spot_instances"] is True
        assert caps.features["supports_linux"] is True


class TestCapacityMetadata:
    def test_vmss_capacity_uses_provisioned_instance_count(self, strategy):
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 10,
            "provisioned_instance_count": 7,
            "provisioning_state": "Updating",
        }

        metadata = {}
        strategy._augment_vmss_capacity_metadata(metadata, ["vmss-demo"])

        assert metadata["fleet_capacity_fulfilment"] == {
            "target_capacity_units": 10,
            "fulfilled_capacity_units": 7,
            "provisioned_instance_count": 7,
            "state": "Updating",
        }

    def test_vmss_capacity_uses_explicit_resource_group_argument_when_provided(self, strategy):
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 4,
            "provisioned_instance_count": 2,
            "provisioning_state": "Updating",
        }

        metadata = {}
        strategy._augment_vmss_capacity_metadata(
            metadata,
            ["vmss-demo"],
            resource_group="override-rg",
        )

        strategy._resource_manager.get_vmss_capacity.assert_called_once_with(
            "override-rg",
            "vmss-demo",
        )
        assert metadata["fleet_capacity_fulfilment"]["target_capacity_units"] == 4

    def test_vmss_capacity_aggregates_multiple_resources(self, strategy):
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.side_effect = [
            {
                "capacity": 4,
                "provisioned_instance_count": 2,
                "provisioning_state": "Updating",
            },
            {
                "capacity": 3,
                "provisioned_instance_count": 1,
                "provisioning_state": "Updating",
            },
        ]

        metadata = {}
        strategy._augment_vmss_capacity_metadata(
            metadata,
            ["vmss-a", "vmss-b"],
            resource_group="override-rg",
        )

        assert metadata["fleet_capacity_fulfilment"] == {
            "target_capacity_units": 7,
            "fulfilled_capacity_units": 3,
            "provisioned_instance_count": 3,
            "state": "Updating",
        }
        assert metadata["fleet_capacity_fulfilment_by_resource"] == {
            "vmss-a": {
                "target_capacity_units": 4,
                "fulfilled_capacity_units": 2,
                "provisioned_instance_count": 2,
                "state": "Updating",
            },
            "vmss-b": {
                "target_capacity_units": 3,
                "fulfilled_capacity_units": 1,
                "provisioned_instance_count": 1,
                "state": "Updating",
            },
        }

    def test_describe_resource_instances_surfaces_vmss_errors_without_instances(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        handler.get_vmss_resource_errors.return_value = [
            {
                "error_code": "ProvisioningStateFailed",
                "error_message": "VMSS provisioning failed",
            }
        ]
        strategy._handlers["VMSS"] = handler
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 3,
            "provisioned_instance_count": 0,
            "provisioning_state": "Failed",
        }

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["instances"] == []
        assert result.metadata["fleet_errors"][0]["error_code"] == "ProvisioningStateFailed"

    def test_describe_resource_instances_adds_shortfall_summary(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = [
            {
                "instance_id": "vm-1",
                "status": "running",
                "private_ip": None,
                "public_ip": None,
                "launch_time": None,
                "instance_type": "Standard_D4s_v5",
                "subnet_id": None,
                "vpc_id": None,
                "provider_data": {
                    "fleet_errors": [
                        {
                            "error_code": "AllocationFailed",
                            "error_message": "No capacity in selected zone",
                        }
                    ]
                },
            }
        ]
        strategy._handlers["VMSS"] = handler
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 3,
            "provisioned_instance_count": 1,
            "provisioning_state": "Updating",
        }

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["capacity_shortfall"]["missing_capacity_units"] == 2
        assert result.metadata["capacity_shortfall"]["likely_causes"] == ["AllocationFailed"]

    def test_describe_resource_instances_uses_request_metadata_resource_group_for_capacity(
        self, strategy
    ):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        handler.get_vmss_resource_errors.return_value = []
        strategy._handlers["VMSS"] = handler
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 2,
            "provisioned_instance_count": 0,
            "provisioning_state": "Updating",
        }

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {"resource_group": "custom-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        strategy._resource_manager.get_vmss_capacity.assert_called_once_with(
            "custom-rg",
            "vmss-demo",
        )

    def test_describe_resource_instances_uses_request_metadata_resource_group_when_present(
        self, strategy
    ):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        handler.get_vmss_resource_errors.return_value = []
        strategy._handlers["VMSS"] = handler
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 2,
            "provisioned_instance_count": 0,
            "provisioning_state": "Updating",
        }

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {"resource_group": "context-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        strategy._resource_manager.get_vmss_capacity.assert_called_once_with(
            "context-rg",
            "vmss-demo",
        )

    def test_describe_resource_instances_aggregates_capacity_for_multiple_vmss(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        handler.get_vmss_resource_errors.return_value = []
        strategy._handlers["VMSS"] = handler
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.side_effect = [
            {
                "capacity": 4,
                "provisioned_instance_count": 2,
                "provisioning_state": "Updating",
            },
            {
                "capacity": 3,
                "provisioned_instance_count": 1,
                "provisioning_state": "Updating",
            },
        ]

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-a", "vmss-b"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["fleet_capacity_fulfilment"] == {
            "target_capacity_units": 7,
            "fulfilled_capacity_units": 3,
            "provisioned_instance_count": 3,
            "state": "Updating",
        }
        assert result.metadata["fleet_capacity_fulfilment_by_resource"] == {
            "vmss-a": {
                "target_capacity_units": 4,
                "fulfilled_capacity_units": 2,
                "provisioned_instance_count": 2,
                "state": "Updating",
            },
            "vmss-b": {
                "target_capacity_units": 3,
                "fulfilled_capacity_units": 1,
                "provisioned_instance_count": 1,
                "state": "Updating",
            },
        }


# ---------------------------------------------------------------------------
# VALIDATE_TEMPLATE
# ---------------------------------------------------------------------------


class TestValidateTemplate:
    def test_valid_template(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={
                "template_config": {
                    "template_id": "t1",
                    "vm_size": "Standard_D4s_v5",
                    "resource_group": "rg",
                    "location": "eastus2",
                    "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
                    "image": {
                        "publisher": "Canonical",
                        "offer": "0001-com-ubuntu-server-jammy",
                        "sku": "22_04-lts-gen2",
                        "version": "latest",
                    },
                },
            },
        )
        result = _run(strategy.execute_operation(op))
        assert result.success
        assert result.data["valid"] is True
        assert result.data["errors"] == []

    def test_invalid_template_missing_fields(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={
                "template_config": {"template_id": "bad"},
            },
        )
        result = _run(strategy.execute_operation(op))
        assert result.success  # validation itself doesn't fail the operation
        assert result.data["valid"] is False
        assert len(result.data["errors"]) > 0

    def test_missing_template_config(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={},
        )
        result = _run(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_TEMPLATE_CONFIG"


# ---------------------------------------------------------------------------
# GET_AVAILABLE_TEMPLATES
# ---------------------------------------------------------------------------


class TestGetAvailableTemplates:
    def test_returns_fallback_templates(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_AVAILABLE_TEMPLATES,
            parameters={},
        )
        result = _run(strategy.execute_operation(op))
        assert result.success
        assert isinstance(result.data["templates"], list)
        assert result.data["count"] >= 1


# ---------------------------------------------------------------------------
# UNSUPPORTED_OPERATION
# ---------------------------------------------------------------------------


class TestUnsupportedOperation:
    def test_unsupported_operation(self, strategy):
        op = ProviderOperation(
            operation_type="totally_unknown",
            parameters={},
        )
        result = _run(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "UNSUPPORTED_OPERATION"


# ---------------------------------------------------------------------------
# CREATE_INSTANCES (with missing config → error path)
# ---------------------------------------------------------------------------


class TestCreateInstances:
    def test_missing_template_config_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
        )
        result = _run(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_TEMPLATE_CONFIG"

    def test_dry_run_short_circuits_before_handler(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "azure-vmss-test",
                    "provider_api": "VMSS",
                    "vm_size": "Standard_D4s_v5",
                    "resource_group": "test-rg",
                    "location": "eastus2",
                    "ssh_public_keys": [
                        "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"
                    ],
                    "image": {
                        "publisher": "Canonical",
                        "offer": "0001-com-ubuntu-server-jammy",
                        "sku": "22_04-lts-gen2",
                        "version": "latest",
                    },
                },
                "count": 2,
            },
            context={"dry_run": True},
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["dry-run-resource-id"]
        assert result.data["count"] == 2
        assert result.metadata["method"] == "dry_run"
        assert result.metadata["provider_data"] == {"dry_run": True}
        handler.acquire_hosts.assert_not_called()

    def test_create_instances_preserves_named_provider_instance_on_synthesized_request(
        self, azure_config, logger
    ):
        strategy = AzureProviderStrategy(
            config=azure_config,
            logger=logger,
            provider_instance_name="azure-test",
        )
        strategy.initialize()

        def acquire_hosts(request, _template):
            assert request.provider_instance == "azure-test"
            return {
                "success": True,
                "resource_ids": ["vmss-demo"],
                "instances": [],
                "provider_data": {"resource_group": "test-rg"},
            }

        handler = MagicMock()
        handler.acquire_hosts.side_effect = acquire_hosts
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "azure-vmss-test",
                    "provider_api": "VMSS",
                    "vm_size": "Standard_D4s_v5",
                    "resource_group": "test-rg",
                    "location": "eastus2",
                    "ssh_public_keys": [
                        "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"
                    ],
                    "image": {
                        "publisher": "Canonical",
                        "offer": "0001-com-ubuntu-server-jammy",
                        "sku": "22_04-lts-gen2",
                        "version": "latest",
                    },
                },
                "count": 1,
                "request_id": "12345678-1234-1234-1234-123456789012",
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["vmss-demo"]


# ---------------------------------------------------------------------------
# TERMINATE_INSTANCES (with missing ids → error path)
# ---------------------------------------------------------------------------


class TestTerminateInstances:
    def test_missing_instance_ids_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={},
        )
        result = _run(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_INSTANCE_IDS"

    def test_fallback_handler_uses_grouped_resource_ids(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-1"],
                "provider_api": "VMSS",
                "resource_mapping": {
                    "orb-1": ("vmss-prod-b", 1),
                },
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        handler.release_hosts.assert_called_once_with(
            machine_ids=["orb-1"],
            resource_id="vmss-prod-b",
            context={"resource_group": "test-rg", "resource_id": "vmss-prod-b"},
        )

    def test_dry_run_short_circuits_before_release(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-1", "orb-2"],
                "provider_api": "VMSS",
            },
            context={"dry_run": True},
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["terminated_count"] == 2
        assert result.metadata["method"] == "dry_run"
        handler.release_hosts.assert_not_called()

    def test_terminate_instances_records_pending_vmss_reconciliation(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        handler.release_hosts.return_value = {
            "provider_data": {
                "pending_reconciliation": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-prod-b",
                    "machine_ids": ["orb-1"],
                    "target_capacity": 2,
                    "orchestration_mode": "Flexible",
                    "delete_vmss_when_empty": False,
                }
            }
        }
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-1"],
                "provider_api": "VMSS",
                "resource_mapping": {
                    "orb-1": ("vmss-prod-b", 1),
                },
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert (
            strategy._pending_vmss_termination_reconciliations[("test-rg", "vmss-prod-b")][
                "target_capacity"
            ]
            == 2
        )

    def test_terminate_instances_merges_pending_vmss_reconciliation_for_same_vmss(
        self, azure_config, logger
    ):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        handler.release_hosts.side_effect = [
            {
                "provider_data": {
                    "pending_reconciliation": {
                        "resource_group": "test-rg",
                        "vmss_name": "vmss-prod-b",
                        "machine_ids": ["orb-1"],
                        "target_capacity": 4,
                        "orchestration_mode": "Flexible",
                        "delete_vmss_when_empty": False,
                    }
                }
            },
            {
                "provider_data": {
                    "pending_reconciliation": {
                        "resource_group": "test-rg",
                        "vmss_name": "vmss-prod-b",
                        "machine_ids": ["orb-2"],
                        "target_capacity": 4,
                        "orchestration_mode": "Flexible",
                        "delete_vmss_when_empty": False,
                    }
                }
            },
        ]
        strategy._handlers = {"VMSS": handler}

        first_op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-1"],
                "provider_api": "VMSS",
                "resource_mapping": {
                    "orb-1": ("vmss-prod-b", 1),
                },
            },
        )
        second_op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-2"],
                "provider_api": "VMSS",
                "resource_mapping": {
                    "orb-2": ("vmss-prod-b", 1),
                },
            },
        )

        first_result = _run(strategy.execute_operation(first_op))
        second_result = _run(strategy.execute_operation(second_op))

        assert first_result.success
        assert second_result.success
        assert strategy._pending_vmss_termination_reconciliations[("test-rg", "vmss-prod-b")] == {
            "resource_group": "test-rg",
            "vmss_name": "vmss-prod-b",
            "machine_ids": ["orb-1", "orb-2"],
            "target_capacity": 4,
            "orchestration_mode": "Flexible",
            "delete_vmss_when_empty": False,
        }

    def test_terminate_instances_forwards_cyclecloud_secret_reference_context(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        strategy._handlers = {"CycleCloud": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["node-1"],
                "provider_api": "CycleCloud",
                "resource_id": "my-cluster",
            },
            context={
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_credential_path": "config/cc.json",
                "cyclecloud_verify_ssl": False,
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
                "cyclecloud_bearer_token": "tok-123",
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        handler.release_hosts.assert_called_once_with(
            machine_ids=["node-1"],
            resource_id="my-cluster",
            context={
                "resource_group": "test-rg",
                "resource_id": "my-cluster",
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_credential_path": "config/cc.json",
                "cyclecloud_verify_ssl": False,
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
                "cyclecloud_bearer_token": "tok-123",
            },
        )


# ---------------------------------------------------------------------------
# GET_INSTANCE_STATUS (with missing ids → error path)
# ---------------------------------------------------------------------------


class TestGetInstanceStatus:
    def test_missing_instance_ids_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={},
        )
        result = _run(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_INSTANCE_IDS"

    def test_get_instance_status_uses_request_metadata_resource_group(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = [
            {
                "instance_id": "vm-1",
                "status": "running",
                "private_ip": "10.0.0.4",
                "public_ip": None,
                "launch_time": None,
                "instance_type": "Standard_D4s_v5",
                "subnet_id": "subnet-1",
                "vpc_id": "vnet-1",
                "provider_type": "azure",
                "provider_data": {
                    "resource_group": "context-rg",
                    "vm_name": "vm-1",
                },
            }
        ]
        strategy._handlers["SingleVM"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vm-1"],
                "provider_api": "SingleVM",
                "request_metadata": {"resource_group": "context-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["queried_count"] == 1
        handler.check_hosts_status.assert_called_once()
        request = handler.check_hosts_status.call_args.args[0]
        assert request.metadata["resource_group"] == "context-rg"

    def test_dry_run_short_circuits_status_lookup(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["vm-1", "vm-2"]},
            context={"dry_run": True},
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["queried_count"] == 2
        assert [m["instance_id"] for m in result.data["machines"]] == ["vm-1", "vm-2"]
        assert result.metadata["method"] == "dry_run"

    def test_single_vm_provider_api_routes_status_via_handler(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        handler.check_hosts_status.return_value = [
            {
                "instance_id": "vm-1",
                "status": "running",
                "private_ip": "10.0.0.4",
                "public_ip": None,
                "launch_time": None,
                "instance_type": "Standard_D4s_v5",
                "subnet_id": "/subscriptions/.../subnets/default",
                "vpc_id": "/subscriptions/.../virtualNetworks/test-vnet",
                "provider_type": "azure",
                "provider_data": {"vm_name": "vm-1"},
            }
        ]
        strategy._handlers = {"SingleVM": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vm-1"],
                "provider_api": "SingleVM",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["method"] == "handler"
        handler.check_hosts_status.assert_called_once()
        assert result.data["machines"][0]["instance_id"] == "vm-1"

    def test_vmss_provider_api_routes_status_via_handler_with_resource_mapping(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        handler.check_hosts_status.return_value = [
            {
                "instance_id": "3",
                "status": "running",
                "private_ip": "10.0.0.7",
                "public_ip": None,
                "launch_time": None,
                "instance_type": "Standard_D4s_v5",
                "subnet_id": "/subscriptions/.../subnets/default",
                "vpc_id": "/subscriptions/.../virtualNetworks/test-vnet",
                "provider_type": "azure",
                "provider_data": {
                    "vmss_instance_id": "3",
                    "vm_id": "vm-guid-3",
                },
            },
            {
                "instance_id": "9",
                "status": "running",
                "private_ip": "10.0.0.9",
                "public_ip": None,
                "launch_time": None,
                "instance_type": "Standard_D4s_v5",
                "subnet_id": "/subscriptions/.../subnets/default",
                "vpc_id": "/subscriptions/.../virtualNetworks/test-vnet",
                "provider_type": "azure",
                "provider_data": {
                    "vmss_instance_id": "9",
                    "vm_id": "vm-guid-9",
                },
            },
        ]
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["3"],
                "provider_api": "VMSS",
                "request_metadata": {"resource_group": "test-rg"},
                "resource_mapping": {"3": ("vmss-demo", 2)},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["method"] == "handler"
        handler.check_hosts_status.assert_called_once()
        assert [m["instance_id"] for m in result.data["machines"]] == ["3"]

    def test_vmss_resource_mapping_routes_status_via_handler_without_provider_api(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        handler.check_hosts_status.return_value = [
            {
                "instance_id": "3",
                "status": "running",
                "private_ip": "10.0.0.7",
                "public_ip": None,
                "launch_time": None,
                "instance_type": "Standard_D4s_v5",
                "subnet_id": "/subscriptions/.../subnets/default",
                "vpc_id": "/subscriptions/.../virtualNetworks/test-vnet",
                "provider_type": "azure",
                "provider_data": {
                    "vmss_instance_id": "3",
                    "vm_id": "vm-guid-3",
                },
            }
        ]
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["3"],
                "request_metadata": {"resource_group": "test-rg"},
                "resource_mapping": {"3": ("vmss-demo", 2)},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["method"] == "handler"
        handler.check_hosts_status.assert_called_once()
        assert [m["instance_id"] for m in result.data["machines"]] == ["3"]

    def test_cyclecloud_status_handler_failure_surfaces_error(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        handler.check_hosts_status.side_effect = CycleCloudConnectionError(
            "cyclecloud auth failed",
            url="https://cc.example.com",
        )
        strategy._handlers = {"CycleCloud": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["node-1"],
                "provider_api": "CycleCloud",
                "resource_id": "my-cluster",
                "request_metadata": {"resource_group": "test-rg"},
            },
            context={
                "cyclecloud_url": "https://cc.example.com",
            },
        )

        result = _run(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "GET_INSTANCE_STATUS_ERROR"
        assert "cyclecloud auth failed" in result.error_message

    def test_status_populates_network_identity(self, strategy):
        azure_client = MagicMock()
        strategy._client = azure_client

        nic_ref = MagicMock()
        nic_ref.id = (
            "/subscriptions/sub/resourceGroups/test-rg/providers/"
            "Microsoft.Network/networkInterfaces/nic-vm-1"
        )
        nic_ref.properties.primary = True

        vm = MagicMock()
        vm.name = "vm-1"
        vm.vm_id = "vm-guid-1"
        vm.instance_view.statuses = []
        vm.hardware_profile.vm_size = "Standard_D4s_v5"
        vm.zones = ["1"]
        vm.location = "eastus2"
        vm.network_profile.network_interfaces = [nic_ref]
        azure_client.compute_client.virtual_machines.get.return_value = vm
        azure_client.resolve_network_identity_from_vm.return_value = {
            "private_ip": "10.0.0.4",
            "public_ip": None,
            "subnet_id": (
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
            ),
            "vnet_id": (
                "/subscriptions/sub/resourceGroups/test-rg/providers/"
                "Microsoft.Network/virtualNetworks/test-vnet"
            ),
            "nic_id": nic_ref.id,
            "nic_name": "nic-vm-1",
        }

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vm-1"],
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["machines"][0]["private_ip"] == "10.0.0.4"
        assert result.data["machines"][0]["subnet_id"].endswith("/subnets/default")
        assert result.data["machines"][0]["vpc_id"].endswith("/virtualNetworks/test-vnet")


# ---------------------------------------------------------------------------
# DESCRIBE_RESOURCE_INSTANCES (with missing resource_ids → error path)
# ---------------------------------------------------------------------------


class TestDescribeResourceInstances:
    def test_missing_resource_ids_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={},
        )
        result = _run(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_RESOURCE_IDS"

    def test_describe_resource_instances_reconciles_pending_flexible_vmss_scale_down(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        handler.get_vmss_resource_errors.return_value = []
        strategy._handlers["VMSS"] = handler
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 2,
            "provisioned_instance_count": 0,
            "provisioning_state": "Updating",
        }
        strategy._pending_vmss_termination_reconciliations[("test-rg", "vmss-demo")] = {
            "resource_group": "test-rg",
            "vmss_name": "vmss-demo",
            "machine_ids": ["vm-a"],
            "target_capacity": 2,
            "orchestration_mode": "Flexible",
            "delete_vmss_when_empty": False,
        }

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        strategy._resource_manager.scale_vmss.assert_called_once_with(
            resource_group="test-rg",
            vmss_name="vmss-demo",
            capacity=2,
        )
        assert ("test-rg", "vmss-demo") not in strategy._pending_vmss_termination_reconciliations

    def test_describe_resource_instances_forwards_cyclecloud_request_metadata(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        strategy._handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["my-cluster"],
                "provider_api": "CycleCloud",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "node_array": "execute",
                    "node_ids": ["node-1"],
                    "cyclecloud_url": "https://cc.example.com",
                    "cyclecloud_auth_mode": "bearer",
                    "cyclecloud_aad_scope": "https://cc.example.com/.default",
                    "cyclecloud_bearer_token": "tok-123",
                    "cyclecloud_verify_ssl": False,
                },
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        forwarded_request = handler.check_hosts_status.call_args.args[0]
        assert forwarded_request.resource_ids == ["my-cluster"]
        assert forwarded_request.metadata["cluster_name"] == "my-cluster"
        assert forwarded_request.metadata["node_array"] == "execute"
        assert forwarded_request.metadata["node_ids"] == ["node-1"]
        assert forwarded_request.metadata["cyclecloud_url"] == "https://cc.example.com"
        assert forwarded_request.metadata["cyclecloud_auth_mode"] == "bearer"
        assert (
            forwarded_request.metadata["cyclecloud_aad_scope"]
            == "https://cc.example.com/.default"
        )
        assert forwarded_request.metadata["cyclecloud_bearer_token"] == "tok-123"
        assert forwarded_request.metadata["cyclecloud_verify_ssl"] is False

    def test_dry_run_short_circuits_resource_discovery(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        handler = MagicMock()
        strategy._handlers = {"VMSS": handler}

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-test"],
                "provider_api": "VMSS",
            },
            context={"dry_run": True},
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["instances"] == []
        assert result.metadata["method"] == "dry_run"
        handler.check_hosts_status.assert_not_called()


# ---------------------------------------------------------------------------
# Provider naming
# ---------------------------------------------------------------------------


class TestProviderNaming:
    def test_generate_provider_name(self, strategy):
        name = strategy.generate_provider_name({
            "subscription_id": "12345678-abcd",
            "region": "westeurope",
        })
        assert name.startswith("azure_")
        assert "westeurope" in name

    def test_parse_provider_name(self, strategy):
        parsed = strategy.parse_provider_name("azure_12345678_westeurope")
        assert parsed["type"] == "azure"
        assert parsed["subscription_id"] == "12345678"
        assert parsed["region"] == "westeurope"

    def test_get_provider_name_pattern(self, strategy):
        pattern = strategy.get_provider_name_pattern()
        assert "{type}" in pattern
        assert "{region}" in pattern


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_resets_state(self, strategy):
        strategy.cleanup()
        assert strategy._initialized is False
        assert strategy._handlers == {}
        assert strategy._client is None


class TestSpotPlacementPlanning:
    def test_create_instances_uses_planned_handler_path(self, strategy, monkeypatch):
        handler = MagicMock()
        handler.acquire_hosts.side_effect = [
            {"success": False, "error_message": "AllocationFailed: No capacity in selected zone"},
            {"success": True, "resource_ids": ["vmss-b"], "instances": []},
        ]
        strategy._handlers["VMSS"] = handler

        monkeypatch.setattr(
            strategy,
            "_build_spot_placement_plan",
            lambda template, count: [
                PlacementPlanEntry(
                    score=PlacementScore(
                        candidate=PlacementCandidate(
                            candidate_id="azure:eastus2:1:Standard_D4s_v5",
                            instance_type="Standard_D4s_v5",
                            region="eastus2",
                            zone="1",
                        ),
                        raw_score="High",
                        normalized_score=1.0,
                    ),
                    planned_count=2,
                ),
                PlacementPlanEntry(
                    score=PlacementScore(
                        candidate=PlacementCandidate(
                            candidate_id="azure:eastus2:2:Standard_D8s_v5",
                            instance_type="Standard_D8s_v5",
                            region="eastus2",
                            zone="2",
                        ),
                        raw_score="Medium",
                        normalized_score=0.6,
                    ),
                    planned_count=1,
                ),
            ],
        )

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "count": 2,
                "request_id": "req-11111111-1111-4111-8111-111111111111",
                "template_config": {
                    "template_id": "tmpl-1",
                    "provider_api": "VMSS",
                    "resource_group": "rg1",
                    "location": "eastus2",
                    "image": {
                        "image_id": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.Compute/images/img"
                    },
                    "vm_size": "Standard_D4s_v5",
                    "vm_sizes": ["Standard_D8s_v5"],
                    "price_type": "spot",
                    "priority": "Spot",
                    "allocation_strategy": "spotPlacementScore",
                    "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCu"],
                },
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["vmss-b"]
        assert result.metadata["method"] == "planned_handler"
        assert result.metadata["provider_data"]["unfulfilled_count"] == 0
        assert len(result.metadata["provider_data"]["child_results"]) == 2
        first_request = handler.acquire_hosts.call_args_list[0].args[0]
        assert str(first_request.request_id).startswith("req-")
        assert str(first_request.request_id) != "req-11111111-1111-4111-8111-111111111111"
        assert first_request.metadata["parent_request_id"] == "req-11111111-1111-4111-8111-111111111111"
        assert first_request.metadata["spot_placement_plan_entry_index"] == 0

    def test_create_instances_falls_back_when_scores_are_stale(self, strategy, monkeypatch):
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": True,
            "resource_ids": ["vmss-fallback"],
            "instances": [],
        }
        strategy._handlers["VMSS"] = handler

        monkeypatch.setattr(
            AzureSpotPlacementScoreAdapter,
            "score_candidates",
            lambda self, requested_count, template: [
                PlacementScore(
                    candidate=PlacementCandidate(
                        candidate_id="azure:eastus2:regional:Standard_D4s_v5",
                        instance_type="Standard_D4s_v5",
                        region="eastus2",
                    ),
                    raw_score="DataNotFoundOrStale",
                    normalized_score=0.0,
                    metadata={"raw_entry": {"score": "DataNotFoundOrStale"}},
                ),
                PlacementScore(
                    candidate=PlacementCandidate(
                        candidate_id="azure:eastus2:regional:Standard_D8s_v5",
                        instance_type="Standard_D8s_v5",
                        region="eastus2",
                    ),
                    raw_score="DataNotFoundOrStale",
                    normalized_score=0.0,
                    metadata={"raw_entry": {"score": "DataNotFoundOrStale"}},
                ),
            ],
        )

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "count": 2,
                "template_config": {
                    "template_id": "tmpl-stale",
                    "provider_api": "VMSS",
                    "resource_group": "rg1",
                    "location": "eastus2",
                    "image": {
                        "image_id": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.Compute/images/img"
                    },
                    "vm_size": "Standard_D4s_v5",
                    "vm_sizes": ["Standard_D8s_v5"],
                    "price_type": "spot",
                    "priority": "Spot",
                    "allocation_strategy": "spotPlacementScore",
                    "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCu"],
                },
            },
        )

        result = _run(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["vmss-fallback"]
        assert result.metadata["method"] == "planned_handler"
        serialized_plan = result.metadata["provider_data"]["placement_plan"]
        assert serialized_plan[0]["instance_type"] == "Standard_D4s_v5"
        assert serialized_plan[0]["planned_count"] == 2
        assert serialized_plan[0]["approximate"] is True
        assert serialized_plan[0]["metadata"]["fallback_reason"] == "no_viable_provider_scores"
