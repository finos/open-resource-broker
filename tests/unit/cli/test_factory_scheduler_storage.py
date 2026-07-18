"""Unit tests for scheduler and storage command factories.

Covers:
  - orb.cli.factories.scheduler_command_factory.SchedulerCommandFactory
  - orb.cli.factories.storage_command_factory.StorageCommandFactory
"""

from __future__ import annotations

import pytest

from orb.cli.factories.scheduler_command_factory import SchedulerCommandFactory
from orb.cli.factories.storage_command_factory import StorageCommandFactory

# ---------------------------------------------------------------------------
# SchedulerCommandFactory
# ---------------------------------------------------------------------------


@pytest.fixture
def scheduler_factory() -> SchedulerCommandFactory:
    return SchedulerCommandFactory()


@pytest.mark.unit
class TestSchedulerListStrategies:
    def test_defaults(self, scheduler_factory):
        q = scheduler_factory.create_list_scheduler_strategies_query()
        assert q.include_current is True
        assert q.include_details is False
        assert q.filter_expressions == []

    def test_include_details(self, scheduler_factory):
        q = scheduler_factory.create_list_scheduler_strategies_query(include_details=True)
        assert q.include_details is True

    def test_filter_expressions_none_normalised(self, scheduler_factory):
        q = scheduler_factory.create_list_scheduler_strategies_query(filter_expressions=None)
        assert q.filter_expressions == []

    def test_filter_expressions_set(self, scheduler_factory):
        exprs = ["strategy=default"]
        q = scheduler_factory.create_list_scheduler_strategies_query(filter_expressions=exprs)
        assert q.filter_expressions == exprs


@pytest.mark.unit
class TestSchedulerGetConfiguration:
    def test_defaults(self, scheduler_factory):
        q = scheduler_factory.create_get_scheduler_configuration_query()
        assert q.scheduler_name is None

    def test_scheduler_name_set(self, scheduler_factory):
        q = scheduler_factory.create_get_scheduler_configuration_query(scheduler_name="hostfactory")
        assert q.scheduler_name == "hostfactory"


@pytest.mark.unit
class TestSchedulerValidateConfiguration:
    def test_defaults(self, scheduler_factory):
        q = scheduler_factory.create_validate_scheduler_configuration_query()
        assert q.scheduler_name is None

    def test_scheduler_name_set(self, scheduler_factory):
        q = scheduler_factory.create_validate_scheduler_configuration_query(
            scheduler_name="default"
        )
        assert q.scheduler_name == "default"


# ---------------------------------------------------------------------------
# StorageCommandFactory
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_factory() -> StorageCommandFactory:
    return StorageCommandFactory()


@pytest.mark.unit
class TestStorageListStrategies:
    def test_defaults(self, storage_factory):
        q = storage_factory.create_list_storage_strategies_query()
        assert q.include_current is True
        assert q.include_details is False
        assert q.filter_expressions == []

    def test_filter_expressions_none_normalised(self, storage_factory):
        q = storage_factory.create_list_storage_strategies_query(filter_expressions=None)
        assert q.filter_expressions == []

    def test_include_details(self, storage_factory):
        q = storage_factory.create_list_storage_strategies_query(include_details=True)
        assert q.include_details is True


@pytest.mark.unit
class TestStorageGetHealthQuery:
    def test_defaults(self, storage_factory):
        q = storage_factory.create_get_storage_health_query()
        assert q.strategy_name is None
        assert q.verbose is False

    def test_strategy_name_set(self, storage_factory):
        q = storage_factory.create_get_storage_health_query(strategy_name="dynamodb")
        assert q.strategy_name == "dynamodb"

    def test_verbose_true(self, storage_factory):
        q = storage_factory.create_get_storage_health_query(verbose=True)
        assert q.verbose is True


@pytest.mark.unit
class TestStorageGetMetricsQuery:
    def test_defaults(self, storage_factory):
        q = storage_factory.create_get_storage_metrics_query()
        assert q.strategy_name is None
        assert q.time_range == "1h"
        assert q.include_operations is True

    def test_strategy_name_set(self, storage_factory):
        q = storage_factory.create_get_storage_metrics_query(strategy_name="sql")
        assert q.strategy_name == "sql"

    def test_time_range_set(self, storage_factory):
        q = storage_factory.create_get_storage_metrics_query(time_range="24h")
        assert q.time_range == "24h"

    def test_include_operations_false(self, storage_factory):
        q = storage_factory.create_get_storage_metrics_query(include_operations=False)
        assert q.include_operations is False
