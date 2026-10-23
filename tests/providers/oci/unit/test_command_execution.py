"""OCI execution is explicit and never exposes launch data in failures."""

import json
import subprocess
import traceback
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.mocking.dry_run_context import dry_run_context
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler


def make_operation(operation_type=ProviderOperationType.CREATE_INSTANCES, context=None):
    return ProviderOperation(
        operation_type=operation_type,
        parameters={
            "template_id": "execution-test",
            "count": 1,
            "image_id": "image",
            "instance_type": "VM.Standard.E5.Flex",
            "subnet_ids": ["subnet"],
            "compartment_id": "compartment",
            "availability_domain": "AD-1",
            "machine_ids": ["instance"],
            "resource_ids": ["instance"],
        },
        context=context or {},
    )


@pytest.mark.parametrize("inject_runner", [True, False])
@pytest.mark.asyncio
async def test_pytest_environment_does_not_suppress_command_execution(monkeypatch, inject_runner):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "outer-harness")
    monkeypatch.setattr(
        "orb.providers.oci.handlers.oci_compute_handler.shutil.which", lambda _: "oci"
    )
    runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            '{"status": "200 OK", "data": {"id": "actual-runner-result", "lifecycleState": "PROVISIONING"}}',
        )
    )
    monkeypatch.setattr("orb.providers.oci.handlers.oci_compute_handler.subprocess.run", runner)
    handler = OCIComputeHandler(
        MagicMock(), "us-phoenix-1", **({"run_command": runner} if inject_runner else {})
    )

    result = await handler.create_instances(make_operation())

    assert result.success
    assert result.data["instance_ids"] == ["actual-runner-result"]
    runner.assert_called_once()
    assert runner.call_args.args[0][1] == "raw-request"


@pytest.mark.parametrize(
    "operation_type",
    [
        ProviderOperationType.CREATE_INSTANCES,
        ProviderOperationType.GET_INSTANCE_STATUS,
        ProviderOperationType.TERMINATE_INSTANCES,
        ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
    ],
)
@pytest.mark.parametrize("mode", ["dry_run", "mock", "allow_mock", "global_dry_run"])
@pytest.mark.asyncio
async def test_explicit_simulation_never_calls_runner(operation_type, mode):
    runner = MagicMock(side_effect=AssertionError("simulation executed a command"))
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=runner)
    operation = make_operation(operation_type, {mode: True})

    with dry_run_context(mode == "global_dry_run"):
        if operation_type == ProviderOperationType.CREATE_INSTANCES:
            result = await handler.create_instances(operation)
        elif operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
            result = handler.get_instance_status(operation)
        elif operation_type == ProviderOperationType.TERMINATE_INSTANCES:
            result = handler.terminate_instances(operation)
        else:
            result = await handler.describe_resource_instances(operation)

    assert result.success
    runner.assert_not_called()


@pytest.mark.parametrize("pytest_present", [True, False])
@pytest.mark.asyncio
async def test_missing_cli_is_an_error_even_inside_pytest(monkeypatch, pytest_present):
    if pytest_present:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "outer-harness")
    else:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        "orb.providers.oci.handlers.oci_compute_handler.shutil.which", lambda _: None
    )
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")

    result = await handler.create_instances(make_operation())

    assert not result.success
    assert result.error_code == "OCI_CLI_UNAVAILABLE"


@pytest.mark.parametrize("flag", ["--metadata", "--from-json", "--freeform-tags"])
def test_failure_omits_arguments_payload_and_echoed_output(flag):
    secret = "private-bootstrap-and-key-marker"
    metadata = {"ssh_authorized_keys": secret, "user_data": secret}
    service_error = {
        "code": "NotAuthorizedOrNotFound",
        "status": 404,
        "message": json.dumps(metadata),
        "request_endpoint": secret,
    }
    runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            [], 1, secret, "ServiceError:\n" + json.dumps(service_error)
        )
    )
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=runner)

    with pytest.raises(RuntimeError) as error:
        handler._run_oci(
            ["compute", "instance", "launch", flag, json.dumps(metadata)], payload=metadata
        )

    rendered = "".join(traceback.format_exception(error.value))
    assert secret not in rendered
    assert "ssh_authorized_keys" not in str(error.value)
    assert "compute instance launch" in str(error.value)
    assert "code=NotAuthorizedOrNotFound, status=404" in str(error.value)
    assert runner.call_args.args[0][-1].startswith("file://")


@pytest.mark.parametrize(
    "failure", ["timeout", "oserror", "subprocess", "invalid_json", "plain_error"]
)
def test_unstructured_failures_cannot_echo_command_data(failure):
    secret = "private-command-output-marker"
    args = ["oci", "compute", "instance", "launch", "--metadata", secret]
    exceptions = {
        "timeout": subprocess.TimeoutExpired(args, 120, output=secret, stderr=secret),
        "oserror": OSError(secret),
        "subprocess": subprocess.CalledProcessError(1, args, output=secret, stderr=secret),
    }
    runner = MagicMock()
    if failure in exceptions:
        runner.side_effect = exceptions[failure]
    else:
        runner.return_value = subprocess.CompletedProcess(
            args, 0 if failure == "invalid_json" else 1, secret, secret
        )
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=runner)

    with pytest.raises(RuntimeError) as error:
        handler._run_oci(args[1:])

    assert secret not in "".join(traceback.format_exception(error.value))
    assert "compute instance launch" in str(error.value)


@pytest.mark.asyncio
async def test_launch_failure_stays_redacted_in_provider_result_and_logs():
    secret = "private-launch-payload-marker"
    runner = MagicMock(return_value=subprocess.CompletedProcess([], 1, secret, secret))
    logger = MagicMock()
    handler = OCIComputeHandler(logger, "us-phoenix-1", run_command=runner)
    operation = make_operation()
    operation.parameters["user_data"] = secret
    operation.parameters["ssh_authorized_keys"] = secret

    result = await handler.create_instances(operation)

    assert not result.success
    assert result.error_code == "OCI_LAUNCH_FAILED"
    assert result.metadata["launched_instance_ids"] == []
    assert secret not in str(result)
    assert secret not in str(logger.mock_calls)


@pytest.mark.parametrize(
    "stderr",
    [
        'ServiceError: {"code": "bad\\nvalue", "status": "not a status"}',
        'ServiceError: {"message": {"code": "NestedPayload"}}',
        "[]",
    ],
)
def test_service_summary_ignores_unstructured_and_nested_fields(stderr):
    assert OCIComputeHandler._service_error_summary(stderr) == ""
