"""Focused tests for Azure strategy core behavior."""

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate

from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.infrastructure.services.spot_placement_score_adapter import (
    AzureSpotPlacementScoreAdapter,
)
from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
from orb.providers.base.strategy import (
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
)
from tests.providers.azure.strategy_test_support import run_operation

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
        result = run_operation(s.execute_operation(op))
        assert not result.success
        assert result.error_code == "NOT_INITIALIZED"

    def test_execute_operation_propagates_cancellation(self, strategy, monkeypatch):
        async def cancelled(_operation):
            raise asyncio.CancelledError()

        monkeypatch.setattr(strategy, "_execute_operation_internal", cancelled)

        op = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
        )

        with pytest.raises(asyncio.CancelledError):
            run_operation(strategy.execute_operation(op))


class TestCapacityMetadata:
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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["fleet_errors"][0]["error_code"] == "ProvisioningStateFailed"

    def test_describe_resource_instances_surfaces_single_vm_deployment_errors_without_instances(
        self, strategy
    ):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        strategy._handlers["SingleVM"] = handler
        strategy._deployment_service = MagicMock()
        strategy._deployment_service.get_deployment_status.return_value = {
            "provisioning_state": "Failed",
            "error_code": "DeploymentFailed",
            "error_message": "Deployment failed during validation",
        }

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vm-a", "vm-b"],
                "provider_api": "SingleVM",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "deployment_name": "dep-singlevm-1",
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["deployment_name"] == "dep-singlevm-1"
        assert result.metadata["deployment_provisioning_state"] == "Failed"
        assert result.metadata["fleet_errors"][0]["error_code"] == "DeploymentFailed"

    def test_describe_resource_instances_returns_canonical_machine_shape(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = [
            {
                "instance_id": "vmss-demo_000001",
                "status": "running",
                "private_ip": "10.0.0.4",
                "public_ip": None,
                "instance_type": "Standard_B1s",
                "subnet_id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/default",
                "vpc_id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet",
                "provider_data": {"vmss_name": "vmss-demo"},
            }
        ]
        strategy._handlers["VMSS"] = handler
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_capacity.return_value = {
            "capacity": 1,
            "provisioned_instance_count": 1,
            "provisioning_state": "Succeeded",
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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["instances"][0]["instance_id"] == "vmss-demo_000001"
        assert result.data["instances"][0]["status"] == "running"
        assert "InstanceId" not in result.data["instances"][0]

    def test_get_instance_status_reconciles_empty_flexible_vmss_return(self, strategy):
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        strategy._handlers["VMSS"] = handler

        compute_client = MagicMock()
        azure_client = MagicMock()
        azure_client.compute_client = compute_client
        strategy._client = azure_client
        strategy._resource_manager = MagicMock()
        strategy._resource_manager.get_vmss_member_count.return_value = 0

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vmss-demo_000001"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "resource_id": "vmss-demo",
                "resource_mapping": {"vmss-demo_000001": ("vmss-demo", 1)},
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["vmss-demo_000001"],
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        compute_client.virtual_machine_scale_sets.begin_delete.assert_called_once_with(
            resource_group_name="test-rg",
            vm_scale_set_name="vmss-demo",
        )
        assert result.metadata["termination_follow_up_pending"] is True

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

        result = run_operation(strategy.execute_operation(op))

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

        result = run_operation(strategy.execute_operation(op))

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

        result = run_operation(strategy.execute_operation(op))

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

        result = run_operation(strategy.execute_operation(op))

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
        result = run_operation(strategy.execute_operation(op))
        assert result.success
        assert result.data["valid"] is True

    def test_invalid_template_missing_fields(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={
                "template_config": {"template_id": "bad"},
            },
        )
        result = run_operation(strategy.execute_operation(op))
        assert result.success  # validation itself doesn't fail the operation
        assert result.data["valid"] is False
        assert len(result.data["errors"]) > 0

    def test_missing_template_config(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={},
        )
        result = run_operation(strategy.execute_operation(op))
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
        result = run_operation(strategy.execute_operation(op))
        assert result.success
        assert result.data["count"] >= 1

    def test_fallback_templates_validate_as_azure_templates(self, strategy):
        for template in strategy._template_catalog_service.get_fallback_templates():
            AzureTemplate.model_validate(template)

# ---------------------------------------------------------------------------
# UNSUPPORTED_OPERATION
# ---------------------------------------------------------------------------


class TestUnsupportedOperation:
    def test_unsupported_operation(self, strategy):
        op = ProviderOperation(
            operation_type="totally_unknown",
            parameters={},
        )
        result = run_operation(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "UNSUPPORTED_OPERATION"


class TestSpotPlacementScoreAdapter:
    def test_score_candidates_uses_template_location_value_object(self, logger):
        adapter = AzureSpotPlacementScoreAdapter(
            azure_client=MagicMock(),
            logger=logger,
            subscription_id="12345678-1234-1234-1234-123456789012",
            base_location="westeurope",
        )
        template = AzureTemplate(
            template_id="azure-spot-score-test",
            provider_api="VMSS",
            vm_size="Standard_D4s_v5",
            resource_group="test-rg",
            location="eastus2",
            ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            image={
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts-gen2",
                "version": "latest",
            },
        )

        adapter._fetch_scores = MagicMock(return_value={})

        scores = adapter.score_candidates(requested_count=2, template=template)

        assert [score.candidate.region for score in scores] == ["eastus2"]
        adapter._fetch_scores.assert_called_once_with(
            requested_count=2,
            regions=["eastus2"],
            vm_sizes=["Standard_D4s_v5"],
            zones=[],
        )


# ---------------------------------------------------------------------------
# CREATE_INSTANCES (with missing config → error path)
# ---------------------------------------------------------------------------



class TestProviderNaming:
    def test_generate_provider_name(self, strategy):
        name = strategy.generate_provider_name({
            "subscription_id": "12345678-abcd",
            "region": "westeurope",
        })
        assert name == "azure_12345678-abcd_westeurope"

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
    def test_cleanup_closes_owned_azure_client(self, azure_config, logger):
        client = MagicMock()
        strategy = AzureProviderStrategy(
            config=azure_config,
            logger=logger,
            provider_instance_name="azure-default",
            azure_client_resolver=lambda: client,
        )
        strategy.initialize()
        strategy._client = client

        strategy.cleanup()

        client.close.assert_called_once_with()

    def test_cleanup_waits_for_in_flight_operation(self, azure_config, logger, monkeypatch):
        client = MagicMock()
        strategy = AzureProviderStrategy(
            config=azure_config,
            logger=logger,
            provider_instance_name="azure-default",
            azure_client_resolver=lambda: client,
        )
        strategy.initialize()
        strategy._client = client

        operation_started = threading.Event()
        release_operation = threading.Event()
        cleanup_finished = threading.Event()
        result_holder: list[ProviderResult] = []

        async def block_operation(_operation):
            operation_started.set()
            while not release_operation.is_set():
                await asyncio.sleep(0.01)
            return ProviderResult.success_result({"ok": True})

        monkeypatch.setattr(strategy, "_execute_operation_internal", block_operation)

        op = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
        )

        operation_thread = threading.Thread(
            target=lambda: result_holder.append(run_operation(strategy.execute_operation(op)))
        )
        operation_thread.start()
        assert operation_started.wait(timeout=1)

        cleanup_thread = threading.Thread(
            target=lambda: (strategy.cleanup(), cleanup_finished.set())
        )
        cleanup_thread.start()

        time.sleep(0.05)
        assert not cleanup_finished.is_set()
        client.close.assert_not_called()

        release_operation.set()
        operation_thread.join(timeout=1)
        cleanup_thread.join(timeout=1)

        assert cleanup_finished.is_set()
        assert result_holder[0].success is True
        client.close.assert_called_once_with()

    def test_execute_operation_rejects_new_work_after_cleanup_starts(
        self, azure_config, logger, monkeypatch
    ):
        client = MagicMock()
        strategy = AzureProviderStrategy(
            config=azure_config,
            logger=logger,
            provider_instance_name="azure-default",
            azure_client_resolver=lambda: client,
        )
        strategy.initialize()
        strategy._client = client

        operation_started = threading.Event()
        release_operation = threading.Event()

        async def block_operation(_operation):
            operation_started.set()
            while not release_operation.is_set():
                await asyncio.sleep(0.01)
            return ProviderResult.success_result({"ok": True})

        monkeypatch.setattr(strategy, "_execute_operation_internal", block_operation)

        op = ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
        )

        operation_thread = threading.Thread(target=lambda: run_operation(strategy.execute_operation(op)))
        operation_thread.start()
        assert operation_started.wait(timeout=1)

        cleanup_thread = threading.Thread(target=strategy.cleanup)
        cleanup_thread.start()
        time.sleep(0.05)

        rejected = run_operation(strategy.execute_operation(op))

        release_operation.set()
        operation_thread.join(timeout=1)
        cleanup_thread.join(timeout=1)

        assert not rejected.success
        assert rejected.error_code == "STRATEGY_SHUTTING_DOWN"
