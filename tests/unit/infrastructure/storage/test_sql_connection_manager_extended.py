"""Extended unit tests for SQLConnectionManager (components/sql_connection_manager.py)."""

from unittest.mock import MagicMock, patch

import pytest


def _make_manager(config=None):
    from orb.infrastructure.storage.components.sql_connection_manager import (
        SQLConnectionManager,
    )

    if config is None:
        config = {"type": "sqlite", "name": ":memory:"}
    return SQLConnectionManager(config)


# ---------------------------------------------------------------------------
# initialize / _initialize_engine paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerInit:
    def test_sqlite_memory_initializes(self) -> None:
        mgr = _make_manager({"type": "sqlite", "name": ":memory:"})
        assert mgr.engine is not None
        assert mgr.session_factory is not None

    def test_initialize_idempotent(self) -> None:
        mgr = _make_manager()
        engine_before = mgr.engine
        mgr.initialize()  # second call — already initialized
        assert mgr.engine is engine_before

    def test_unsupported_db_type_raises(self) -> None:
        from orb.infrastructure.storage.components.sql_connection_manager import (
            SQLConnectionManager,
        )

        with pytest.raises(Exception):
            SQLConnectionManager({"type": "oracle"})


# ---------------------------------------------------------------------------
# get_connection_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerConnectionInfo:
    def test_info_contains_expected_keys(self) -> None:
        mgr = _make_manager()
        info = mgr.get_connection_info()
        assert "type" in info
        assert "database_type" in info
        assert "initialized" in info

    def test_info_when_initialized(self) -> None:
        mgr = _make_manager()
        info = mgr.get_connection_info()
        assert info["initialized"] is True

    def test_info_includes_pool_attributes(self) -> None:
        mgr = _make_manager()
        info = mgr.get_connection_info()
        # pool_size is present (even as N/A for sqlite's StaticPool)
        assert "pool_size" in info


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerIsHealthy:
    def test_healthy_with_real_engine(self) -> None:
        mgr = _make_manager()
        assert mgr.is_healthy() is True

    def test_unhealthy_when_engine_is_none(self) -> None:
        mgr = _make_manager()
        mgr.engine = None
        assert mgr.is_healthy() is False

    def test_unhealthy_when_execute_raises(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr, "get_connection") as mock_conn_ctx:
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = RuntimeError("no db")
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)
            result = mgr.is_healthy()
        assert result is False


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerGetSession:
    def test_get_session_yields_session(self) -> None:
        mgr = _make_manager()
        with mgr.get_session() as session:
            assert session is not None

    def test_get_session_raises_when_not_initialized(self) -> None:
        mgr = _make_manager()
        mgr.session_factory = None
        with pytest.raises(RuntimeError, match="not initialized"):
            with mgr.get_session():
                pass

    def test_get_session_rollback_on_error(self) -> None:
        mgr = _make_manager()
        with pytest.raises(RuntimeError, match="session bomb"):
            with mgr.get_session():
                raise RuntimeError("session bomb")


# ---------------------------------------------------------------------------
# get_connection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerGetConnection:
    def test_get_connection_yields_connection(self) -> None:
        mgr = _make_manager()
        with mgr.get_connection() as conn:
            assert conn is not None

    def test_get_connection_raises_when_engine_none(self) -> None:
        mgr = _make_manager()
        mgr.engine = None
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            with mgr.get_connection():
                pass


# ---------------------------------------------------------------------------
# execute_query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerExecuteQuery:
    def test_execute_select_returns_rows(self) -> None:
        mgr = _make_manager()
        rows = mgr.execute_query("SELECT 1 AS val")
        assert rows is not None

    def test_execute_ddl_returns_none(self) -> None:
        mgr = _make_manager()
        result = mgr.execute_query("CREATE TABLE IF NOT EXISTS eq_test (id TEXT PRIMARY KEY)")
        assert result is None


# ---------------------------------------------------------------------------
# table_exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerTableExists:
    def test_returns_true_for_existing_table(self) -> None:
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        strategy = SQLStorageStrategy(
            config={"type": "sqlite", "name": ":memory:"},
            table_name="te_check",
            columns={"id": "TEXT PRIMARY KEY"},
        )
        assert strategy.connection_manager.table_exists("te_check") is True

    def test_returns_false_for_missing_table(self) -> None:
        mgr = _make_manager()
        assert mgr.table_exists("nonexistent_xyz") is False

    def test_returns_false_on_exception(self) -> None:
        mgr = _make_manager()
        with patch.object(mgr, "get_connection", side_effect=RuntimeError("boom")):
            result = mgr.table_exists("any")
        assert result is False

    def test_unsupported_db_type_returns_false(self) -> None:
        mgr = _make_manager()
        mgr.config = {"type": "cassandra"}  # unsupported type for table_exists
        result = mgr.table_exists("any")
        assert result is False


# ---------------------------------------------------------------------------
# get_engine
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerGetEngine:
    def test_returns_engine_when_initialized(self) -> None:
        mgr = _make_manager()
        engine = mgr.get_engine()
        assert engine is not None

    def test_raises_when_engine_none(self) -> None:
        mgr = _make_manager()
        mgr.engine = None
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            mgr.get_engine()


# ---------------------------------------------------------------------------
# cleanup / close
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerCleanup:
    def test_cleanup_disposes_engine(self) -> None:
        mgr = _make_manager()
        engine_mock = MagicMock()
        mgr.engine = engine_mock
        mgr.cleanup()
        engine_mock.dispose.assert_called_once()

    def test_cleanup_sets_not_initialized(self) -> None:
        mgr = _make_manager()
        mgr.cleanup()
        assert mgr._initialized is False

    def test_close_is_alias_for_cleanup(self) -> None:
        mgr = _make_manager()
        engine_mock = MagicMock()
        mgr.engine = engine_mock
        mgr.close()
        engine_mock.dispose.assert_called_once()

    def test_cleanup_without_engine_does_not_raise(self) -> None:
        mgr = _make_manager()
        mgr.engine = None
        mgr.cleanup()  # should not raise


# ---------------------------------------------------------------------------
# get_resource
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLConnectionManagerGetResource:
    def test_get_engine_resource(self) -> None:
        mgr = _make_manager()
        assert mgr.get_resource("engine") is mgr.engine

    def test_get_session_factory_resource(self) -> None:
        mgr = _make_manager()
        assert mgr.get_resource("session_factory") is mgr.session_factory

    def test_unknown_resource_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(KeyError, match="Unknown resource"):
            mgr.get_resource("nonexistent")
