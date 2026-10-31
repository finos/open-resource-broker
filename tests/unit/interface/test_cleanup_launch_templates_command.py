"""``orb providers cleanup-launch-templates`` routes to the provider operation."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.domain.base.operations import OperationType
from orb.domain.base.ports import ProviderSelectionPort
from orb.interface.system_command_handlers import handle_cleanup_launch_templates

pytestmark = pytest.mark.unit


def _args(**kwargs):
    args = argparse.Namespace(
        provider_name=None,
        provider_type=None,
        dry_run=False,
        min_age_hours=24,
        force=False,
        **kwargs,
    )
    port = MagicMock()
    port.select_active_provider.return_value = SimpleNamespace(provider_name="aws-1")
    capabilities = MagicMock()
    capabilities.supports_operation.side_effect = lambda op: (
        op == OperationType.CLEANUP_ORPHANED_RESOURCES
    )
    port.get_strategy_capabilities.return_value = capabilities
    port.execute_operation = AsyncMock(
        return_value=SimpleNamespace(
            success=True, data={"deleted": [], "would_delete": [], "kept": []}, error_message=None
        )
    )
    container = MagicMock()
    container.get.side_effect = lambda key: port if key is ProviderSelectionPort else MagicMock()
    args._container = container
    return args, port


@pytest.mark.asyncio
async def test_the_flags_reach_the_provider_operation():
    args, port = _args()
    args.dry_run = True
    args.min_age_hours = 6

    response = await handle_cleanup_launch_templates(args)

    provider, operation = port.execute_operation.await_args.args
    assert provider == "aws-1"
    assert operation.operation_type == OperationType.CLEANUP_ORPHANED_RESOURCES
    assert operation.parameters == {"dry_run": True, "min_age_hours": 6, "force": False}
    assert response.exit_code == 0
    assert response.data["success"] is True


@pytest.mark.asyncio
async def test_a_failed_sweep_exits_non_zero():
    args, port = _args()
    port.execute_operation.return_value = SimpleNamespace(
        success=False, data=None, error_message="Launch template sweep aborted"
    )

    response = await handle_cleanup_launch_templates(args)

    assert response.exit_code == 1
    assert "aborted" in response.data["message"]


@pytest.mark.asyncio
async def test_a_provider_without_the_operation_is_reported():
    args, port = _args()
    port.get_strategy_capabilities.return_value.supports_operation.side_effect = None
    port.get_strategy_capabilities.return_value.supports_operation.return_value = False

    response = await handle_cleanup_launch_templates(args)

    port.execute_operation.assert_not_awaited()
    assert response.exit_code == 1


def test_the_command_is_parsed_with_its_options():
    import sys
    from unittest.mock import patch

    from orb.cli.args import parse_args

    argv = ["orb", "providers", "cleanup-launch-templates", "--dry-run", "--min-age-hours", "3"]
    with patch.object(sys, "argv", argv):
        ns, _ = parse_args()
    assert ns.action == "cleanup-launch-templates"
    assert ns.dry_run is True
    assert ns.min_age_hours == 3
    assert ns.force is False


@pytest.mark.asyncio
async def test_a_partial_sweep_exits_non_zero_with_its_report():
    args, port = _args()
    result = SimpleNamespace(
        success=False,
        data={
            "deleted": [{"launch_template_id": "lt-1"}],
            "failed": [{"launch_template_id": "lt-2"}],
        },
        error_message="Deleted 1 launch template(s); 1 could not be deleted",
    )
    port.execute_operation.return_value = result

    response = await handle_cleanup_launch_templates(args)

    assert response.exit_code == 1
    assert response.data["deleted"] == [{"launch_template_id": "lt-1"}]
    assert response.data["failed"] == [{"launch_template_id": "lt-2"}]
