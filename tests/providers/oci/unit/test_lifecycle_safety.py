"""Failure-path regressions for the OCI provider boundary and lifecycle."""

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.cli_client import OCICommandError, run_oci
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.mapping import OCITemplateMapper
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy
from tests.providers.oci.unit.test_command_execution import make_operation


@pytest.mark.parametrize("status", [400, 403, 500])
def test_raw_http_error_is_failure_even_when_cli_exits_zero(status):
    marker = "sensitive-error-body"
    runner = MagicMock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "status": f"{status} Error",
                    "data": {"code": "OutOfHostCapacity", "message": marker},
                }
            ),
        )
    )
    with pytest.raises(OCICommandError) as error:
        run_oci(["raw-request"], region="us-phoenix-1", runner=runner)
    assert error.value.status == status
    assert error.value.code == "OutOfHostCapacity"
    assert error.value.ambiguous is (status >= 500)
    assert marker not in str(error.value)


@pytest.mark.asyncio
async def test_signed_launch_sends_stable_header_and_private_api_body():
    requests = []
    paths = []

    def run(cmd, **kwargs):
        assert cmd[1] == "raw-request"
        assert "--opc-retry-token" not in cmd
        assert (
            cmd[cmd.index("--target-uri") + 1]
            == "https://iaas.us-phoenix-1.oraclecloud.com/20160918/instances"
        )
        path = Path(cmd[cmd.index("--request-body") + 1].removeprefix("file://"))
        paths.append(path)
        body = json.loads(path.read_text())
        assert body["metadata"]["ssh_authorized_keys"] == "private-input-marker"
        assert body["sourceDetails"]["sourceType"] == "image"
        assert "private-input-marker" not in " ".join(cmd)
        token = json.loads(cmd[cmd.index("--request-headers") + 1])["opc-retry-token"]
        assert body["freeformTags"]["orb_launch_id"] == token
        requests.append(token)
        return subprocess.CompletedProcess(
            cmd, 0, '{"status":"200 OK","data":{"id":"instance","lifecycleState":"PROVISIONING"}}'
        )

    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=run)
    operation = make_operation()
    operation.parameters.update(
        request_id="same-request", ssh_authorized_keys="private-input-marker"
    )
    for _ in range(2):
        result = await handler.create_instances(operation)
        assert result.success, result.error_message
        assert result.data["instances"][0]["status"] == "PROVISIONING"
    assert requests[0] == requests[1]
    assert all(not p.exists() for p in paths)


@pytest.mark.asyncio
async def test_cross_region_launch_is_rejected_before_mutation():
    runner = MagicMock(side_effect=AssertionError("cross-region request reached OCI"))
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=runner)
    operation = make_operation()
    operation.parameters["subnet_ids"] = ["ocid1.subnet.oc1.iad.example"]
    result = await handler.create_instances(operation)
    assert not result.success
    assert "region" in result.error_message
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_launch_status_return_and_rollback_use_provider_region():
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "raw-request":
            if sum(c[1:4] == command[1:4] for c in commands) == 2:
                return subprocess.CompletedProcess(
                    command, 1, "", 'ServiceError: {"code":"InvalidParameter","status":400}'
                )
            return subprocess.CompletedProcess(
                command, 0, '{"status":"200 OK","data":{"id":"ocid1.instance.oc1.phx.example"}}'
            )
        return subprocess.CompletedProcess(command, 0, '{"data":{"lifecycle-state":"TERMINATED"}}')

    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=run)
    operation = make_operation()
    operation.parameters.update(count=2, subnet_ids=["ocid1.subnet.oc1.phx.example"])
    result = await handler.create_instances(operation)
    assert not result.success
    assert result.metadata["rollback_requested_instance_ids"] == ["ocid1.instance.oc1.phx.example"]
    status = make_operation(ProviderOperationType.GET_INSTANCE_STATUS)
    status.parameters["machine_ids"] = ["ocid1.instance.oc1.phx.example"]
    assert handler.get_instance_status(status).success
    assert handler.terminate_instances(status).success
    assert all(command[command.index("--region") + 1] == "us-phoenix-1" for command in commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [True, False])
async def test_timeout_reconciles_owned_launch_without_ondemand_fallback(monkeypatch, visible):
    monkeypatch.setattr("orb.providers.oci.handlers.oci_compute_handler.time.sleep", lambda _: None)
    launches, queries = [], []
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=MagicMock())

    def launch(payload, force_ondemand=False):
        launches.append((payload, force_ondemand))
        raise OCICommandError("timeout", reason="timeout")

    def query(args, **kwargs):
        queries.append(args)
        payload = launches[-1][0]
        # First list call has not observed the accepted launch yet.
        items = (
            []
            if not visible or len(queries) == 1
            else [
                {
                    "id": "accepted-before-timeout",
                    "display-name": payload["display_name"],
                    "freeform-tags": payload["freeform_tags"],
                    "lifecycle-state": "PROVISIONING",
                }
            ]
        )
        return {"data": items}

    handler._launch_with_cli = launch
    handler._run_oci = query
    operation = make_operation()
    operation.parameters.update(
        request_id="same-request", capacity_type="preemptible", fallback_to_ondemand=True
    )
    result = await handler.create_instances(operation)
    assert len(launches) == 1
    assert launches[0][1] is False
    if visible:
        assert result.success and result.data["instance_ids"] == ["accepted-before-timeout"]
    else:
        assert not result.success
        token = result.metadata["unresolved_launch"]["retry_token"]
        second = await handler.create_instances(operation)
        assert second.metadata["unresolved_launch"]["retry_token"] == token
        assert not second.metadata["fallback_attempted"]


