"""Tests for RepositoryQueryError and count_by_column raise behaviour (T12).

Coverage:
  1. RepositoryQueryError is defined in storage exceptions and is a RuntimeError.
  2. SQLStorageStrategy.count_by_column raises RepositoryQueryError (not silently
     returns {}) when the underlying query fails with a SQLAlchemyError.
  3. DashboardSummaryOrchestrator catches RepositoryQueryError from count_by_*
     calls, logs at WARNING, and continues with empty counts (graceful degradation).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from orb.application.ports.exceptions import RepositoryQueryError
from orb.infrastructure.storage.exceptions import StorageError

# ---------------------------------------------------------------------------
# 1.  RepositoryQueryError class contract
# ---------------------------------------------------------------------------


class TestRepositoryQueryErrorClass:
    def test_is_runtime_error(self):
        """RepositoryQueryError must be a RuntimeError subclass."""
        assert issubclass(RepositoryQueryError, RuntimeError)

    def test_is_not_storage_error(self):
        """RepositoryQueryError must NOT inherit from StorageError."""
        assert not issubclass(RepositoryQueryError, StorageError)

    def test_message_is_preserved(self):
        """The error message passed to the constructor must be accessible."""
        exc = RepositoryQueryError("query failed")
        assert "query failed" in str(exc)

    def test_cause_chain_preserved(self):
        """When raised with 'from', the __cause__ chain must be intact."""
        original = OperationalError("original", None, None)
        try:
            raise RepositoryQueryError("wrapped") from original
        except RepositoryQueryError as caught:
            assert caught.__cause__ is original


# ---------------------------------------------------------------------------
# 2.  SQLStorageStrategy.count_by_column raises RepositoryQueryError
# ---------------------------------------------------------------------------


class TestCountByColumnRaises:
    """count_by_column must raise RepositoryQueryError on SQLAlchemy errors."""

    def _make_strategy_with_failing_session(self, sqla_error: Exception):
        """Return a SQLStorageStrategy whose session.execute() raises sqla_error."""
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        with patch.object(SQLStorageStrategy, "_initialize_table"):
            strategy = SQLStorageStrategy.__new__(SQLStorageStrategy)

        strategy.table_name = "machines"
        strategy.columns = {
            "machine_id": "VARCHAR(255) PRIMARY KEY",
            "status": "VARCHAR(50)",
            "provider_api": "VARCHAR(255)",
        }

        import logging

        strategy.logger = logging.getLogger("test.count_by_column")

        # Mock connection manager whose session raises the error.
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.side_effect = sqla_error

        mock_cm = MagicMock()
        mock_cm.get_session.return_value = mock_session
        strategy.connection_manager = mock_cm

        # Simple lock manager that yields immediately.
        from orb.infrastructure.storage.components import LockManager

        strategy.lock_manager = LockManager("simple")

        return strategy

    def test_raises_repository_query_error_on_sqla_error(self):
        """A SQLAlchemy OperationalError must be re-raised as RepositoryQueryError."""
        sqla_err = OperationalError("no such table: machines", None, None)
        strategy = self._make_strategy_with_failing_session(sqla_err)

        with pytest.raises(RepositoryQueryError) as exc_info:
            strategy.count_by_column("status")

        assert "no such table" in str(exc_info.value)

    def test_cause_is_original_sqla_error(self):
        """The RepositoryQueryError's __cause__ must be the original SQLAlchemy error."""
        sqla_err = OperationalError("disk I/O error", None, None)
        strategy = self._make_strategy_with_failing_session(sqla_err)

        with pytest.raises(RepositoryQueryError) as exc_info:
            strategy.count_by_column("status")

        assert exc_info.value.__cause__ is sqla_err

    def test_non_sqla_exception_propagates_unchanged(self):
        """A non-SQLAlchemy exception must NOT be wrapped; it propagates as-is."""
        strategy = self._make_strategy_with_failing_session(RuntimeError("unexpected"))

        # Should raise the original RuntimeError, not RepositoryQueryError.
        with pytest.raises(RuntimeError) as exc_info:
            strategy.count_by_column("status")

        assert type(exc_info.value) is RuntimeError

    def test_unknown_column_raises_storage_error_not_query_error(self):
        """Passing an unregistered column name must raise StorageError (not query error)."""
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        with patch.object(SQLStorageStrategy, "_initialize_table"):
            strategy = SQLStorageStrategy.__new__(SQLStorageStrategy)

        strategy.table_name = "machines"
        strategy.columns = {"machine_id": "VARCHAR(255) PRIMARY KEY"}
        strategy.lock_manager = MagicMock()
        strategy.connection_manager = MagicMock()

        import logging

        strategy.logger = logging.getLogger("test.count_by_column")

        with pytest.raises(StorageError):
            strategy.count_by_column("nonexistent_column")


