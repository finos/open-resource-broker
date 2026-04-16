"""Focused tests for Azure strategy create and terminate flows."""

from unittest.mock import MagicMock

import pytest

from orb.application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
)
from orb.infrastructure.template.dtos import TemplateDTO
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import (
    AzureValidationError,
    TerminationError,
)
from orb.providers.azure.infrastructure.cyclecloud_session import CycleCloudRequestContext
from orb.providers.azure.infrastructure.handlers.azure_handler import AzureReleaseContext
from orb.providers.azure.infrastructure.services.spot_placement_score_adapter import (
    AzureSpotPlacementScoreAdapter,
)
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from tests.providers.azure.strategy_test_support import build_strategy_harness, run_operation

class TestCreateInstances:
    def test_missing_template_config_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
        )
        result = run_operation(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_TEMPLATE_CONFIG"

    def test_missing_create_handler_returns_error(self, strategy):
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
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "HANDLER_NOT_FOUND"

    def test_dry_run_short_circuits_before_handler(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        strategy_harness.handlers["VMSS"] = handler

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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["dry-run-resource-id"]
        assert result.data["count"] == 2
        assert result.metadata["method"] == "dry_run"
        assert result.metadata["provider_data"] == {"dry_run": True}
        handler.acquire_hosts.assert_not_called()

    def test_create_instances_preserves_named_provider_instance_on_synthesized_request(
        self, azure_config, logger
    ):
        strategy_harness = build_strategy_harness(
            config=azure_config,
            logger=logger,
            provider_instance_name="azure-test",
        )
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": True,
            "resource_ids": ["vmss-demo"],
            "instances": [],
            "provider_data": {"resource_group": "test-rg"},
        }
        strategy_harness.handlers["VMSS"] = handler

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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["vmss-demo"]

    @pytest.mark.parametrize("provider_api", ["VMSS", "SingleVM"])
    def test_create_instances_coalesces_azure_defaults_before_validation(
        self, azure_config, logger, provider_api
    ):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": True,
            "resource_ids": ["azure-resource"],
            "instances": [],
            "provider_data": {"resource_group": "test-rg"},
        }
        strategy_harness.handlers[provider_api] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "azure-minimal-test",
                    "provider_api": provider_api,
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
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["azure-resource"]

    def test_create_instances_uses_image_from_template_dto_metadata_roundtrip(
        self, azure_config, logger
    ):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": True,
            "resource_ids": ["azure-resource"],
            "instances": [],
        }
        strategy_harness.handlers["VMSS"] = handler

        azure_template = AzureTemplate(
            template_id="azure-roundtrip-test",
            provider_api="VMSS",
            vm_size="Standard_D4s_v5",
            resource_group="test-rg",
            location="eastus2",
            ssh_public_keys=["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCu"],
            image={
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts-gen2",
                "version": "latest",
            },
        )
        dto = TemplateDTO.from_domain(azure_template)
        template_config = dto.to_template_config()

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": template_config,
                "count": 1,
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        handler.acquire_hosts.assert_called_once()

    def test_create_instances_accepts_enum_provider_api_in_template_config(
        self, azure_config, logger
    ):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": True,
            "resource_ids": ["vmss-demo"],
            "instances": [],
        }
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": {
                    "template_id": "azure-vmss-test",
                    "provider_api": AzureProviderApi.VMSS,
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
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["provider_api"] == "VMSS"

    def test_create_instances_preserves_azure_validation_failures(self, strategy_harness):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.side_effect = AzureValidationError(
            "VM size is not available in this region",
            error_code="SkuNotAvailable",
        )
        strategy_harness.handlers["VMSS"] = handler

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
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "SkuNotAvailable"
        assert result.metadata["error_class"] == "AzureValidationError"
        assert result.metadata["provider_error"]["error_code"] == "SkuNotAvailable"


# ---------------------------------------------------------------------------
# TERMINATE_INSTANCES (with missing ids → error path)
# ---------------------------------------------------------------------------


