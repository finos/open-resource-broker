"""``orb providers exec`` routes allowlisted operations to the selected provider."""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.domain.base.operations import OperationType
from orb.domain.base.ports import ProviderSelectionPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.interface.system_command_handlers import handle_execute_provider_operation

pytestmark = pytest.mark.unit

AWS_OPERATIONS = [
    OperationType.CREATE_INSTANCES,
    OperationType.TERMINATE_INSTANCES,
    OperationType.CLEANUP_ORPHANED_RESOURCES,
]


def _capabilities(operations):
    caps = MagicMock()
    caps.supported_operations = list(operations)
    caps.supports_operation.side_effect = lambda op: op in operations
    return caps


def _args(
    operation: str | None = "cleanup-orphaned-resources",
    operations=AWS_OPERATIONS,
    active=("aws-1",),
    **kwargs,
):
    values = {
        "operation": operation,
        "provider_name": None,
        "provider_type": None,
        "dry_run": False,
        "list_operations": False,
        "param": None,
        "params": None,
    }
    values.update(kwargs)
    args = argparse.Namespace(**values)
    port = MagicMock()
    port.select_active_provider.return_value = SimpleNamespace(provider_name="aws-1")
    port.get_strategy_capabilities.return_value = _capabilities(operations)
    port.execute_operation = AsyncMock(
        return_value=SimpleNamespace(
            success=True, data={"deleted": [], "would_delete": [], "kept": []}, error_message=None
        )
    )
    config_port = MagicMock()
    config_port.get_active_providers.return_value = [
        SimpleNamespace(name=name, type="aws") for name in active
    ]
    container = MagicMock()
    container.get.side_effect = lambda key: {
        ProviderSelectionPort: port,
        ConfigurationPort: config_port,
    }.get(key, MagicMock())
    args._container = container
    return args, port


@pytest.mark.asyncio
async def test_an_allowlisted_operation_is_dispatched_with_its_parameters():
    args, port = _args(dry_run=True, param=["min_age_hours=6", "note=nightly"])

    response = await handle_execute_provider_operation(args)

    provider, operation = port.execute_operation.await_args.args
    assert provider == "aws-1"
    assert operation.operation_type == OperationType.CLEANUP_ORPHANED_RESOURCES
    assert operation.parameters == {"dry_run": True, "min_age_hours": 6, "note": "nightly"}
    assert response.exit_code == 0
    assert response.data["success"] is True


@pytest.mark.asyncio
async def test_the_operation_name_is_accepted_in_snake_case_and_json_params_merge():
    args, port = _args(
        operation="cleanup_orphaned_resources",
        params='{"min_age_hours": 48}',
        param=["force=true"],
    )

    await handle_execute_provider_operation(args)

    operation = port.execute_operation.await_args.args[1]
    assert operation.parameters == {"min_age_hours": 48, "force": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["terminate-instances", "create-instances", "not-a-real-op"])
async def test_an_operation_outside_the_allowlist_is_refused(requested):
    args, port = _args(operation=requested)

    response = await handle_execute_provider_operation(args)

    port.execute_operation.assert_not_awaited()
    assert response.exit_code == 1
    assert "cleanup-orphaned-resources" in response.data["message"]
    port.select_active_provider.assert_not_called()


@pytest.mark.asyncio
async def test_a_provider_without_the_operation_exits_one_and_lists_what_it_supports():
    args, port = _args(operations=[OperationType.CREATE_INSTANCES])

    response = await handle_execute_provider_operation(args)

    port.execute_operation.assert_not_awaited()
    assert response.exit_code == 1
    assert (
        "Provider 'aws-1' does not support 'cleanup-orphaned-resources'"
        in (response.data["message"])
    )
    assert "Supported: none" in response.data["message"]


@pytest.mark.asyncio
async def test_list_reports_the_operations_of_every_provider_instance():
    args, port = _args(operation=None, list_operations=True)

    response = await handle_execute_provider_operation(args)

    port.execute_operation.assert_not_awaited()
    assert response.exit_code == 0
    [entry] = response.data["providers"]
    assert entry["provider"] == "aws-1"
    assert entry["supported_operations"] == [
        "create_instances",
        "terminate_instances",
        "cleanup_orphaned_resources",
    ]
    assert entry["executable_operations"] == ["cleanup-orphaned-resources"]


