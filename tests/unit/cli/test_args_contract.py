"""CLI argument contract tests — one file covering all 6 consistency fix tasks."""

import argparse

import pytest

from orb.cli.args import (
    add_machine_actions,
    add_provider_actions,
    add_request_actions,
    add_template_actions,
)
from orb.cli.args_extractor import ArgsExtractor
from orb.domain.machine.machine_status import MachineStatus


def _make_subparsers() -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:  # type: ignore[type-arg]
    parser = argparse.ArgumentParser()
    sp = parser.add_subparsers(dest="action")
    return parser, sp


def _parse(
    sp_tuple: tuple[argparse.ArgumentParser, argparse._SubParsersAction], args: list[str]
) -> argparse.Namespace:  # type: ignore[type-arg]
    """Parse args using the parent parser that owns the subparsers."""
    parser, _sp = sp_tuple
    return parser.parse_args(args)


# ---------------------------------------------------------------------------
# Task 2046 — machines list --status enum choices
# ---------------------------------------------------------------------------


def test_machines_list_status_has_choices():
    _, sp = _make_subparsers()
    add_machine_actions(sp)
    list_parser = sp.choices["list"]
    status_action = next(
        a for a in list_parser._actions if "--status" in getattr(a, "option_strings", [])
    )
    choices = list(status_action.choices) if status_action.choices is not None else []
    assert len(choices) > 0


def test_machines_list_status_choices_match_machine_status_enum():
    _, sp = _make_subparsers()
    add_machine_actions(sp)
    list_parser = sp.choices["list"]
    status_action = next(
        a for a in list_parser._actions if "--status" in getattr(a, "option_strings", [])
    )
    assert status_action.choices is not None
    assert set(status_action.choices) == {s.value for s in MachineStatus}


@pytest.mark.parametrize("valid_status", [s.value for s in MachineStatus])
def test_machines_list_valid_status_accepted(valid_status):
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_machine_actions(sp)
    ns = _parse(sp_tuple, ["list", "--status", valid_status])
    assert ns.status == valid_status


def test_machines_list_invalid_status_raises_argparse_error():
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_machine_actions(sp)
    with pytest.raises(SystemExit) as exc_info:
        _parse(sp_tuple, ["list", "--status", "not-a-real-status"])
    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Task 2044 — fix extract_output_format + rename --template-id to --request-id
# ---------------------------------------------------------------------------


def test_extract_output_format_reads_format_attr():
    ns = argparse.Namespace(format="yaml", output="/tmp/out.txt")
    extractor = ArgsExtractor(ns)
    assert extractor.extract_output_format() == "yaml"


def test_extract_output_format_does_not_return_file_path():
    ns = argparse.Namespace(output="/tmp/results.json")
    extractor = ArgsExtractor(ns)
    result = extractor.extract_output_format(default="table")
    assert result != "/tmp/results.json"


def test_machines_list_has_request_id_flag_not_template_id():
    _, sp = _make_subparsers()
    add_machine_actions(sp)
    list_parser = sp.choices["list"]
    all_opts = [opt for a in list_parser._actions for opt in getattr(a, "option_strings", [])]
    assert "--request-id" in all_opts
    assert "--template-id" not in all_opts


# ---------------------------------------------------------------------------
# Task 2047 — providers select positional named provider_name + exec --args alias
# ---------------------------------------------------------------------------


def test_providers_select_positional_named_provider_name():
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_provider_actions(sp)
    ns = _parse(sp_tuple, ["select", "aws-prod"])
    assert hasattr(ns, "provider_name")
    assert ns.provider_name == "aws-prod"


def test_providers_exec_accepts_args_flag():
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_provider_actions(sp)
    ns = _parse(sp_tuple, ["exec", "describe-instances", "--args", '{"key": "val"}'])
    assert ns.params == '{"key": "val"}'


