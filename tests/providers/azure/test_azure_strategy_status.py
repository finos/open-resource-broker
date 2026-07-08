"""Focused tests for Azure strategy status and discovery flows."""
from unittest.mock import AsyncMock, MagicMock

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.value_objects import AzureProviderApi
from orb.providers.azure.exceptions.azure_exceptions import CycleCloudConnectionError
from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from tests.providers.azure.strategy_test_support import build_strategy_harness, run_operation

class TestGetInstanceStatus:
    def test_missing_instance_ids_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={},
        )
        result = run_operation(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_INSTANCE_IDS"

    def test_missing_resource_group_returns_error_when_not_in_request_or_config(self, logger):
        strategy = AzureProviderStrategy(
            config=AzureProviderConfig(
                subscription_id="12345678-1234-1234-1234-123456789012",
                resource_group=None,
                region="eastus2",
            ),
            logger=logger,
            provider_instance_name="azure-default",
        )
        strategy.initialize()

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["vm-1"]},
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "MISSING_RESOURCE_GROUP"

    def test_get_instance_status_uses_request_metadata_resource_group(self, strategy_harness):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[
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
        ])
        strategy_harness.handlers["SingleVM"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vm-1"],
                "provider_api": "SingleVM",
                "request_metadata": {"resource_group": "context-rg"},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["queried_count"] == 1
        assert result.metadata["provider_fulfilment"].state == "fulfilled"
        assert result.metadata["provider_fulfilment"].target_units == 1
        handler.check_hosts_status_async.assert_awaited_once()
    def test_dry_run_short_circuits_status_lookup(self, azure_config, logger):
        strategy = AzureProviderStrategy(config=azure_config, logger=logger, provider_instance_name="azure-default")
        strategy.initialize()

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["vm-1", "vm-2"]},
            context={"dry_run": True},
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["queried_count"] == 2
        assert [m["instance_id"] for m in result.data["instances"]] == ["vm-1", "vm-2"]
        assert result.metadata["method"] == "dry_run"
        assert result.metadata["provider_fulfilment"].state == "in_progress"

    def test_single_vm_provider_api_routes_status_via_handler(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[
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
        ])
        strategy_harness.handlers["SingleVM"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vm-1"],
                "provider_api": "SingleVM",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["method"] == "handler"
        handler.check_hosts_status_async.assert_awaited_once()
        assert result.data["instances"][0]["instance_id"] == "vm-1"

    def test_vmss_provider_api_routes_status_via_handler_with_resource_mapping(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[
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
        ])
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["3"],
                "provider_api": "VMSS",
                "request_metadata": {"resource_group": "test-rg"},
                "resource_mapping": {"3": ("vmss-demo", 2)},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["method"] == "handler"
        handler.check_hosts_status_async.assert_awaited_once()
        assert [m["instance_id"] for m in result.data["instances"]] == ["3"]

    def test_vmss_resource_mapping_routes_status_via_handler_with_provider_api(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[
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
        ])
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["3"],
                "provider_api": "VMSS",
                "request_metadata": {"resource_group": "test-rg"},
                "resource_mapping": {"3": ("vmss-demo", 2)},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["method"] == "handler"
        handler.check_hosts_status_async.assert_awaited_once()
        assert [m["instance_id"] for m in result.data["instances"]] == ["3"]

    def test_cyclecloud_status_handler_failure_surfaces_error(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(side_effect=CycleCloudConnectionError(
            "cyclecloud auth failed",
            url="https://cc.example.com",
        ))
        strategy_harness.handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["node-1"],
                "provider_api": "CycleCloud",
                "resource_id": "my-cluster",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "cyclecloud_url": "https://cc.example.com",
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "CycleCloudConnectionError"
        assert "cyclecloud auth failed" in result.error_message
        assert result.metadata["error_class"] == "CycleCloudConnectionError"

    def test_status_preserves_handler_identity_fields(self, strategy_harness):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[
            {
                "instance_id": "vm-1",
                "status": "running",
                "private_ip": "10.0.0.4",
                "public_ip": None,
                "instance_type": "Standard_D4s_v5",
                "subnet_id": (
                    "/subscriptions/sub/resourceGroups/test-rg/providers/"
                    "Microsoft.Network/virtualNetworks/test-vnet/subnets/default"
                ),
                "vpc_id": (
                    "/subscriptions/sub/resourceGroups/test-rg/providers/"
                    "Microsoft.Network/virtualNetworks/test-vnet"
                ),
                "provider_type": "azure",
                "provider_data": {"vm_name": "vm-1"},
            }
        ])
        strategy_harness.handlers["SingleVM"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vm-1"],
                "provider_api": "SingleVM",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        machine = result.data["instances"][0]
        assert machine["instance_id"] == "vm-1"
        assert machine["status"] == "running"
        assert machine["private_ip"] == "10.0.0.4"
        assert machine["instance_type"] == "Standard_D4s_v5"
        assert machine["subnet_id"].endswith("/subnets/default")
        assert machine["provider_data"]["vm_name"] == "vm-1"

    def test_status_query_without_provider_api_is_rejected(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["vm-1"],
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "MISSING_PROVIDER_API"

    def test_get_instance_status_accepts_enum_provider_api(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[
            {
                "instance_id": "3",
                "status": "running",
                "provider_type": "azure",
                "provider_data": {"vmss_instance_id": "3"},
            }
        ])
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["3"],
                "provider_api": AzureProviderApi.VMSS,
                "request_metadata": {"resource_group": "test-rg"},
                "resource_mapping": {"3": ("vmss-demo", 2)},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert [m["instance_id"] for m in result.data["instances"]] == ["3"]
        handler.check_hosts_status_async.assert_awaited_once()


# ---------------------------------------------------------------------------
# DESCRIBE_RESOURCE_INSTANCES (with missing resource_ids → error path)
# ---------------------------------------------------------------------------


class TestDescribeResourceInstances:
    def test_missing_resource_ids_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={},
        )
        result = run_operation(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_RESOURCE_IDS"

    def test_missing_provider_api_returns_error(self, strategy):
        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={"resource_ids": ["vmss-demo"]},
        )
        result = run_operation(strategy.execute_operation(op))
        assert not result.success
        assert result.error_code == "MISSING_PROVIDER_API"

    def test_describe_resource_instances_cleans_up_empty_vmss_when_requested_members_are_gone(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
        strategy_harness.handlers["VMSS"] = handler
        resource_manager = MagicMock()
        resource_manager.get_vmss_member_count_async = AsyncMock(return_value=0)
        strategy_harness.resource_manager = resource_manager
        compute_client = MagicMock()
        compute_client.virtual_machine_scale_sets.begin_delete = AsyncMock()
        azure_client = MagicMock()
        azure_client.get_async_compute_client = AsyncMock(return_value=compute_client)
        strategy_harness.azure_client = azure_client

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["vm-a"],
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        compute_client.virtual_machine_scale_sets.begin_delete.assert_awaited_once_with(
            resource_group_name="test-rg",
            vm_scale_set_name="vmss-demo",
        )
        assert result.metadata["termination_follow_up_pending"] is True
        assert result.metadata["termination_follow_up_details"] == [
            {
                "resource_group": "test-rg",
                "vmss_name": "vmss-demo",
                "machine_ids": ["vm-a"],
                "delete_vmss_when_empty": True,
                "member_delete_submitted": True,
                "delete_submitted": True,
                "delete_retry_pending": False,
            }
        ]

    def test_describe_resource_instances_clears_pending_cleanup_after_vmss_is_gone(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
        strategy_harness.handlers["VMSS"] = handler
        resource_manager = MagicMock()
        resource_manager.vmss_exists_async = AsyncMock(return_value=False)
        strategy_harness.resource_manager = resource_manager
        compute_client = MagicMock()
        compute_client.virtual_machine_scale_sets.begin_delete = AsyncMock()
        azure_client = MagicMock()
        azure_client.get_async_compute_client = AsyncMock(return_value=compute_client)
        strategy_harness.azure_client = azure_client

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["vm-a"],
                                "delete_vmss_when_empty": True,
                                "delete_submitted": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        resource_manager.vmss_exists_async.assert_awaited_once_with(
            resource_group="test-rg",
            vmss_name="vmss-demo",
        )
        compute_client.virtual_machine_scale_sets.begin_delete.assert_not_awaited()
        assert result.metadata["termination_follow_up_pending"] is False

    def test_describe_resource_instances_does_not_cleanup_when_strict_vmss_status_fails(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(side_effect=RuntimeError(
            "Failed to list instances for VMSS 'vmss-demo': transient ARM failure"
        ))
        strategy_harness.handlers["VMSS"] = handler
        strategy_harness.resource_manager = MagicMock()

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["vm-a"],
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.error_code == "DESCRIBE_RESOURCE_INSTANCES_ERROR"
        strategy_harness.resource_manager.get_vmss_member_count_async.assert_not_called()
        forwarded_request = handler.check_hosts_status_async.await_args.args[0]
        assert forwarded_request.metadata["raise_on_status_error"] is True

    def test_describe_resource_instances_leaves_cleanup_pending_when_other_members_remain(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
        strategy_harness.handlers["VMSS"] = handler
        resource_manager = MagicMock()
        resource_manager.get_vmss_member_count_async = AsyncMock(return_value=1)
        strategy_harness.resource_manager = resource_manager
        compute_client = MagicMock()
        compute_client.virtual_machine_scale_sets.begin_delete = AsyncMock()
        azure_client = MagicMock()
        azure_client.get_async_compute_client = AsyncMock(return_value=compute_client)
        strategy_harness.azure_client = azure_client

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-b"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-b",
                                "machine_ids": ["vm-b"],
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        resource_manager.get_vmss_member_count_async.assert_awaited_once_with(
            resource_group="test-rg",
            vmss_name="vmss-b",
        )
        assert result.metadata["termination_follow_up_pending"] is True
        assert result.metadata["termination_follow_up_details"] == [
            {
                "resource_group": "test-rg",
                "vmss_name": "vmss-b",
                "machine_ids": ["vm-b"],
                "delete_vmss_when_empty": True,
                "member_delete_submitted": True,
                "delete_submitted": False,
                "delete_retry_pending": False,
            }
        ]
        compute_client.virtual_machine_scale_sets.begin_delete.assert_not_awaited()

    def test_describe_resource_instances_leaves_cleanup_pending_when_member_count_is_unknown(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
        strategy_harness.handlers["VMSS"] = handler
        resource_manager = MagicMock()
        resource_manager.get_vmss_member_count_async = AsyncMock(return_value=None)
        strategy_harness.resource_manager = resource_manager
        compute_client = MagicMock()
        compute_client.virtual_machine_scale_sets.begin_delete = AsyncMock()
        azure_client = MagicMock()
        azure_client.get_async_compute_client = AsyncMock(return_value=compute_client)
        strategy_harness.azure_client = azure_client

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["vm-a"],
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        resource_manager.get_vmss_member_count_async.assert_awaited_once_with(
            resource_group="test-rg",
            vmss_name="vmss-demo",
        )
        assert result.metadata["termination_follow_up_pending"] is True
        assert result.metadata["termination_follow_up_details"] == [
            {
                "resource_group": "test-rg",
                "vmss_name": "vmss-demo",
                "machine_ids": ["vm-a"],
                "delete_vmss_when_empty": True,
                "member_delete_submitted": True,
                "delete_submitted": False,
                "delete_retry_pending": False,
            }
        ]
        compute_client.virtual_machine_scale_sets.begin_delete.assert_not_awaited()

    def test_describe_resource_instances_surfaces_retry_pending_when_vmss_delete_retry_fails(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
        strategy_harness.handlers["VMSS"] = handler
        resource_manager = MagicMock()
        resource_manager.get_vmss_member_count_async = AsyncMock(return_value=0)
        strategy_harness.resource_manager = resource_manager
        compute_client = MagicMock()
        compute_client.virtual_machine_scale_sets.begin_delete = AsyncMock(
            side_effect=RuntimeError("delete still blocked")
        )
        azure_client = MagicMock()
        azure_client.get_async_compute_client = AsyncMock(return_value=compute_client)
        strategy_harness.azure_client = azure_client

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["vm-a"],
                                "delete_vmss_when_empty": True,
                            }
                        }
                    ],
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["termination_follow_up_pending"] is True
        assert result.metadata["termination_follow_up_details"] == [
            {
                "resource_group": "test-rg",
                "vmss_name": "vmss-demo",
                "machine_ids": ["vm-a"],
                "delete_vmss_when_empty": True,
                "member_delete_submitted": True,
                "delete_submitted": False,
                "delete_retry_pending": True,
                "delete_retry_count": 1,
                "last_delete_error": "delete still blocked",
            }
        ]

    def test_describe_resource_instances_clears_pending_cleanup_when_no_delete_is_required(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
        strategy_harness.handlers["VMSS"] = handler
        resource_manager = MagicMock()
        strategy_harness.resource_manager = resource_manager
        azure_client = MagicMock()
        strategy_harness.azure_client = azure_client

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": "VMSS",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "termination_requests": [
                        {
                            "pending_resource_cleanup": {
                                "resource_group": "test-rg",
                                "vmss_name": "vmss-demo",
                                "machine_ids": ["vm-a"],
                                "delete_vmss_when_empty": False,
                            }
                        }
                    ],
                },
            },
        )

        first_result = run_operation(strategy.execute_operation(op))
        second_result = run_operation(strategy.execute_operation(op))

        assert first_result.success
        assert first_result.metadata["termination_follow_up_pending"] is False
        assert second_result.success
        assert second_result.metadata["termination_follow_up_pending"] is False
        resource_manager.get_vmss_member_count_async.assert_not_called()
        azure_client.get_async_compute_client.assert_not_called()

    def test_describe_resource_instances_forwards_cyclecloud_request_metadata(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        strategy_harness.handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["req-12345678-1234-1234-1234-123456789012"],
                "provider_api": "CycleCloud",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "cluster_name": "my-cluster",
                    "node_array": "execute",
                    "node_ids": ["node-1"],
                    "operation_id": "op-123",
                    "operation_location": "https://cc.example.com/operations/op-123",
                    "cyclecloud_url": "https://cc.example.com",
                    "cyclecloud_auth_mode": "bearer",
                    "cyclecloud_aad_scope": "https://cc.example.com/.default",
                    "cyclecloud_verify_ssl": False,
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        forwarded_request = handler.check_hosts_status_async.await_args.args[0]
        assert forwarded_request.resource_ids == ["req-12345678-1234-1234-1234-123456789012"]
        assert forwarded_request.metadata["cluster_name"] == "my-cluster"
        assert forwarded_request.metadata["node_array"] == "execute"
        assert forwarded_request.metadata["node_ids"] == ["node-1"]
        assert forwarded_request.metadata["operation_id"] == "op-123"
        assert (
            forwarded_request.metadata["operation_location"]
            == "https://cc.example.com/operations/op-123"
        )
        assert forwarded_request.metadata["cyclecloud_url"] == "https://cc.example.com"
        assert forwarded_request.metadata["cyclecloud_auth_mode"] == "bearer"
        assert (
            forwarded_request.metadata["cyclecloud_aad_scope"]
            == "https://cc.example.com/.default"
        )
        assert forwarded_request.metadata["cyclecloud_verify_ssl"] is False

    def test_get_instance_status_matches_cyclecloud_node_name_alias(self, strategy_harness):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[
            {
                "instance_id": "6ecc44d4-417d-41e4-a729-3d504d651fd3",
                "name": "dynamic-1",
                "status": "running",
                "provider_type": "azure",
                "provider_data": {
                    "cluster_name": "contoso-slurm-lab-cluster",
                    "node_id": "6ecc44d4-417d-41e4-a729-3d504d651fd3",
                    "node_name": "dynamic-1",
                },
            }
        ])
        strategy_harness.handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["dynamic-1"],
                "provider_api": "CycleCloud",
                "resource_id": "contoso-slurm-lab-cluster",
                "resource_mapping": {"dynamic-1": ("contoso-slurm-lab-cluster", 1)},
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "cluster_name": "contoso-slurm-lab-cluster",
                    "node_ids": ["dynamic-1"],
                    "cyclecloud_url": "https://cc.example.com",
                    "cyclecloud_auth_mode": "bearer",
                    "cyclecloud_aad_scope": "https://cc.example.com/.default",
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.data["queried_count"] == 1
        assert len(result.data["instances"]) == 1
        assert result.data["instances"][0]["name"] == "dynamic-1"

    def test_get_instance_status_uses_cyclecloud_cluster_name_when_resource_id_missing(
        self, strategy_harness
    ):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        strategy_harness.handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["dynamic-1"],
                "provider_api": "CycleCloud",
                "template_id": "tmpl-1",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "cluster_name": "contoso-slurm-lab-cluster",
                    "node_ids": ["dynamic-1"],
                    "cyclecloud_url": "https://cc.example.com",
                    "cyclecloud_auth_mode": "bearer",
                    "cyclecloud_aad_scope": "https://cc.example.com/.default",
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        forwarded_request = handler.check_hosts_status_async.await_args.args[0]
        assert forwarded_request.resource_ids == ["contoso-slurm-lab-cluster"]

    def test_get_instance_status_uses_cyclecloud_context_from_request_metadata(
        self, azure_config, logger
    ):
        strategy_harness = build_strategy_harness(
            config=azure_config,
            logger=logger,
            provider_instance_name="azure-default",
        )
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        strategy_harness.handlers["CycleCloud"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["dynamic-1"],
                "provider_api": "CycleCloud",
                "template_id": "tmpl-1",
                "request_id": "req-11111111-1111-4111-8111-111111111111",
                "request_metadata": {
                    "resource_group": "test-rg",
                    "cluster_name": "contoso-slurm-lab-cluster",
                    "cyclecloud_url": "https://cc.example.com",
                    "cyclecloud_auth_mode": "bearer",
                    "cyclecloud_aad_scope": "https://cc.example.com/.default",
                },
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        forwarded_request = handler.check_hosts_status_async.await_args.args[0]
        assert forwarded_request.resource_ids == ["contoso-slurm-lab-cluster"]
        assert forwarded_request.metadata["cluster_name"] == "contoso-slurm-lab-cluster"

    def test_dry_run_short_circuits_resource_discovery(self, azure_config, logger):
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock()
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-test"],
                "provider_api": "VMSS",
            },
            context={"dry_run": True},
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["method"] == "dry_run"
        handler.check_hosts_status_async.assert_not_awaited()

    def test_describe_resource_instances_accepts_enum_provider_api(self, strategy_harness):
        strategy = strategy_harness.strategy
        handler = MagicMock()
        handler.check_hosts_status_async = AsyncMock(return_value=[])
        handler.get_vmss_resource_errors_async = AsyncMock(return_value=[])
        strategy_harness.handlers["VMSS"] = handler
        strategy_harness.resource_manager = MagicMock()

        op = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={
                "resource_ids": ["vmss-demo"],
                "provider_api": AzureProviderApi.VMSS,
                "template_id": "tmpl-1",
                "request_metadata": {"resource_group": "test-rg"},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert result.success
        assert result.metadata["provider_api"] == "VMSS"
        handler.check_hosts_status_async.assert_awaited_once()


# ---------------------------------------------------------------------------
# Provider naming
# ---------------------------------------------------------------------------
