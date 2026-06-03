"""Unit tests for OCI strategy compute operation routing and contracts."""

from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


def _make_strategy(initialized: bool = True) -> OCIProviderStrategy:
    strategy = OCIProviderStrategy(
        config=OCIProviderConfig(region="us-phoenix-1", profile="DEFAULT"),
        logger=MagicMock(),
    )
    if initialized:
        strategy.initialize()
    return strategy


@pytest.mark.asyncio
async def test_create_instances_success() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "template_id": "tpl-oci",
            "count": 2,
            "image_id": "ocid1.image.oc1..img",
            "instance_type": "VM.Standard.E4.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["status"] == "accepted"
    assert len(result.data["instance_ids"]) == 2
    assert len(result.data["launch_requests"]) == 2


@pytest.mark.asyncio
async def test_create_instances_requires_template_id() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={"count": 1},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is False
    assert result.error_code == "MISSING_TEMPLATE_ID"


@pytest.mark.asyncio
async def test_create_instances_requires_oci_template_fields() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={"template_id": "tpl-oci", "count": 1},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is False
    assert result.error_code == "MISSING_REQUIRED_FIELDS"
    assert "image_id" in result.metadata["missing_fields"]


@pytest.mark.asyncio
async def test_create_instances_supports_nested_template_payload() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "count": 1,
            "template": {
                "template_id": "tpl-oci-nested",
                "image_id": "ocid1.image.oc1..img",
                "instance_type": "VM.Standard.E4.Flex",
                "subnet_ids": ["ocid1.subnet.oc1..subnet"],
                "compartment_id": "ocid1.compartment.oc1..compartment",
            },
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["status"] == "accepted"
    assert result.data["launch_requests"][0]["source_details"]["image_id"] == "ocid1.image.oc1..img"


@pytest.mark.asyncio
async def test_create_instances_supports_template_config_payload() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "count": 1,
            "template_config": {
                "template_id": "tpl-oci-scheduler",
                "image_id": "ocid1.image.oc1..img",
                "instance_type": "VM.Standard.E4.Flex",
                "subnet_ids": ["ocid1.subnet.oc1..subnet"],
                "compartment_id": "ocid1.compartment.oc1..compartment",
            },
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["status"] == "accepted"
    assert result.data["launch_requests"][0]["source_details"]["image_id"] == "ocid1.image.oc1..img"


@pytest.mark.asyncio
async def test_create_instances_supports_template_config_with_nested_configuration() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "count": 1,
            "template_config": {
                "template_id": "tpl-oci-scheduler-nested",
                "image_id": "ocid1.image.oc1..img",
                "configuration": {
                    "instance_type": "VM.Standard.E4.Flex",
                    "subnet_ids": ["ocid1.subnet.oc1..subnet"],
                    "compartment_id": "ocid1.compartment.oc1..compartment",
                },
            },
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["status"] == "accepted"
    assert result.data["launch_requests"][0]["shape"] == "VM.Standard.E4.Flex"
    assert (
        result.data["launch_requests"][0]["compartment_id"]
        == "ocid1.compartment.oc1..compartment"
    )


@pytest.mark.asyncio
async def test_create_instances_supports_flex_runtime_inputs_and_pricing() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "template_id": "tpl-oci-flex",
            "count": 1,
            "image_id": "ocid1.image.oc1..img",
            "instance_type": "VM.Standard.E6.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "ocpus": 2,
            "memory_gbs": 16,
            "boot_volume_gbs": 100,
            "capacity_type": "preemptible",
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["launch_requests"][0]["shape_config"]["ocpus"] == 2
    assert result.data["launch_requests"][0]["shape_config"]["memoryInGBs"] == 16
    assert result.data["capacity_type"] == "preemptible"
    assert result.data["pricing_estimate"]["total_hourly"] > 0


@pytest.mark.asyncio
async def test_terminate_instances_requires_machine_ids() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.TERMINATE_INSTANCES,
        parameters={},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is False
    assert result.error_code == "MISSING_MACHINE_IDS"


@pytest.mark.asyncio
async def test_get_instance_status_success() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
        parameters={"machine_ids": ["ocid1.instance.oc1..abc"]},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert "ocid1.instance.oc1..abc" in result.data["instances"]


