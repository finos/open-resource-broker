"""Preflight gates a batch without changing OCI launch lifecycle contracts."""

import copy
import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orb.infrastructure.mocking.dry_run_context import dry_run_context
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.cli_client import OCICommandError
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

AD = "test:PHX-AD-1"
COMPARTMENT = "ocid1.compartment.oc1..example"


@pytest.fixture
def template():
    return {
        "template_id": "oci-preflight-test",
        "image_id": "ocid1.image.oc1.phx.example",
        "subnet_ids": ["ocid1.subnet.oc1.phx.example"],
        "compartment_id": COMPARTMENT,
        "availability_domain": AD,
        "machine_types": {"VM.Standard.E5.Flex": 1},
        "shape_config": {"ocpus": 4, "memoryInGBs": 32},
        "user_data": "private-bootstrap-marker",
        "ssh_authorized_keys": "private-key-marker",
    }


class FakeOCI:
    def __init__(self):
        self.calls = []
        self.launches = []
        self.image_state = "AVAILABLE"
        self.cluster_state = "ACTIVE"
        self.read_error = None
        self.launch_errors = {}

    def __call__(self, args, **kwargs):
        self.calls.append(copy.deepcopy(args))
        command = args[:3]
        if command == ["compute", "image", "get"]:
            if self.read_error:
                raise self.read_error
            return {
                "data": {
                    "id": args[args.index("--image-id") + 1],
                    "lifecycle-state": self.image_state,
                }
            }
        if command == ["network", "subnet", "get"]:
            return {
                "data": {
                    "id": args[args.index("--subnet-id") + 1],
                    "lifecycle-state": "AVAILABLE",
                    "availability-domain": None,
                }
            }
        if command == ["iam", "availability-domain", "list"]:
            return {"data": [{"name": AD}]}
        if command == ["compute", "shape", "list"]:
            shape = args[args.index("--shape") + 1]
            return {
                "data": [
                    {
                        "shape": shape,
                        "is-flexible": "Flex" in shape,
                        "ocpus": 36,
                        "memory-in-gbs": 512,
                        "ocpu-options": {"min": 1, "max": 96},
                        "memory-options": {
                            "min-in-g-bs": 1,
                            "max-in-g-bs": 1536,
                            "min-per-ocpu-in-gbs": 1,
                            "max-per-ocpu-in-gbs": 64,
                        },
                    }
                ]
            }
        if command == ["compute", "image-shape-compatibility-entry", "get"]:
            return {
                "data": {
                    "image-id": args[args.index("--image-id") + 1],
                    "shape": args[args.index("--shape-name") + 1],
                }
            }
        if command == ["compute", "compute-cluster", "get"]:
            return {
                "data": {
                    "lifecycle-state": self.cluster_state,
                    "compartment-id": COMPARTMENT,
                    "availability-domain": AD,
                }
            }
        if args[0] == "raw-request":
            self.launches.append(copy.deepcopy(kwargs["payload"]))
            if error := self.launch_errors.get(len(self.launches)):
                raise error
            return {
                "data": {"id": f"instance-{len(self.launches)}", "lifecycleState": "PROVISIONING"}
            }
        if command == ["compute", "instance", "terminate"]:
            return {"data": {}}
        raise AssertionError(f"Unexpected OCI command: {args}")


def make_strategy(runner, *, enabled=True):
    config = OCIProviderConfig(credential_source="instance_principal", launch_preflight=enabled)
    strategy = OCIProviderStrategy(config, MagicMock())
    assert strategy.initialize()
    strategy._compute_handler._run_oci = MagicMock(side_effect=runner)
    return strategy


def acquire(template, count=1, *, context=None):
    return ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={"template_config": template, "count": count, "request_id": "request-test"},
        context=context,
    )


def test_preflight_is_opt_in_and_configuration_rejects_invalid_booleans(monkeypatch):
    monkeypatch.delenv("ORB_OCI_LAUNCH_PREFLIGHT", raising=False)
    assert OCIProviderConfig().launch_preflight is False
    monkeypatch.setenv("ORB_OCI_LAUNCH_PREFLIGHT", "true")
    assert OCIProviderConfig().launch_preflight is True
    with pytest.raises(ValidationError):
        OCIProviderConfig(launch_preflight="sometimes")


@pytest.mark.asyncio
async def test_disabled_preflight_keeps_the_existing_launch_calls(template):
    runner = FakeOCI()
    result = await make_strategy(runner, enabled=False).execute_operation(acquire(template, 2))
    assert result.success, result.error_message
    assert [call[0] for call in runner.calls] == ["raw-request", "raw-request"]


