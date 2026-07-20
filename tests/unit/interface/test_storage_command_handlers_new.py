"""Unit tests for storage_command_handlers.

Covers happy path, error paths, and ImportError branches.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.orchestration.dtos import (
    GetStorageConfigOutput,
    ListStorageStrategiesOutput,
)
from orb.interface.response_formatting_service import ResponseFormattingService
from orb.interface.storage_command_handlers import (
    handle_list_storage_strategies,
    handle_show_storage_config,
    handle_storage_health,
    handle_storage_metrics,
    handle_storage_migrate,
    handle_validate_storage_config,
)


def _make_formatter() -> MagicMock:
    fmt = MagicMock(spec=ResponseFormattingService)
    fmt.format_storage_strategy_list.return_value = InterfaceResponse(data={"strategies": []})
    fmt.format_storage_config.return_value = InterfaceResponse(data={"config": {}})
    fmt.format_success.return_value = InterfaceResponse(data={"ok": True})
    fmt.format_error.return_value = InterfaceResponse(data={"error": "err"}, exit_code=1)
    fmt.format_config.return_value = InterfaceResponse(data={"data": {}})
    fmt.format_storage_test.return_value = InterfaceResponse(data={"test": "ok"})
    return fmt


@pytest.mark.unit
class TestHandleListStorageStrategies:
    """Tests for handle_list_storage_strategies."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.list_storage_strategies import (
            ListStorageStrategiesOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListStorageStrategiesOrchestrator)
        orch.execute.return_value = ListStorageStrategiesOutput(
            strategies=[{"name": "json"}, {"name": "sql"}], current_strategy="json", count=2
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListStorageStrategiesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        result = await handle_list_storage_strategies(args)

        orch.execute.assert_awaited_once()
        fmt.format_storage_strategy_list.assert_called_once_with(
            [{"name": "json"}, {"name": "sql"}], "json", 2
        )
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_orchestrator_called_with_empty_input(self):
        from orb.application.services.orchestration.list_storage_strategies import (
            ListStorageStrategiesOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=ListStorageStrategiesOrchestrator)
        orch.execute.return_value = ListStorageStrategiesOutput(
            strategies=[], current_strategy="json", count=0
        )

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ListStorageStrategiesOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        await handle_list_storage_strategies(args)

        call_input = orch.execute.call_args[0][0]
        # ListStorageStrategiesInput is a no-field dataclass
        from orb.application.services.orchestration.dtos import ListStorageStrategiesInput

        assert isinstance(call_input, ListStorageStrategiesInput)


@pytest.mark.unit
class TestHandleShowStorageConfig:
    """Tests for handle_show_storage_config."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        from orb.application.services.orchestration.get_storage_config import (
            GetStorageConfigOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetStorageConfigOrchestrator)
        orch.execute.return_value = GetStorageConfigOutput(config={"type": "json"})

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetStorageConfigOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container, strategy=None)
        result = await handle_show_storage_config(args)

        orch.execute.assert_awaited_once()
        fmt.format_storage_config.assert_called_once_with({"type": "json"})
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_strategy_name_forwarded(self):
        from orb.application.services.orchestration.get_storage_config import (
            GetStorageConfigOrchestrator,
        )

        fmt = _make_formatter()
        orch = AsyncMock(spec=GetStorageConfigOrchestrator)
        orch.execute.return_value = GetStorageConfigOutput(config={})

        container = MagicMock()
        container.get.side_effect = lambda t: {
            GetStorageConfigOrchestrator: orch,
            ResponseFormattingService: fmt,
        }.get(t, MagicMock())

        args = Namespace(_container=container, strategy="sql")
        await handle_show_storage_config(args)

        call_input = orch.execute.call_args[0][0]
        assert call_input.strategy_name == "sql"


@pytest.mark.unit
class TestHandleValidateStorageConfig:
    """Tests for handle_validate_storage_config."""

    @pytest.mark.asyncio
    async def test_query_path_used_when_health_query_available(self):
        """When the health query is importable, the real query bus path runs (not the
        static ImportError fallback)."""
        from orb.infrastructure.di.buses import QueryBus

        fmt = _make_formatter()
        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = {"status": "healthy", "checks": 3}

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        await handle_validate_storage_config(args)

        # The real query path must execute the health query, not fall back.
        mock_bus.execute.assert_awaited_once()
        payload = fmt.format_success.call_args[0][0]
        # Health status comes from the query result, and the fallback status "ok" is absent.
        assert payload["status"] == "healthy"
        assert payload["message"] == "Storage configuration is valid"

    @pytest.mark.asyncio
    async def test_query_bus_exception_returns_error(self):
        """Exceptions from query execution are caught and formatted as errors."""
        from orb.infrastructure.di.buses import QueryBus

        fmt = _make_formatter()
        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.side_effect = RuntimeError("storage down")

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        result = await handle_validate_storage_config(args)

        # The query is executed and its failure is surfaced via format_error.
        mock_bus.execute.assert_awaited_once()
        fmt.format_error.assert_called_once()
        assert "storage down" in fmt.format_error.call_args[0][0]
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_successful_query_returns_success_response(self):
        """Successful query bus execution produces a success response built from the
        health payload."""
        from orb.infrastructure.di.buses import QueryBus

        fmt = _make_formatter()
        fmt.format_success.return_value = InterfaceResponse(data={"status": "healthy"})
        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = {"status": "healthy"}

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container)
        result = await handle_validate_storage_config(args)

        mock_bus.execute.assert_awaited_once()
        fmt.format_success.assert_called_once()
        assert fmt.format_success.call_args[0][0]["status"] == "healthy"
        assert result.data == {"status": "healthy"}


@pytest.mark.unit
class TestHandleStorageHealth:
    """Tests for handle_storage_health."""

    @pytest.mark.asyncio
    async def test_import_error_returns_error_response(self):
        """When GetStorageHealthQuery import fails, format_error is returned."""
        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, strategy=None, verbose=False)

        with patch.dict("sys.modules", {"orb.application.queries.storage": None}):
            result = await handle_storage_health(args)

        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_successful_health_check_returns_config(self):
        """Successful health query result is formatted via format_config."""
        from orb.infrastructure.di.buses import QueryBus

        fmt = _make_formatter()
        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = {"status": "healthy", "details": {}}

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container, strategy="json", verbose=False)

        mock_queries = MagicMock()
        mock_queries.GetStorageHealthQuery = MagicMock(return_value=MagicMock())
        with patch.dict("sys.modules", {"orb.application.queries.storage": mock_queries}):
            result = await handle_storage_health(args)

        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_non_dict_health_result_is_model_dumped(self):
        """When health result has model_dump, it is used."""
        from orb.infrastructure.di.buses import QueryBus

        fmt = _make_formatter()
        health_obj = MagicMock()
        health_obj.model_dump.return_value = {"status": "ok"}

        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = health_obj

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container, strategy=None, verbose=False)

        mock_queries = MagicMock()
        mock_queries.GetStorageHealthQuery = MagicMock(return_value=MagicMock())
        with patch.dict("sys.modules", {"orb.application.queries.storage": mock_queries}):
            result = await handle_storage_health(args)

        fmt.format_config.assert_called_once_with({"status": "ok"})
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleStorageMigrate:
    """Tests for handle_storage_migrate."""

    @pytest.mark.asyncio
    async def test_unknown_subcommand_returns_error(self):
        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, migrate_subcommand="unknown-cmd")
        await handle_storage_migrate(args)

        fmt.format_error.assert_called_once()
        assert "Unknown migrate subcommand" in fmt.format_error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_alembic_not_installed_returns_error(self):
        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, migrate_subcommand="up")

        with patch("importlib.util.find_spec", return_value=None):
            await handle_storage_migrate(args)

        fmt.format_error.assert_called()
        assert "Alembic is not installed" in fmt.format_error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stamp_subcommand_uses_revision(self):
        """stamp subcommand must pass ['stamp', '<revision>'] through to alembic."""
        fmt = _make_formatter()
        fmt.format_success.return_value = InterfaceResponse(data={"message": "ok"})
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, migrate_subcommand="stamp", revision="abc123")

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))

        mock_exec = AsyncMock(return_value=mock_proc)

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("asyncio.create_subprocess_exec", mock_exec),
        ):
            await handle_storage_migrate(args)

        # The alembic subprocess argv must contain the stamp verb and revision.
        mock_exec.assert_awaited_once()
        argv = list(mock_exec.await_args[0])
        assert "alembic" in argv
        # The subcommand-specific args are appended after the "--config" pair.
        assert "stamp" in argv
        assert "abc123" in argv
        assert argv.index("stamp") < argv.index("abc123")

    @pytest.mark.asyncio
    async def test_successful_migration_returns_success(self):
        fmt = _make_formatter()
        fmt.format_success.return_value = InterfaceResponse(data={"message": "Migration complete"})
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, migrate_subcommand="up")

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"INFO  [alembic] Running upgrade", b""))

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            result = await handle_storage_migrate(args)

        fmt.format_success.assert_called()
        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_failed_migration_returns_error(self):
        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container, migrate_subcommand="up")

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"ERROR: migration failed"))

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            result = await handle_storage_migrate(args)

        fmt.format_error.assert_called()
        assert isinstance(result, InterfaceResponse)


@pytest.mark.unit
class TestHandleStorageMetrics:
    """Tests for handle_storage_metrics."""

    @pytest.mark.asyncio
    async def test_import_error_returns_error_response(self):
        """When GetStorageMetricsQuery is missing, format_error is returned."""
        fmt = _make_formatter()
        container = MagicMock()
        container.get.return_value = fmt

        args = Namespace(_container=container)

        with patch.dict("sys.modules", {"orb.application.queries.storage": None}):
            result = await handle_storage_metrics(args)

        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_successful_metrics_returns_config(self):
        from orb.infrastructure.di.buses import QueryBus

        fmt = _make_formatter()
        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = {"total_requests": 42}

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container)

        mock_queries = MagicMock()
        mock_queries.GetStorageMetricsQuery = MagicMock(return_value=MagicMock())
        with patch.dict("sys.modules", {"orb.application.queries.storage": mock_queries}):
            result = await handle_storage_metrics(args)

        assert isinstance(result, InterfaceResponse)

    @pytest.mark.asyncio
    async def test_non_dict_metrics_model_dump_called(self):
        from orb.infrastructure.di.buses import QueryBus

        fmt = _make_formatter()
        metrics_obj = MagicMock()
        metrics_obj.model_dump.return_value = {"metric_a": 1}

        mock_bus = AsyncMock(spec=QueryBus)
        mock_bus.execute.return_value = metrics_obj

        container = MagicMock()
        container.get.side_effect = lambda t: {
            ResponseFormattingService: fmt,
            QueryBus: mock_bus,
        }.get(t, MagicMock())

        args = Namespace(_container=container)

        mock_queries = MagicMock()
        mock_queries.GetStorageMetricsQuery = MagicMock(return_value=MagicMock())
        with patch.dict("sys.modules", {"orb.application.queries.storage": mock_queries}):
            result = await handle_storage_metrics(args)

        fmt.format_config.assert_called_once_with({"metric_a": 1})
        assert isinstance(result, InterfaceResponse)