@pytest.mark.asyncio
async def test_validate_template_reports_bm_flex_constraint() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
        parameters={
            "template_id": "tpl-bm",
            "image_id": "ocid1.image.oc1..img",
            "shape": "BM.Standard.E5.192",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "ocpus": 64,
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["valid"] is False
    assert "bm_shape_does_not_support_flex_sizing" in result.data["errors"]


@pytest.mark.asyncio
async def test_create_instances_live_cli_path_parses_real_ocid() -> None:
    strategy = _make_strategy()
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    handler._force_live_cli_for_tests = True
    handler._run_oci = MagicMock(
        return_value={
            "data": {
                "id": "ocid1.instance.oc1..realinstanceid",
                "shape": "VM.Standard.E4.Flex",
                "lifecycle-state": "PROVISIONING",
            }
        }
    )

    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "template_id": "tpl-oci",
            "count": 1,
            "image_id": "ocid1.image.oc1..img",
            "instance_type": "VM.Standard.E4.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "availability_domain": "kIdk:PHX-AD-1",
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["instance_ids"] == ["ocid1.instance.oc1..realinstanceid"]
    assert result.data["resource_ids"] == ["ocid1.instance.oc1..realinstanceid"]
    assert result.data["effective_capacity_type"] == "ondemand"


@pytest.mark.asyncio
async def test_create_instances_live_cli_uses_preemptible_flag() -> None:
    strategy = _make_strategy()
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    handler._force_live_cli_for_tests = True

    observed_args = []

    def _mock_run_oci(args, payload=None, region_override=None):
        observed_args.append(list(args))
        return {
            "data": {
                "id": "ocid1.instance.oc1..realpreemptible",
                "shape": "VM.Standard.E6.Flex",
                "lifecycle-state": "PROVISIONING",
            }
        }

    handler._run_oci = MagicMock(side_effect=_mock_run_oci)

    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "template_id": "tpl-oci-preemptible",
            "count": 1,
            "image_id": "ocid1.image.oc1..img",
            "instance_type": "VM.Standard.E6.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "availability_domain": "kIdk:FRA-AD-1",
            "capacity_type": "preemptible",
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    launch_call = observed_args[0]
    assert "--preemptible-instance-config" in launch_call
    assert result.data["capacity_type"] == "preemptible"
    assert result.data["effective_capacity_type"] == "preemptible"
    assert result.data["fallback_attempted"] is False


@pytest.mark.asyncio
async def test_create_instances_preemptible_fallback_to_ondemand() -> None:
    strategy = _make_strategy()
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    handler._force_live_cli_for_tests = True

    call_count = {"n": 0}
    observed_args = []

    def _mock_run_oci(args, payload=None, region_override=None):
        observed_args.append(list(args))
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Insufficient capacity for preemptible launch")
        return {
            "data": {
                "id": "ocid1.instance.oc1..fallbackondemand",
                "shape": "VM.Standard.E6.Flex",
                "lifecycle-state": "PROVISIONING",
            }
        }

    handler._run_oci = MagicMock(side_effect=_mock_run_oci)

    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "template_id": "tpl-oci-preemptible-fallback",
            "count": 1,
            "image_id": "ocid1.image.oc1..img",
            "instance_type": "VM.Standard.E6.Flex",
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "availability_domain": "kIdk:FRA-AD-1",
            "capacity_type": "preemptible",
            "fallback_to_ondemand": True,
        },
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert len(observed_args) == 2
    assert "--preemptible-instance-config" in observed_args[0]
    assert "--preemptible-instance-config" not in observed_args[1]
    assert result.data["effective_capacity_type"] == "ondemand"
    assert result.data["fallback_attempted"] is True


@pytest.mark.asyncio
async def test_get_instance_status_supports_instance_ids_live_cli() -> None:
    strategy = _make_strategy()
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    handler._force_live_cli_for_tests = True
    handler._run_oci = MagicMock(
        return_value={
            "data": {
                "id": "ocid1.instance.oc1..realinstanceid",
                "lifecycle-state": "RUNNING",
            }
        }
    )

    operation = ProviderOperation(
        operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
        parameters={"instance_ids": ["ocid1.instance.oc1..realinstanceid"]},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["instances"]["ocid1.instance.oc1..realinstanceid"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_terminate_instances_supports_instance_ids_live_cli() -> None:
    strategy = _make_strategy()
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    handler._force_live_cli_for_tests = True
    handler._run_oci = MagicMock(return_value={"data": {}})

    operation = ProviderOperation(
        operation_type=ProviderOperationType.TERMINATE_INSTANCES,
        parameters={"instance_ids": ["ocid1.instance.oc1..realinstanceid"]},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["terminated_machine_ids"] == ["ocid1.instance.oc1..realinstanceid"]


@pytest.mark.asyncio
async def test_describe_resource_instances_requires_resource_ids() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is False
    assert result.error_code == "MISSING_RESOURCE_IDS"


@pytest.mark.asyncio
async def test_describe_resource_instances_success() -> None:
    strategy = _make_strategy()
    operation = ProviderOperation(
        operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
        parameters={"resource_ids": ["ocid1.instancepool.oc1..pool1"]},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is True
    assert result.data["instances"][0]["resource_id"] == "ocid1.instancepool.oc1..pool1"


@pytest.mark.asyncio
async def test_execute_operation_requires_initialize() -> None:
    strategy = _make_strategy(initialized=False)
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={"template_id": "tpl-oci", "count": 1},
    )

    result = await strategy.execute_operation(operation)

    assert result.success is False
    assert result.error_code == "NOT_INITIALIZED"
