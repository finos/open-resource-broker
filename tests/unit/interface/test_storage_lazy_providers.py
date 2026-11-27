"""A storage collaborator that cannot be resolved fails where it always did.

The query bus and configuration manager are resolved inside the handler methods,
so a command that never needs them does not resolve them, and one that does
reports the failure the same way as before the handlers received constructor
parameters.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.domain.base.exceptions import DomainException
from orb.interface.storage_command_handlers import StorageHandlers
from tests.utilities.cli_handlers import make_handler


def _unresolvable(message: str = "unavailable"):
    def provider():
        raise RuntimeError(message)

    return provider


def _handler(**overrides) -> StorageHandlers:
    return make_handler(StorageHandlers, **overrides)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_commands_that_need_no_bus_never_resolve_it() -> None:
    bus_provider = MagicMock()
    handler = _handler(
        query_bus_provider=bus_provider,
        list_orchestrator=AsyncMock(),
        config_orchestrator=AsyncMock(),
    )

    await handler.list_storage_strategies(Namespace())
    await handler.show_storage_config(Namespace(strategy=None))

    bus_provider.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_reports_an_unresolvable_bus_as_an_invalid_configuration() -> None:
    formatter = MagicMock()
    handler = _handler(query_bus_provider=_unresolvable("no bus"), formatter=formatter)

    await handler.validate_storage_config(Namespace())

    formatter.format_error.assert_called_once_with("Storage configuration invalid: no bus")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["storage_health", "storage_metrics", "test_storage"])
async def test_other_bus_users_surface_an_unresolvable_bus_as_an_error(method: str) -> None:
    handler = _handler(query_bus_provider=_unresolvable("no bus"))

    with pytest.raises(DomainException, match="no bus"):
        await getattr(handler, method)(Namespace(strategy=None, verbose=False))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_migrate_reports_an_unresolvable_configuration_without_its_detail() -> None:
    formatter = MagicMock()
    handler = _handler(
        config_manager_provider=_unresolvable("password=hunter2"), formatter=formatter
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("importlib.util.find_spec", lambda name: object())
        await handler.storage_migrate(Namespace(migrate_subcommand="up"))

    message = formatter.format_error.call_args.args[0]
    assert "Cannot determine the SQL database" in message
    assert "hunter2" not in message