# ---------------------------------------------------------------------------
# Task 2045 — requests list --offset forwarded to orchestrator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_list_offset_forwarded_to_orchestrator():
    from unittest.mock import AsyncMock, MagicMock, patch

    from orb.application.dto.interface_response import InterfaceResponse
    from orb.application.services.orchestration.dtos import ListRequestsOutput
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
    from orb.application.services.response_formatting_service import ResponseFormattingService
    from orb.interface.request_command_handlers import handle_list_requests

    container = MagicMock()
    list_req_orch = AsyncMock(spec=ListRequestsOrchestrator)
    list_req_orch.execute.return_value = ListRequestsOutput(requests=[])
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_request_status.return_value = InterfaceResponse(data={"requests": []})

    container.get.side_effect = lambda t: {
        ListRequestsOrchestrator: list_req_orch,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())

    ns = argparse.Namespace(offset=25, limit=50, status=None)

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_list_requests(ns)

    call_input = list_req_orch.execute.call_args[0][0]
    assert call_input.offset == 25


@pytest.mark.asyncio
async def test_requests_list_status_forwarded_to_orchestrator():
    from unittest.mock import AsyncMock, MagicMock, patch

    from orb.application.dto.interface_response import InterfaceResponse
    from orb.application.services.orchestration.dtos import ListRequestsOutput
    from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
    from orb.application.services.response_formatting_service import ResponseFormattingService
    from orb.interface.request_command_handlers import handle_list_requests

    container = MagicMock()
    list_req_orch = AsyncMock(spec=ListRequestsOrchestrator)
    list_req_orch.execute.return_value = ListRequestsOutput(requests=[])
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_request_status.return_value = InterfaceResponse(data={"requests": []})

    container.get.side_effect = lambda t: {
        ListRequestsOrchestrator: list_req_orch,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())

    ns = argparse.Namespace(offset=0, limit=10, status="pending")

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_list_requests(ns)

    call_input = list_req_orch.execute.call_args[0][0]
    assert call_input.status == "pending"
    assert call_input.limit == 10


# ---------------------------------------------------------------------------
# Task 2043 — --machine-id/-m, --request-id/-r, --template-id/-t flag forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand", ["return", "terminate", "stop", "start"])
def test_machines_subcommand_accepts_machine_id_long_flag(subcommand):
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_machine_actions(sp)
    ns = _parse(sp_tuple, [subcommand, "--machine-id", "i-abc123"])
    ids = getattr(ns, "machine_ids", None) or getattr(ns, "machine_ids_flag", None)
    assert ids is not None and "i-abc123" in ids


@pytest.mark.parametrize("subcommand", ["return", "terminate", "stop", "start"])
def test_machines_subcommand_accepts_machine_id_short_flag(subcommand):
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_machine_actions(sp)
    ns = _parse(sp_tuple, [subcommand, "-m", "i-abc123"])
    ids = getattr(ns, "machine_ids", None) or getattr(ns, "machine_ids_flag", None)
    assert ids is not None and "i-abc123" in ids


@pytest.mark.parametrize("subcommand", ["show", "cancel"])
def test_requests_subcommand_accepts_request_id_flag(subcommand):
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_request_actions(sp)
    ns = _parse(sp_tuple, [subcommand, "--request-id", "req-999"])
    rid = getattr(ns, "request_id", None) or getattr(ns, "flag_request_id", None)
    assert rid is not None


def test_templates_validate_accepts_template_id_flag():
    sp_tuple = _make_subparsers()
    _, sp = sp_tuple
    add_template_actions(sp)
    ns = _parse(sp_tuple, ["validate", "--template-id", "tmpl-1"])
    tid = getattr(ns, "template_id", None) or getattr(ns, "flag_template_id", None)
    assert tid is not None


# ---------------------------------------------------------------------------
# Task 2048 — build_parser() extraction + --detailed on providers health + contract
# ---------------------------------------------------------------------------


def test_build_parser_is_callable():
    from orb.cli.args import build_parser

    parser, resource_parsers = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert isinstance(resource_parsers, dict)


def test_parse_args_uses_build_parser():
    """parse_args must be a thin wrapper — build_parser must exist and be importable."""
    from orb.cli.args import build_parser, parse_args

    assert callable(build_parser)
    assert callable(parse_args)


