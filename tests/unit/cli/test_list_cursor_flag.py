"""`--cursor` on the list commands: parsed, forwarded, and hinted for human output."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import ListMachinesOutput, ListRequestsOutput
from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
from orb.application.services.orchestration.list_requests import ListRequestsOrchestrator
from orb.cli.args import add_machine_actions, add_request_actions
from orb.interface.machine_command_handlers import handle_list_machines
from orb.interface.request_command_handlers import handle_list_requests
from orb.interface.response_formatting_service import ResponseFormattingService


def _parser(add_actions) -> argparse.ArgumentParser:  # noqa: ANN001
    parser = argparse.ArgumentParser()
    add_actions(parser.add_subparsers(dest="action"))
    return parser


def _container(orchestrator_cls, orchestrator) -> MagicMock:  # noqa: ANN001
    container = MagicMock()
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_machine_list.return_value = InterfaceResponse(data={})
    formatter.format_request_status.return_value = InterfaceResponse(data={})
    container.get.side_effect = lambda t: {
        orchestrator_cls: orchestrator,
        ResponseFormattingService: formatter,
    }.get(t, MagicMock())
    return container


@pytest.mark.unit
class TestFlagIsParsed:
    @pytest.mark.parametrize(
        ("add_actions", "command"),
        [(add_machine_actions, "machines"), (add_request_actions, "requests")],
    )
    def test_list_accepts_cursor(self, add_actions, command: str) -> None:  # noqa: ANN001
        parser = _parser(add_actions)

        namespace = parser.parse_args(["list", "--cursor", "abc123"])

        assert namespace.cursor == "abc123"

    def test_return_request_listing_accepts_cursor(self) -> None:
        parser = _parser(add_request_actions)

        assert parser.parse_args(["list-returns", "--cursor", "abc"]).cursor == "abc"

    def test_cursor_defaults_to_none(self) -> None:
        assert _parser(add_machine_actions).parse_args(["list"]).cursor is None

    @pytest.mark.parametrize(
        ("add_actions", "argv"),
        [
            (add_machine_actions, ["show", "m-1"]),
            (add_machine_actions, ["stop", "m-1"]),
            (add_request_actions, ["show"]),
            (add_request_actions, ["cancel", "req-1"]),
        ],
    )
    def test_commands_that_do_not_page_reject_cursor(self, add_actions, argv: list[str]) -> None:  # noqa: ANN001
        parser = _parser(add_actions)

        with pytest.raises(SystemExit):
            parser.parse_args([*argv, "--cursor", "abc"])


@pytest.mark.unit
@pytest.mark.asyncio
class TestFlagIsForwarded:
    async def test_machines_list(self) -> None:
        orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
        orchestrator.execute.return_value = ListMachinesOutput(machines=[])
        ns = argparse.Namespace(limit=5, offset=0, cursor="tok")
        ns._container = _container(ListMachinesOrchestrator, orchestrator)

        await handle_list_machines(ns)

        assert orchestrator.execute.call_args[0][0].cursor == "tok"

    async def test_requests_list(self) -> None:
        orchestrator = AsyncMock(spec=ListRequestsOrchestrator)
        orchestrator.execute.return_value = ListRequestsOutput(requests=[])
        ns = argparse.Namespace(limit=5, offset=0, cursor="tok", status=None)
        ns._container = _container(ListRequestsOrchestrator, orchestrator)

        await handle_list_requests(ns)

        assert orchestrator.execute.call_args[0][0].cursor == "tok"


@pytest.mark.unit
@pytest.mark.asyncio
class TestCursorHint:
    @pytest.mark.parametrize("fmt", ["table", "list"])
    async def test_human_formats_print_the_next_cursor_on_stderr(self, fmt: str, capsys) -> None:  # noqa: ANN001
        orchestrator = AsyncMock(spec=ListMachinesOrchestrator)
        orchestrator.execute.return_value = ListMachinesOutput(machines=[], next_cursor="NEXT")
        ns = argparse.Namespace(limit=5, offset=0, format=fmt)
        ns._container = _container(ListMachinesOrchestrator, orchestrator)

        await handle_list_machines(ns)

        captured = capsys.readouterr()
        assert "next cursor: NEXT" in captured.err
        assert "NEXT" not in captured.out

    @pytest.mark.parametrize("fmt", ["json", "yaml", None])
    async def test_document_formats_stay_quiet(self, fmt, capsys) -> None:  # noqa: ANN001
        orchestrator = AsyncMock(spec=ListRequestsOrchestrator)
        orchestrator.execute.return_value = ListRequestsOutput(requests=[], next_cursor="NEXT")
        ns = argparse.Namespace(limit=5, offset=0, format=fmt, status=None)
        ns._container = _container(ListRequestsOrchestrator, orchestrator)

        await handle_list_requests(ns)

        assert "NEXT" not in capsys.readouterr().err

    async def test_last_page_prints_nothing(self, capsys) -> None:  # noqa: ANN001
        orchestrator = AsyncMock(spec=ListRequestsOrchestrator)
        orchestrator.execute.return_value = ListRequestsOutput(requests=[], next_cursor=None)
        ns = argparse.Namespace(limit=5, offset=0, format="table", status=None)
        ns._container = _container(ListRequestsOrchestrator, orchestrator)

        await handle_list_requests(ns)

        assert capsys.readouterr().err == ""
