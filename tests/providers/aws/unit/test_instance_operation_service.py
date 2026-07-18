"""Unit tests for AWSInstanceOperationService — terminate, status, start, stop."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
from orb.providers.base.strategy.provider_strategy import ProviderOperation, ProviderOperationType

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(provisioning_adapter=True) -> AWSInstanceOperationService:
    aws_client = MagicMock()
    logger = MagicMock()
    prov_adapter = MagicMock() if provisioning_adapter else None
    machine_adapter = MagicMock()
    return AWSInstanceOperationService(
        aws_client=aws_client,
        logger=logger,
        provisioning_adapter=prov_adapter,
        machine_adapter=machine_adapter,
        provider_name="test-provider",
        provider_type="aws",
    )


def _op(op_type: ProviderOperationType, params: dict) -> ProviderOperation:
    return ProviderOperation(operation_type=op_type, parameters=params)


# ---------------------------------------------------------------------------
# terminate_instances
# ---------------------------------------------------------------------------


class TestTerminateInstances:
    def test_returns_error_when_no_instance_ids(self):
        svc = _make_service()
        op = _op(ProviderOperationType.TERMINATE_INSTANCES, {"instance_ids": []})
        result = svc.terminate_instances(op)
        assert not result.success
        assert "required" in result.error_message.lower()

    def test_success_via_provisioning_adapter(self):
        svc = _make_service()
        svc._provisioning_adapter.release_resources.return_value = None
        op = _op(
            ProviderOperationType.TERMINATE_INSTANCES,
            {"instance_ids": ["i-001", "i-002"], "provider_api": "RunInstances"},
        )
        result = svc.terminate_instances(op)
        assert result.success
        assert result.data["terminated_count"] == 2

    def test_fleet_resource_failure_not_falling_back(self):
        svc = _make_service()
        svc._provisioning_adapter.release_resources.side_effect = RuntimeError("fleet err")
        op = _op(
            ProviderOperationType.TERMINATE_INSTANCES,
            {
                "instance_ids": ["i-001"],
                "provider_api": "EC2Fleet",
            },
        )
        result = svc.terminate_instances(op)
        assert not result.success
        assert "EC2Fleet" in result.error_message or "FLEET" in (result.error_code or "")

    def test_run_instances_failure_falls_back_to_direct_termination(self):
        svc = _make_service()
        svc._provisioning_adapter.release_resources.side_effect = RuntimeError("prov err")
        svc._aws_client.ec2_client.terminate_instances.return_value = {
            "TerminatingInstances": [{"InstanceId": "i-001"}]
        }
        op = _op(
            ProviderOperationType.TERMINATE_INSTANCES,
            {"instance_ids": ["i-001"], "provider_api": "RunInstances"},
        )
        result = svc.terminate_instances(op)
        assert result.success
        assert result.data["terminated_count"] == 1

    def test_returns_error_on_unexpected_exception(self):
        svc = _make_service()
        svc._provisioning_adapter.release_resources.side_effect = Exception("boom")
        # Set a fleet-based provider so it fails quickly
        svc._aws_client.ec2_client.terminate_instances.side_effect = Exception("also boom")
        op = _op(
            ProviderOperationType.TERMINATE_INSTANCES,
            {"instance_ids": ["i-001"], "provider_api": "EC2Fleet"},
        )
        result = svc.terminate_instances(op)
        assert not result.success


# ---------------------------------------------------------------------------
# get_instance_status
# ---------------------------------------------------------------------------


class TestGetInstanceStatus:
    def test_returns_error_when_no_instance_ids(self):
        svc = _make_service()
        op = _op(ProviderOperationType.GET_INSTANCE_STATUS, {"instance_ids": []})
        result = svc.get_instance_status(op)
        assert not result.success
        assert result.error_code == "MISSING_INSTANCE_IDS"

    def test_returns_instances_list_on_success(self):
        svc = _make_service()
        svc._aws_client.ec2_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "instance_id": "i-001",
                            "status": "running",
                        }
                    ]
                }
            ]
        }
        # machine_adapter.create_machine_from_aws_instance is a mock
        svc._machine_adapter.create_machine_from_aws_instance.return_value = {
            "instance_id": "i-001"
        }
        op = _op(
            ProviderOperationType.GET_INSTANCE_STATUS,
            {"instance_ids": ["i-001"], "provider_api": "RunInstances"},
        )
        result = svc.get_instance_status(op)
        assert result.success
        assert result.data["queried_count"] == 1
        assert len(result.data["instances"]) == 1

    def test_returns_error_on_aws_exception(self):
        svc = _make_service()
        svc._aws_client.ec2_client.describe_instances.side_effect = RuntimeError("aws err")
        op = _op(ProviderOperationType.GET_INSTANCE_STATUS, {"instance_ids": ["i-001"]})
        result = svc.get_instance_status(op)
        assert not result.success
        assert result.error_code == "GET_INSTANCE_STATUS_ERROR"


# ---------------------------------------------------------------------------
# start_instances
# ---------------------------------------------------------------------------


class TestStartInstances:
    def test_returns_error_when_no_instance_ids(self):
        svc = _make_service()
        op = _op(ProviderOperationType.START_INSTANCES, {"instance_ids": []})
        result = svc.start_instances(op)
        assert not result.success
        assert result.error_code == "MISSING_INSTANCE_IDS"

    def test_returns_success_with_results_mapping(self):
        svc = _make_service()
        svc._aws_client.ec2_client.start_instances.return_value = {
            "StartingInstances": [{"InstanceId": "i-001", "CurrentState": {"Name": "pending"}}]
        }
        op = _op(ProviderOperationType.START_INSTANCES, {"instance_ids": ["i-001"]})
        result = svc.start_instances(op)
        assert result.success
        assert result.data["results"]["i-001"] is True

    def test_running_state_is_also_success(self):
        svc = _make_service()
        svc._aws_client.ec2_client.start_instances.return_value = {
            "StartingInstances": [{"InstanceId": "i-002", "CurrentState": {"Name": "running"}}]
        }
        op = _op(ProviderOperationType.START_INSTANCES, {"instance_ids": ["i-002"]})
        result = svc.start_instances(op)
        assert result.data["results"]["i-002"] is True

    def test_returns_false_for_unexpected_state(self):
        svc = _make_service()
        svc._aws_client.ec2_client.start_instances.return_value = {
            "StartingInstances": [{"InstanceId": "i-003", "CurrentState": {"Name": "terminated"}}]
        }
        op = _op(ProviderOperationType.START_INSTANCES, {"instance_ids": ["i-003"]})
        result = svc.start_instances(op)
        assert result.data["results"]["i-003"] is False

    def test_returns_error_on_exception(self):
        svc = _make_service()
        svc._aws_client.ec2_client.start_instances.side_effect = RuntimeError("aws err")
        op = _op(ProviderOperationType.START_INSTANCES, {"instance_ids": ["i-001"]})
        result = svc.start_instances(op)
        assert not result.success
        assert result.error_code == "START_INSTANCES_ERROR"


# ---------------------------------------------------------------------------
# stop_instances
# ---------------------------------------------------------------------------


class TestStopInstances:
    def test_returns_error_when_no_instance_ids(self):
        svc = _make_service()
        op = _op(ProviderOperationType.STOP_INSTANCES, {"instance_ids": []})
        result = svc.stop_instances(op)
        assert not result.success
        assert result.error_code == "MISSING_INSTANCE_IDS"

    def test_returns_success_with_results_mapping_stopping_state(self):
        svc = _make_service()
        svc._aws_client.ec2_client.stop_instances.return_value = {
            "StoppingInstances": [{"InstanceId": "i-001", "CurrentState": {"Name": "stopping"}}]
        }
        op = _op(ProviderOperationType.STOP_INSTANCES, {"instance_ids": ["i-001"]})
        result = svc.stop_instances(op)
        assert result.success
        assert result.data["results"]["i-001"] is True

    def test_stopped_state_is_also_success(self):
        svc = _make_service()
        svc._aws_client.ec2_client.stop_instances.return_value = {
            "StoppingInstances": [{"InstanceId": "i-002", "CurrentState": {"Name": "stopped"}}]
        }
        op = _op(ProviderOperationType.STOP_INSTANCES, {"instance_ids": ["i-002"]})
        result = svc.stop_instances(op)
        assert result.data["results"]["i-002"] is True

    def test_returns_false_for_unexpected_state(self):
        svc = _make_service()
        svc._aws_client.ec2_client.stop_instances.return_value = {
            "StoppingInstances": [{"InstanceId": "i-003", "CurrentState": {"Name": "terminated"}}]
        }
        op = _op(ProviderOperationType.STOP_INSTANCES, {"instance_ids": ["i-003"]})
        result = svc.stop_instances(op)
        assert result.data["results"]["i-003"] is False

    def test_returns_error_on_exception(self):
        svc = _make_service()
        svc._aws_client.ec2_client.stop_instances.side_effect = RuntimeError("aws err")
        op = _op(ProviderOperationType.STOP_INSTANCES, {"instance_ids": ["i-001"]})
        result = svc.stop_instances(op)
        assert not result.success
        assert result.error_code == "STOP_INSTANCES_ERROR"


# ---------------------------------------------------------------------------
# create_instances — async
# ---------------------------------------------------------------------------


class TestCreateInstances:
    def test_returns_error_when_no_template_config(self):
        svc = _make_service()
        op = _op(ProviderOperationType.CREATE_INSTANCES, {})
        result = asyncio.run(svc.create_instances(op, {}))
        assert not result.success
        assert result.error_code == "MISSING_TEMPLATE_CONFIG"

    def test_returns_error_when_no_handler_found(self):
        svc = _make_service()
        op = _op(
            ProviderOperationType.CREATE_INSTANCES,
            {
                "template_config": {
                    "provider_api": "SomeUnknownAPI",
                    "template_id": "tmpl-1",
                    "image_id": "ami-xxx",
                    "instance_type": "t2.micro",
                    "subnet_ids": [],
                    "security_group_ids": [],
                }
            },
        )
        result = asyncio.run(svc.create_instances(op, {}))
        assert not result.success
        assert result.error_code == "HANDLER_NOT_FOUND"

    def test_success_via_provisioning_adapter(self):
        svc = _make_service()
        svc._provisioning_adapter.provision_resources = AsyncMock(
            return_value={
                "resource_ids": ["fleet-001"],
                "instances": [{"instance_id": "i-001"}],
                "provider_data": {"method": "ec2fleet"},
            }
        )
        mock_handler = MagicMock()
        op = _op(
            ProviderOperationType.CREATE_INSTANCES,
            {
                "template_config": {
                    "provider_api": "EC2Fleet",
                    "template_id": "tmpl-1",
                    "image_id": "ami-xxx",
                    "instance_type": "t2.micro",
                    "subnet_ids": ["subnet-001"],
                    "security_group_ids": [],
                },
                "count": 1,
            },
        )
        result = asyncio.run(svc.create_instances(op, {"EC2Fleet": mock_handler}))
        assert result.success
        assert result.data["provider_api"] == "EC2Fleet"

    def test_metadata_fields_extracted_from_metadata_block(self):
        svc = _make_service()
        svc._provisioning_adapter.provision_resources = AsyncMock(
            return_value={
                "resource_ids": [],
                "instances": [],
                "provider_data": {},
            }
        )
        mock_handler = MagicMock()
        op = _op(
            ProviderOperationType.CREATE_INSTANCES,
            {
                "template_config": {
                    "provider_api": "RunInstances",
                    "template_id": "tmpl-2",
                    "image_id": "ami-xxx",
                    "instance_type": "t2.micro",
                    "subnet_ids": [],
                    "security_group_ids": [],
                    "metadata": {
                        "key_name": "my-key",
                        "user_data": "#!/bin/bash",
                    },
                }
            },
        )
        result = asyncio.run(svc.create_instances(op, {"RunInstances": mock_handler}))
        assert result.success

    def test_returns_error_on_provision_failure(self):
        svc = _make_service()
        svc._provisioning_adapter.provision_resources = AsyncMock(
            side_effect=RuntimeError("prov fail")
        )
        mock_handler = MagicMock()
        op = _op(
            ProviderOperationType.CREATE_INSTANCES,
            {
                "template_config": {
                    "provider_api": "EC2Fleet",
                    "template_id": "tmpl-1",
                    "image_id": "ami-xxx",
                    "instance_type": "t2.micro",
                    "subnet_ids": [],
                    "security_group_ids": [],
                }
            },
        )
        result = asyncio.run(svc.create_instances(op, {"EC2Fleet": mock_handler}))
        assert not result.success
        assert result.error_code == "CREATE_INSTANCES_ERROR"
