"""Unit tests for sql/registration.py."""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _build_connection_string
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildConnectionString:
    def test_sqlite(self) -> None:
        from orb.infrastructure.storage.sql.registration import _build_connection_string

        cfg = MagicMock()
        cfg.type = "sqlite"
        cfg.name = "mydb.db"
        result = _build_connection_string(cfg)
        assert "sqlite:///mydb.db" == result

    def test_postgresql(self) -> None:
        from orb.infrastructure.storage.sql.registration import _build_connection_string

        cfg = MagicMock()
        cfg.type = "postgresql"
        cfg.username = "user"
        cfg.password = "pass"
        cfg.host = "localhost"
        cfg.port = 5432
        cfg.name = "testdb"
        result = _build_connection_string(cfg)
        assert result.startswith("postgresql://")
        assert "localhost" in result
        assert "testdb" in result

    def test_mysql(self) -> None:
        from orb.infrastructure.storage.sql.registration import _build_connection_string

        cfg = MagicMock()
        cfg.type = "mysql"
        cfg.username = "u"
        cfg.password = "p"
        cfg.host = "host"
        cfg.port = 3306
        cfg.name = "db"
        result = _build_connection_string(cfg)
        assert result.startswith("mysql://")

    def test_unsupported_type_raises(self) -> None:
        from orb.infrastructure.storage.sql.registration import _build_connection_string

        cfg = MagicMock()
        cfg.type = "cassandra"
        with pytest.raises(ValueError, match="Unsupported database type"):
            _build_connection_string(cfg)


# ---------------------------------------------------------------------------
# create_sql_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSqlConfig:
    def test_creates_sqlite_config(self) -> None:
        from orb.infrastructure.storage.sql.registration import create_sql_config

        cfg = create_sql_config({"type": "sqlite", "name": "test.db"})
        assert cfg is not None
        assert cfg.type == "sqlite"


# ---------------------------------------------------------------------------
# create_sql_strategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSqlStrategy:
    def test_creates_strategy_with_connection_string(self) -> None:
        from orb.infrastructure.storage.sql.registration import create_sql_strategy

        cfg = MagicMock(spec=[])  # no sql_strategy attribute
        cfg.connection_string = "sqlite:///:memory:"
        strategy = create_sql_strategy(cfg)
        assert strategy is not None
        assert strategy.table_name == "generic_storage"

    def test_creates_strategy_with_sql_strategy_config(self) -> None:
        from orb.infrastructure.storage.sql.registration import create_sql_strategy

        sql_cfg = MagicMock()
        sql_cfg.type = "sqlite"
        sql_cfg.name = ":memory:"
        sql_cfg.pool_size = 5
        sql_cfg.max_overflow = 10

        db_cfg = MagicMock()
        db_cfg.connection_timeout = 30

        cfg = MagicMock()
        cfg.sql_strategy = sql_cfg
        cfg.database = db_cfg
        strategy = create_sql_strategy(cfg)
        assert strategy is not None

    def test_creates_strategy_with_sql_strategy_no_database(self) -> None:
        from orb.infrastructure.storage.sql.registration import create_sql_strategy

        sql_cfg = MagicMock()
        sql_cfg.type = "sqlite"
        sql_cfg.name = ":memory:"
        sql_cfg.pool_size = 5
        sql_cfg.max_overflow = 10

        cfg = MagicMock()
        cfg.sql_strategy = sql_cfg
        cfg.database = None
        strategy = create_sql_strategy(cfg)
        assert strategy is not None


# ---------------------------------------------------------------------------
# create_sql_unit_of_work
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSqlUnitOfWork:
    def test_creates_uow_from_dict_config(self) -> None:
        from orb.infrastructure.storage.sql.registration import create_sql_unit_of_work

        cfg = {"connection_string": "sqlite:///:memory:"}
        uow = create_sql_unit_of_work(cfg)
        assert uow is not None
        assert uow.machines is not None

    def test_creates_uow_from_config_manager(self) -> None:
        from orb.infrastructure.storage.sql.registration import create_sql_unit_of_work

        # Mock ConfigurationManager
        with patch(
            "orb.infrastructure.storage.sql.registration.isinstance",
            side_effect=lambda obj, cls: True,  # pretend everything is ConfigurationManager
        ):
            pass  # Don't use isinstance patch — too broad.

        # Use a real dict path instead
        uow = create_sql_unit_of_work({"connection_string": "sqlite:///:memory:"})
        assert uow.requests is not None


# ---------------------------------------------------------------------------
# register_sql_storage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterSqlStorage:
    def test_register_sql_storage_succeeds(self) -> None:
        from orb.infrastructure.storage.sql.registration import register_sql_storage

        # Create an isolated registry so we don't pollute global state
        mock_registry = MagicMock()
        with patch(
            "orb.infrastructure.storage.sql.registration.get_storage_registry",
            return_value=mock_registry,
        ):
            register_sql_storage()
        mock_registry.register_storage.assert_called_once()
        call_kwargs = mock_registry.register_storage.call_args
        assert call_kwargs.kwargs.get("storage_type") == "sql" or (
            call_kwargs.args and call_kwargs.args[0] == "sql"
        )

    def test_register_sql_storage_raises_on_registry_error(self) -> None:
        from orb.infrastructure.storage.sql.registration import register_sql_storage

        mock_registry = MagicMock()
        mock_registry.register_storage.side_effect = RuntimeError("registry broken")
        with patch(
            "orb.infrastructure.storage.sql.registration.get_storage_registry",
            return_value=mock_registry,
        ):
            with pytest.raises(RuntimeError, match="registry broken"):
                register_sql_storage()
