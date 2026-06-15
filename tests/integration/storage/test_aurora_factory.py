"""Aurora SQL storage factory tests.

Aurora's storage strategy is SQLAlchemy-based; the strategy and UoW
themselves are exercised by SQL contract tests against sqlite. These
tests verify the Aurora factory wires its connection string and engine
into a working SQLUnitOfWork without contacting AWS.
"""

import pytest


@pytest.mark.integration
class TestAuroraFactory:
    def test_create_aurora_strategy_with_simple_config(self):
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy
        from orb.providers.aws.storage.registration import create_aurora_strategy

        class _Cfg:
            connection_string = "sqlite:///:memory:"

        strategy = create_aurora_strategy(_Cfg())
        assert isinstance(strategy, SQLStorageStrategy)

    def test_create_aurora_unit_of_work_with_dict_config(self):
        from orb.infrastructure.storage.sql.unit_of_work import SQLUnitOfWork
        from orb.providers.aws.storage.registration import create_aurora_unit_of_work

        uow = create_aurora_unit_of_work({"connection_string": "sqlite:///:memory:"})
        assert isinstance(uow, SQLUnitOfWork)
        assert uow.machines is not None
        assert uow.requests is not None
        assert uow.templates is not None