@pytest.mark.parametrize(
    "resource,subcommand,flag,short",
    [
        ("machines", "return", "--machine-id", "-m"),
        ("machines", "terminate", "--machine-id", "-m"),
        ("machines", "stop", "--machine-id", "-m"),
        ("machines", "start", "--machine-id", "-m"),
        ("requests", "show", "--request-id", "-r"),
        ("requests", "cancel", "--request-id", "-r"),
        ("templates", "validate", "--template-id", "-t"),
        ("providers", "health", "--detailed", None),
    ],
)
def test_cli_contract_flag_exists(resource, subcommand, flag, short):
    adder_map = {
        "machines": add_machine_actions,
        "requests": add_request_actions,
        "templates": add_template_actions,
        "providers": add_provider_actions,
    }
    _, sp = _make_subparsers()
    adder_map[resource](sp)
    sub_parser = sp.choices[subcommand]
    all_opts = [
        opt for action in sub_parser._actions for opt in getattr(action, "option_strings", [])
    ]
    assert flag in all_opts, f"{flag} missing from {resource} {subcommand}"
    if short:
        assert short in all_opts, f"{short} missing from {resource} {subcommand}"


# ---------------------------------------------------------------------------
# machine_ids_flag actually used by stop/start/return handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_machines_uses_machine_ids_flag():
    from unittest.mock import AsyncMock, MagicMock, patch

    from orb.application.services.orchestration.dtos import StopMachinesOutput
    from orb.application.services.orchestration.stop_machines import StopMachinesOrchestrator
    from orb.application.services.response_formatting_service import ResponseFormattingService
    from orb.interface.machine_command_handlers import handle_stop_machines

    orch = AsyncMock(spec=StopMachinesOrchestrator)
    orch.execute.return_value = StopMachinesOutput(
        success=True, message="ok", stopped_machines=["i-flag1"], failed_machines=[]
    )
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_machine_operation.return_value = MagicMock()
    container = MagicMock()
    container.get.side_effect = lambda t: {
        StopMachinesOrchestrator: orch,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())

    ns = argparse.Namespace(all=False, force=False, machine_ids=[], machine_ids_flag=["i-flag1"])

    with patch("orb.interface.machine_command_handlers.get_container", return_value=container):
        await handle_stop_machines(ns)

    call_input = orch.execute.call_args[0][0]
    assert "i-flag1" in call_input.machine_ids


@pytest.mark.asyncio
async def test_start_machines_uses_machine_ids_flag():
    from unittest.mock import AsyncMock, MagicMock, patch

    from orb.application.services.orchestration.dtos import StartMachinesOutput
    from orb.application.services.orchestration.start_machines import StartMachinesOrchestrator
    from orb.application.services.response_formatting_service import ResponseFormattingService
    from orb.interface.machine_command_handlers import handle_start_machines

    orch = AsyncMock(spec=StartMachinesOrchestrator)
    orch.execute.return_value = StartMachinesOutput(
        success=True, message="ok", started_machines=["i-flag2"], failed_machines=[]
    )
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_machine_operation.return_value = MagicMock()
    container = MagicMock()
    container.get.side_effect = lambda t: {
        StartMachinesOrchestrator: orch,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())

    ns = argparse.Namespace(all=False, machine_ids=[], machine_ids_flag=["i-flag2"])

    with patch("orb.interface.machine_command_handlers.get_container", return_value=container):
        await handle_start_machines(ns)

    call_input = orch.execute.call_args[0][0]
    assert "i-flag2" in call_input.machine_ids


@pytest.mark.asyncio
async def test_request_return_machines_uses_machine_ids_flag():
    from unittest.mock import AsyncMock, MagicMock, patch

    from orb.application.dto.interface_response import InterfaceResponse
    from orb.application.services.orchestration.dtos import ReturnMachinesOutput
    from orb.application.services.orchestration.return_machines import ReturnMachinesOrchestrator
    from orb.application.services.response_formatting_service import ResponseFormattingService
    from orb.interface.request_command_handlers import handle_request_return_machines

    orch = AsyncMock(spec=ReturnMachinesOrchestrator)
    orch.execute.return_value = ReturnMachinesOutput(request_id=None, status="completed")
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_request_operation.return_value = InterfaceResponse(data={})

    container = MagicMock()
    container.get.side_effect = lambda t: {
        ReturnMachinesOrchestrator: orch,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())

    ns = argparse.Namespace(all=False, force=False, machine_ids=[], machine_ids_flag=["i-flag3"])

    with patch("orb.interface.request_command_handlers.get_container", return_value=container):
        await handle_request_return_machines(ns)

    call_input = orch.execute.call_args[0][0]
    assert "i-flag3" in call_input.machine_ids