@pytest.mark.asyncio
async def test_reconciliation_does_not_adopt_another_requests_worker(monkeypatch):
    monkeypatch.setattr("orb.providers.oci.handlers.oci_compute_handler.time.sleep", lambda _: None)
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=MagicMock())
    handler._launch_with_cli = MagicMock(side_effect=OCICommandError("timeout", reason="timeout"))
    handler._run_oci = MagicMock(
        return_value={
            "data": [{"id": "unrelated", "freeform-tags": {"orb_launch_id": "someone-else"}}]
        }
    )
    result = await handler.create_instances(make_operation())
    assert not result.success
    assert result.metadata["launched_instance_ids"] == []
    assert all(call.args[0][2] == "list" for call in handler._run_oci.call_args_list)


def test_terminate_attempts_all_and_reports_mixed_results():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=MagicMock())
    handler._run_oci = MagicMock(
        side_effect=[
            OCICommandError("missing or inaccessible", status=404, code="NotAuthorizedOrNotFound"),
            OCICommandError("forbidden", status=403, code="NotAuthorized"),
            {},
        ]
    )
    result = handler.terminate_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"machine_ids": ["gone", "denied", "running"]},
        )
    )
    assert not result.success
    assert handler._run_oci.call_count == 3
    assert [item["status"] for item in result.data["results"]] == [
        "not_found_or_not_authorized",
        "failed",
        "accepted",
    ]
    assert result.data["results"][0]["verified_terminated"] is False


@pytest.mark.parametrize("failure", [None, "timeout", "service"])
def test_private_launch_input_stays_off_argv_and_is_removed(failure):
    paths = []
    secret = "private-bootstrap-marker"

    def runner(cmd, **kwargs):
        assert secret not in str(cmd)
        path = Path(cmd[cmd.index("--from-json") + 1].removeprefix("file://"))
        paths.append(path)
        assert json.loads(path.read_text())["metadata"]["user_data"] == secret
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 120)
        return subprocess.CompletedProcess(cmd, 1 if failure else 0, "{}", "")

    if failure:
        with pytest.raises(OCICommandError):
            run_oci(
                ["compute", "instance", "launch"],
                region="us-phoenix-1",
                runner=runner,
                payload={"metadata": {"user_data": secret}},
            )
    else:
        run_oci(
            ["compute", "instance", "launch"],
            region="us-phoenix-1",
            runner=runner,
            payload={"metadata": {"user_data": secret}},
        )
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize("reason", ["unavailable", "service"])
def test_health_and_credentials_report_real_command_failure(monkeypatch, reason):
    monkeypatch.setattr(
        "orb.providers.oci.strategy.oci_provider_strategy.run_oci",
        MagicMock(side_effect=OCICommandError("OCI probe failed", reason=reason)),
    )
    strategy = OCIProviderStrategy(
        OCIProviderConfig(credential_source="instance_principal"), MagicMock()
    )
    assert not strategy.check_health().is_healthy
    assert not strategy.test_credentials(credential_source="instance_principal")["success"]


def test_initialize_reports_missing_cli(monkeypatch):
    monkeypatch.setattr(
        "orb.providers.oci.handlers.oci_compute_handler.shutil.which", lambda _: None
    )
    strategy = OCIProviderStrategy(OCIProviderConfig(), MagicMock())
    assert not strategy.initialize()


@pytest.mark.parametrize(
    "value,expected", [("false", False), ("0", False), ("true", True), ("1", True), (False, False)]
)
def test_string_boolean_options(value, expected):
    normalized = OCITemplateMapper.normalize_template_fields(
        {"fallback_to_ondemand": value, "preemptible_preserve_boot_volume": value}
    )
    assert normalized["fallback_to_ondemand"] is expected
    assert normalized["preemptible_preserve_boot_volume"] is expected


@pytest.mark.asyncio
async def test_launch_tags_do_not_mutate_caller_or_other_instances():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    operation = make_operation(context={"dry_run": True})
    tags = {"environment": "test"}
    operation.parameters.update(count=2, tags=tags)
    result = await handler.create_instances(operation)
    first, second = result.data["launch_requests"]
    assert tags == {"environment": "test"}
    assert first["freeform_tags"] is not second["freeform_tags"]
    assert first["freeform_tags"]["orb_launch_id"] != second["freeform_tags"]["orb_launch_id"]


@pytest.mark.parametrize("is_return", [True, False])
def test_not_found_status_is_only_idempotent_for_returns(is_return):
    from types import SimpleNamespace

    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=MagicMock())
    handler._run_oci = MagicMock(
        side_effect=OCICommandError(
            "missing or inaccessible", code="NotAuthorizedOrNotFound", status=404
        )
    )
    operation = make_operation(ProviderOperationType.GET_INSTANCE_STATUS)
    operation.parameters["request"] = SimpleNamespace(
        request_type="return" if is_return else "acquire", requested_count=1
    )
    result = handler.get_instance_status(operation)
    assert result.success is is_return
    if is_return:
        assert result.data["instances"][0]["provider_data"]["termination_verified"] is False
        assert result.metadata["provider_fulfilment"].state == "fulfilled"


@pytest.mark.asyncio
async def test_invalid_boolean_cannot_reach_launch():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=MagicMock())
    operation = make_operation()
    operation.parameters["fallback_to_ondemand"] = "perhaps"
    result = await handler.create_instances(operation)
    assert not result.success and result.error_code == "INVALID_TEMPLATE"
    handler._run_command.assert_not_called()