@pytest.mark.asyncio
async def test_valid_batch_reads_once_and_preserves_sizes_tags_and_retry_tokens(template):
    runner = FakeOCI()
    original = copy.deepcopy(template)
    strategy = make_strategy(runner)
    result = await strategy.execute_operation(acquire(template, 2))
    assert result.success, result.error_message
    assert result.data["instance_ids"] == ["instance-1", "instance-2"]
    assert len(runner.calls) == 6
    assert all(call[0] != "raw-request" for call in runner.calls[:4])
    assert all(body["shapeConfig"] == {"ocpus": 4, "memoryInGBs": 32} for body in runner.launches)
    assert all(body["availabilityDomain"] == AD for body in runner.launches)
    assert all(body["freeformTags"]["orb_request_id"] == "request-test" for body in runner.launches)
    raw_calls = [call for call in runner.calls if call[0] == "raw-request"]
    tokens = [
        json.loads(call[call.index("--request-headers") + 1])["opc-retry-token"]
        for call in raw_calls
    ]
    assert len(set(tokens)) == 2
    assert template == original
    serialized = result.model_dump_json()
    assert "private-bootstrap-marker" not in serialized
    assert "private-key-marker" not in serialized
    assert "launch_requests" not in result.data


@pytest.mark.asyncio
async def test_resolved_ad_is_reused_for_every_worker_without_repeated_subnet_reads(template):
    template.pop("availability_domain")
    runner = FakeOCI()
    result = await make_strategy(runner).execute_operation(acquire(template, 3))
    assert result.success, result.error_message
    assert all(body["availabilityDomain"] == AD for body in runner.launches)
    assert sum(call[:3] == ["network", "subnet", "get"] for call in runner.calls) == 1
    assert sum(call[:3] == ["iam", "availability-domain", "list"] for call in runner.calls) == 1


@pytest.mark.asyncio
async def test_invalid_image_submits_no_launch_or_rollback(template):
    runner = FakeOCI()
    runner.image_state = "DISABLED"
    result = await make_strategy(runner).execute_operation(acquire(template, 2))
    assert not result.success
    assert result.error_code == "OCI_PREFLIGHT_REJECTED"
    assert runner.launches == []
    assert len(runner.calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
@pytest.mark.asyncio
async def test_unavailable_reads_never_launch_or_leak_diagnostics(template, status):
    runner = FakeOCI()
    runner.read_error = OCICommandError("private-bootstrap-marker", status=status)
    result = await make_strategy(runner).execute_operation(acquire(template))
    assert not result.success
    assert result.error_code == "OCI_PREFLIGHT_UNAVAILABLE"
    assert runner.launches == []
    assert "private-bootstrap-marker" not in result.model_dump_json()


@pytest.mark.parametrize("context", [{"dry_run": True}, {"mock": True}, {"allow_mock": True}])
@pytest.mark.asyncio
async def test_explicit_simulation_stays_offline_when_preflight_enabled(template, context):
    runner = FakeOCI()
    result = await make_strategy(runner).execute_operation(acquire(template, context=context))
    assert result.success, result.error_message
    assert runner.calls == []


@pytest.mark.asyncio
async def test_global_dry_run_and_template_validation_make_no_preflight_reads(template):
    runner = FakeOCI()
    strategy = make_strategy(runner)
    with dry_run_context():
        result = await strategy.execute_operation(acquire(template))
    assert result.success, result.error_message
    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
            parameters={"template_config": template},
        )
    )
    assert result.success and result.data["valid"]
    assert runner.calls == []


@pytest.mark.asyncio
async def test_preflight_preserves_definite_capacity_fallback_without_rechecking(template):
    template.update(capacity_type="preemptible", fallback_to_ondemand=True)
    runner = FakeOCI()
    runner.launch_errors[1] = OCICommandError(
        "Capacity unavailable", code="OutOfCapacity", status=409
    )
    result = await make_strategy(runner).execute_operation(acquire(template))
    assert result.success, result.error_message
    assert result.data["fallback_attempted"] is True
    assert result.data["effective_capacity_type"] == "ondemand"
    assert "preemptibleInstanceConfig" in runner.launches[0]
    assert "preemptibleInstanceConfig" not in runner.launches[1]
    assert sum(call[:3] == ["compute", "shape", "list"] for call in runner.calls) == 1


@pytest.mark.asyncio
async def test_partial_launch_failure_still_requests_rollback(template):
    runner = FakeOCI()
    runner.launch_errors[2] = OCICommandError(
        "Launch rejected", code="InvalidParameter", status=400
    )
    result = await make_strategy(runner).execute_operation(acquire(template, 2))
    assert not result.success
    assert result.error_code == "OCI_LAUNCH_FAILED"
    assert result.metadata["rollback_requested_instance_ids"] == ["instance-1"]
    assert sum(call[:3] == ["compute", "instance", "terminate"] for call in runner.calls) == 1


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.asyncio
async def test_bare_metal_preflight_keeps_existing_cluster_validation(template, active):
    template["machine_types"] = {"BM.Optimized3.36": 1}
    template.pop("shape_config")
    template["compute_cluster_id"] = "ocid1.computecluster.oc1.phx.example"
    runner = FakeOCI()
    runner.cluster_state = "ACTIVE" if active else "DELETED"
    result = await make_strategy(runner).execute_operation(acquire(template))
    assert result.success is active
    if active:
        assert runner.launches[0]["computeClusterId"] == template["compute_cluster_id"]
        assert "shapeConfig" not in runner.launches[0]
    else:
        assert result.error_code == "OCI_COMPUTE_CLUSTER_INVALID"
        assert runner.launches == []