@pytest.mark.asyncio
async def test_list_for_one_provider_does_not_read_the_configuration():
    args, port = _args(operation=None, list_operations=True, provider_name="aws-9")

    response = await handle_execute_provider_operation(args)

    port.get_strategy_capabilities.assert_called_once_with("aws-9")
    assert response.data["providers"][0]["provider"] == "aws-9"


@pytest.mark.asyncio
async def test_a_missing_operation_is_an_error():
    args, port = _args(operation=None)

    response = await handle_execute_provider_operation(args)

    assert response.exit_code == 1
    port.execute_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_malformed_parameter_is_rejected_before_the_provider_is_called():
    args, port = _args(param=["min_age_hours"])

    response = await handle_execute_provider_operation(args)

    assert response.exit_code == 1
    port.execute_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_operation_exits_non_zero():
    args, port = _args()
    port.execute_operation.return_value = SimpleNamespace(
        success=False, data=None, error_message="Launch template sweep aborted"
    )

    response = await handle_execute_provider_operation(args)

    assert response.exit_code == 1
    assert "aborted" in response.data["message"]


@pytest.mark.asyncio
async def test_a_partial_result_exits_non_zero_with_its_report():
    args, port = _args()
    port.execute_operation.return_value = SimpleNamespace(
        success=False,
        data={
            "deleted": [{"launch_template_id": "lt-1"}],
            "failed": [{"launch_template_id": "lt-2"}],
        },
        error_message="Deleted 1 launch template(s); 1 could not be deleted",
    )

    response = await handle_execute_provider_operation(args)

    assert response.exit_code == 1
    assert response.data["deleted"] == [{"launch_template_id": "lt-1"}]
    assert response.data["failed"] == [{"launch_template_id": "lt-2"}]


@pytest.mark.asyncio
async def test_a_changing_run_with_several_providers_needs_an_explicit_name():
    args, port = _args(active=("aws-1", "aws-2"))

    response = await handle_execute_provider_operation(args)

    assert response.exit_code == 1
    assert "--provider-name" in response.data["message"]
    port.execute_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_dry_run_with_several_providers_may_use_the_default():
    args, _ = _args(active=("aws-1", "aws-2"), dry_run=True)

    response = await handle_execute_provider_operation(args)

    assert response.exit_code == 0
    assert response.data["provider"] == "aws-1"


@pytest.mark.asyncio
async def test_an_explicit_name_with_several_providers_runs_and_is_echoed():
    args, port = _args(active=("aws-1", "aws-2"), provider_name="aws-2")
    port.select_active_provider.return_value = SimpleNamespace(provider_name="aws-2")

    response = await handle_execute_provider_operation(args)

    assert response.exit_code == 0
    assert port.execute_operation.await_args.args[0] == "aws-2"
    assert response.data["provider"] == "aws-2"


@pytest.mark.asyncio
async def test_the_provider_scope_in_the_result_is_passed_through():
    args, port = _args()
    port.execute_operation.return_value = SimpleNamespace(
        success=True, data={"region": "eu-west-2", "account_id": "123456789012"}, error_message=None
    )

    response = await handle_execute_provider_operation(args)

    assert response.data["provider"] == "aws-1"
    assert response.data["region"] == "eu-west-2"
    assert response.data["account_id"] == "123456789012"


def test_the_command_is_parsed_with_its_options():
    from orb.cli.args import parse_args

    argv = [
        "orb",
        "providers",
        "exec",
        "cleanup-orphaned-resources",
        "--dry-run",
        "--param",
        "min_age_hours=3",
        "--param",
        "force=true",
    ]
    with patch.object(sys, "argv", argv):
        ns, _ = parse_args()
    assert ns.action == "exec"
    assert ns.operation == "cleanup-orphaned-resources"
    assert ns.dry_run is True
    assert ns.param == ["min_age_hours=3", "force=true"]


def test_list_needs_no_operation():
    from orb.cli.args import parse_args

    with patch.object(sys, "argv", ["orb", "providers", "exec", "--list"]):
        ns, _ = parse_args()
    assert ns.list_operations is True
    assert ns.operation is None


def test_the_dedicated_cleanup_subcommand_no_longer_exists():
    from orb.cli.args import parse_args

    with patch.object(sys, "argv", ["orb", "providers", "cleanup-launch-templates"]):
        with pytest.raises(SystemExit):
            parse_args()