# ---------------------------------------------------------------------------
# 3.  DashboardSummaryOrchestrator catches RepositoryQueryError
# ---------------------------------------------------------------------------


class TestDashboardSummaryCatchesRepositoryQueryError:
    """DashboardSummaryOrchestrator must catch RepositoryQueryError and degrade."""

    def _make_orchestrator(self, failing_method: str):
        """Return a DashboardSummaryOrchestrator wired with a UoW whose
        *failing_method* raises RepositoryQueryError."""
        from orb.application.services.orchestration.dashboard_summary import (
            DashboardSummaryOrchestrator,
        )

        # Build repository mocks.
        mock_machines = MagicMock()
        mock_requests = MagicMock()
        mock_templates = MagicMock()

        # Default: all count methods return empty dicts.
        mock_machines.count_by_status.return_value = {}
        mock_requests.count_by_status.return_value = {}
        mock_templates.count_by_provider_api.return_value = {}

        # Default: list_recent_activity returns empty list.
        mock_requests.list_recent_activity.return_value = []

        # Make the specified method raise.
        target_repo, method = failing_method.split(".")
        target = {
            "machines": mock_machines,
            "requests": mock_requests,
            "templates": mock_templates,
        }[target_repo]
        getattr(target, method).side_effect = RepositoryQueryError("db error")

        mock_uow = MagicMock()
        mock_uow.machines = mock_machines
        mock_uow.requests = mock_requests
        mock_uow.templates = mock_templates
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)

        mock_uow_factory = MagicMock()
        mock_uow_factory.create_unit_of_work.return_value = mock_uow

        logger = MagicMock()
        logger.info = MagicMock()
        logger.warning = MagicMock()

        mock_provider_registry = MagicMock()
        mock_provider_registry.list_all_provider_apis.return_value = []

        orchestrator = DashboardSummaryOrchestrator(
            uow_factory=mock_uow_factory,
            logger=logger,
            provider_registry=mock_provider_registry,
        )
        return orchestrator, logger

    @pytest.mark.asyncio
    async def test_machines_count_error_returns_zero_total(self):
        """RepositoryQueryError from machines.count_by_status must yield total=0."""
        from orb.application.services.orchestration.dtos import DashboardSummaryInput

        orchestrator, _ = self._make_orchestrator("machines.count_by_status")
        result = await orchestrator.execute(DashboardSummaryInput())
        assert result.machines["total"] == 0

    @pytest.mark.asyncio
    async def test_requests_count_error_returns_zero_total(self):
        """RepositoryQueryError from requests.count_by_status must yield total=0."""
        from orb.application.services.orchestration.dtos import DashboardSummaryInput

        orchestrator, _ = self._make_orchestrator("requests.count_by_status")
        result = await orchestrator.execute(DashboardSummaryInput())
        assert result.requests["total"] == 0

    @pytest.mark.asyncio
    async def test_templates_count_error_returns_zero_total(self):
        """RepositoryQueryError from templates.count_by_provider_api must yield total=0."""
        from orb.application.services.orchestration.dtos import DashboardSummaryInput

        orchestrator, _ = self._make_orchestrator("templates.count_by_provider_api")
        result = await orchestrator.execute(DashboardSummaryInput())
        assert result.templates["total"] == 0

    @pytest.mark.asyncio
    async def test_warning_is_logged_on_error(self):
        """A WARNING must be logged when count_by_status fails."""
        from orb.application.services.orchestration.dtos import DashboardSummaryInput

        orchestrator, logger = self._make_orchestrator("machines.count_by_status")
        await orchestrator.execute(DashboardSummaryInput())
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_other_sections_unaffected_when_machines_fails(self):
        """When machines count fails the requests and templates sections must still
        contain their expected keys."""
        from orb.application.services.orchestration.dtos import DashboardSummaryInput

        orchestrator, _ = self._make_orchestrator("machines.count_by_status")
        result = await orchestrator.execute(DashboardSummaryInput())

        # requests and templates sections should still have the expected keys
        assert "total" in result.requests
        assert "total" in result.templates