class TestTerminateInstances:
    def test_missing_instance_ids_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={},
        )
        result = run_operation(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_INSTANCE_IDS"

    def test_missing_provider_api_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"instance_ids": ["orb-1"]},
        )
        result = run_operation(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_PROVIDER_API"

    def test_missing_resource_id_returns_error(self, strategy_harness):
        strategy = strategy_harness.strategy
        strategy_harness.handlers["VMSS"] = MagicMock()

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"instance_ids": ["orb-1"], "provider_api": "VMSS"},
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "MISSING_RESOURCE_ID"

    def test_missing_terminate_handler_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"instance_ids": ["orb-1"], "provider_api": "VMSS"},
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "HANDLER_NOT_FOUND"

    def test_fallback_handler_uses_grouped_resource_ids(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        strategy_harness.handlers["VMSS"] = handler

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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        handler.release_hosts.assert_called_once()
        _, kwargs = handler.release_hosts.call_args
        assert kwargs["machine_ids"] == ["orb-1"]
        assert kwargs["resource_id"] == "vmss-prod-b"
        assert kwargs["context"] == AzureReleaseContext(
            resource_group="test-rg",
            resource_id="vmss-prod-b",
        )

    def test_dry_run_short_circuits_before_release(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-1", "orb-2"],
                "provider_api": "VMSS",
            },
            context={"dry_run": True},
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["terminated_count"] == 2
        assert result.metadata["method"] == "dry_run"
        handler.release_hosts.assert_not_called()

    def test_terminate_instances_records_pending_vmss_cleanup(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.release_hosts.return_value = {
            "provider_data": {
                "pending_resource_cleanup": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-prod-b",
                    "machine_ids": ["orb-1"],
                    "delete_vmss_when_empty": False,
                    "member_delete_submitted": True,
                    "delete_submission_semantics": "best_effort_without_reverification",
                    "delete_submitted": False,
                    "delete_retry_pending": False,
                }
            }
        }
        strategy_harness.handlers["VMSS"] = handler

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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["provider_data"]["termination_requests"] == [
            {
                "pending_resource_cleanup": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-prod-b",
                    "machine_ids": ["orb-1"],
                    "delete_vmss_when_empty": False,
                    "member_delete_submitted": True,
                    "delete_submission_semantics": "best_effort_without_reverification",
                    "delete_submitted": False,
                    "delete_retry_pending": False,
                }
            }
        ]

    def test_terminate_instances_merges_pending_vmss_cleanup_for_same_vmss(
        self, azure_config, logger
    ):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.release_hosts.side_effect = [
            {
                "provider_data": {
                    "pending_resource_cleanup": {
                        "resource_group": "test-rg",
                        "vmss_name": "vmss-prod-b",
                        "machine_ids": ["orb-1"],
                        "delete_vmss_when_empty": False,
                        "member_delete_submitted": True,
                        "delete_submission_semantics": "best_effort_without_reverification",
                        "delete_submitted": False,
                        "delete_retry_pending": False,
                    }
                }
            },
            {
                "provider_data": {
                    "pending_resource_cleanup": {
                        "resource_group": "test-rg",
                        "vmss_name": "vmss-prod-b",
                        "machine_ids": ["orb-2"],
                        "delete_vmss_when_empty": False,
                        "member_delete_submitted": True,
                        "delete_submission_semantics": "best_effort_without_reverification",
                        "delete_submitted": False,
                        "delete_retry_pending": False,
                    }
                }
            },
        ]
        strategy_harness.handlers["VMSS"] = handler

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

        first_result = run_operation(strategy.execute_operation(first_op))
        second_result = run_operation(strategy.execute_operation(second_op))

        assert first_result.success
        assert second_result.success
        assert first_result.metadata["provider_data"]["termination_requests"] == [
            {
                "pending_resource_cleanup": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-prod-b",
                    "machine_ids": ["orb-1"],
                    "delete_vmss_when_empty": False,
                    "member_delete_submitted": True,
                    "delete_submission_semantics": "best_effort_without_reverification",
                    "delete_submitted": False,
                    "delete_retry_pending": False,
                }
            }
        ]
        assert second_result.metadata["provider_data"]["termination_requests"] == [
            {
                "pending_resource_cleanup": {
                    "resource_group": "test-rg",
                    "vmss_name": "vmss-prod-b",
                    "machine_ids": ["orb-2"],
                    "delete_vmss_when_empty": False,
                    "member_delete_submitted": True,
                    "delete_submission_semantics": "best_effort_without_reverification",
                    "delete_submitted": False,
                    "delete_retry_pending": False,
                }
            }
        ]

    def test_terminate_instances_preserves_provider_failures(self, strategy_harness):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.release_hosts.side_effect = TerminationError(
            "Azure rejected the delete request",
            resource_ids=["vmss-prod-b"],
        )
        strategy_harness.handlers["VMSS"] = handler

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

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "TerminationError"
        assert result.metadata["error_class"] == "TerminationError"
        assert result.metadata["provider_error"]["details"]["resource_ids"] == ["vmss-prod-b"]

    def test_get_instance_status_restores_pending_vmss_cleanup_from_request_metadata(
        self, azure_config, logger
    ):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status.return_value = []
        strategy_harness.handlers["VMSS"] = handler

        resource_manager = MagicMock()
        resource_manager.get_vmss_member_count.return_value = 0
        strategy_harness.resource_manager = resource_manager

        azure_client = MagicMock()
        strategy_harness.azure_client = azure_client

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["orb-1"],
                "provider_api": "VMSS",
                "resource_id": "vmss-prod-b",
                "resource_mapping": {"orb-1": ("vmss-prod-b", 1)},
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-prod-b",
                                "machine_ids": ["orb-1"],
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        azure_client.compute_client.virtual_machine_scale_sets.begin_delete.assert_called_once_with(
            resource_group_name="test-rg",
            vm_scale_set_name="vmss-prod-b",
        )
        assert result.metadata["termination_follow_up_pending"] is True

    def test_terminate_instances_forwards_cyclecloud_secret_reference_request_metadata(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        strategy_harness.handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["node-1"],
                "provider_api": "CycleCloud",
                "request_metadata": {
                    "cluster_name": "my-cluster",
                    "cyclecloud_url": "https://cc.example.com",
                    "cyclecloud_credential_path": "config/cc.json",
                    "cyclecloud_verify_ssl": False,
                    "cyclecloud_auth_mode": "bearer",
                    "cyclecloud_aad_scope": "https://cc.example.com/.default",
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        handler.release_hosts.assert_called_once()
        _, kwargs = handler.release_hosts.call_args
        assert kwargs["machine_ids"] == ["node-1"]
        assert kwargs["resource_id"] == "my-cluster"
        assert kwargs["context"] == AzureReleaseContext(
            resource_group="test-rg",
            resource_id="my-cluster",
            cyclecloud_request_context=CycleCloudRequestContext(
                cluster_name="my-cluster",
                cyclecloud_url="https://cc.example.com",
                cyclecloud_credential_path="config/cc.json",
                cyclecloud_verify_ssl=False,
                cyclecloud_auth_mode="bearer",
                cyclecloud_aad_scope="https://cc.example.com/.default",
            ),
        )

    def test_terminate_instances_recovers_cyclecloud_context_from_origin_request(self, azure_config, logger):
        origin_request = MagicMock()
        origin_request.provider_data = {
            "follow_up_context": {
                "cluster_name": "my-cluster",
                "cyclecloud_url": "https://cc.example.com",
                "cyclecloud_credential_path": "config/cc.json",
                "cyclecloud_verify_ssl": False,
                "cyclecloud_auth_mode": "bearer",
                "cyclecloud_aad_scope": "https://cc.example.com/.default",
            }
        }
        lookup = MagicMock(return_value=origin_request)

        strategy_harness = build_strategy_harness(
            config=azure_config,
            logger=logger,
            provider_instance_name="azure-default",
            cyclecloud_request_lookup=lookup,
        )
        strategy = strategy_harness.strategy
        handler = MagicMock()
        strategy_harness.handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["node-1"],
                "provider_api": "CycleCloud",
                "request_id": "req-11111111-1111-4111-8111-111111111111",
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        handler.release_hosts.assert_called_once()
        _, kwargs = handler.release_hosts.call_args
        assert kwargs["machine_ids"] == ["node-1"]
        assert kwargs["resource_id"] == "my-cluster"
        assert kwargs["context"] == AzureReleaseContext(
            resource_group="test-rg",
            resource_id="my-cluster",
            cyclecloud_request_context=CycleCloudRequestContext(
                cluster_name="my-cluster",
                cyclecloud_url="https://cc.example.com",
                cyclecloud_credential_path="config/cc.json",
                cyclecloud_verify_ssl=False,
                cyclecloud_auth_mode="bearer",
                cyclecloud_aad_scope="https://cc.example.com/.default",
            ),
        )
        lookup.assert_called_once_with("req-11111111-1111-4111-8111-111111111111")

    def test_terminate_instances_accepts_enum_provider_api(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-1"],
                "provider_api": AzureProviderApi.VMSS,
                "resource_mapping": {"orb-1": ("vmss-prod-b", 1)},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        handler.release_hosts.assert_called_once()
        _, kwargs = handler.release_hosts.call_args
        assert kwargs["machine_ids"] == ["orb-1"]
        assert kwargs["resource_id"] == "vmss-prod-b"
        assert kwargs["context"] == AzureReleaseContext(
            resource_group="test-rg",
            resource_id="vmss-prod-b",
        )


class TestSpotPlacementPlanning:
    def test_create_instances_returns_generic_planning_error_for_capacity_exhaustion(
        self, strategy_harness, monkeypatch
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": False,
            "error_message": "AllocationFailed: No capacity in selected zone",
            "provider_data": {"error_codes": ["AllocationFailed"]},
        }
        strategy_harness.handlers["VMSS"] = handler

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
                    "vm_sizes": ["Standard_D4s_v5"],
                    "price_type": "spot",
                    "priority": "Spot",
                    "allocation_strategy": "spotPlacementScore",
                    "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCu"],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "PROVISIONING_ADAPTER_ERROR"
        assert result.error_message == "Spot placement plan could not provision any instances"

    def test_create_instances_returns_terminal_planning_error_for_non_capacity_failure(
        self, strategy_harness, monkeypatch
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": False,
            "error_message": "insufficient capacity",
            "provider_data": {"error_codes": ["OtherFailure"]},
        }
        strategy_harness.handlers["VMSS"] = handler

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
            ],
        )

        monkeypatch.setattr(
            strategy,
            "_is_capacity_like_failure",
            lambda result: False,
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
                    "vm_sizes": ["Standard_D4s_v5"],
                    "price_type": "spot",
                    "priority": "Spot",
                    "allocation_strategy": "spotPlacementScore",
                    "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCu"],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "PROVISIONING_ADAPTER_ERROR"
        assert result.error_message == "Provisioning failed: insufficient capacity"

    def test_create_instances_uses_planned_handler_path(self, strategy_harness, monkeypatch):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.side_effect = [
            {
                "success": False,
                "error_message": "AllocationFailed: No capacity in selected zone",
                "provider_data": {"error_codes": ["AllocationFailed"]},
            },
            {"success": True, "resource_ids": ["vmss-b"], "instances": []},
        ]
        strategy_harness.handlers["VMSS"] = handler

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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["vmss-b"]
        assert result.metadata["method"] == "planned_handler"
        assert result.metadata["provider_data"]["unfulfilled_count"] == 0

    def test_create_instances_falls_back_when_scores_are_stale(
        self, strategy_harness, monkeypatch
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.acquire_hosts.return_value = {
            "success": True,
            "resource_ids": ["vmss-fallback"],
            "instances": [],
        }
        strategy_harness.handlers["VMSS"] = handler

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

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["resource_ids"] == ["vmss-fallback"]
        assert result.metadata["method"] == "planned_handler"
        serialized_plan = result.metadata["provider_data"]["placement_plan"]
        assert serialized_plan[0]["instance_type"] == "Standard_D4s_v5"
        assert serialized_plan[0]["planned_count"] == 2
        assert serialized_plan[0]["approximate"] is True
        assert serialized_plan[0]["metadata"]["fallback_reason"] == "no_viable_provider_scores"
