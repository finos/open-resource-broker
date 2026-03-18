"""Tests for SQLConnectionManager wiring DatabaseConfig values."""

import pytest


@pytest.mark.unit
class TestSQLConnectionManagerConnectionString:
    """SQLConnectionManager must use connection_string directly when present."""

    def test_connection_string_fast_path(self):
        from orb.infrastructure.storage.components.sql_connection_manager import (
            SQLConnectionManager,
        )

        config = {"connection_string": "sqlite:///test.db"}
        manager = SQLConnectionManager(config)

        assert manager.engine is not None
        assert "sqlite" in str(manager.engine.url)

    def test_connection_string_skips_type_name_reconstruction(self):
        from orb.infrastructure.storage.components.sql_connection_manager import (
            SQLConnectionManager,
        )

        # If connection_string is present, type/name should be ignored
        config = {
            "connection_string": "sqlite:///fast_path.db",
            "type": "postgresql",  # Should be ignored
            "name": "ignored.db",  # Should be ignored
        }
        manager = SQLConnectionManager(config)

        assert manager.engine is not None
        # Verify it used the connection_string, not type+name
        assert "fast_path.db" in str(manager.engine.url)
        assert "postgresql" not in str(manager.engine.url)


@pytest.mark.unit
class TestSQLConnectionManagerTypeNamePath:
    """SQLConnectionManager must read type and name when connection_string absent."""

    def test_sqlite_type_with_name(self):
        from orb.infrastructure.storage.components.sql_connection_manager import (
            SQLConnectionManager,
        )

        config = {"type": "sqlite", "name": "database.db"}
        manager = SQLConnectionManager(config)

        assert manager.engine is not None
        assert "sqlite" in str(manager.engine.url)
        assert "database.db" in str(manager.engine.url)


@pytest.mark.unit
class TestSQLConnectionManagerConnectionTimeout:
    """SQLConnectionManager must wire connection_timeout to sqlite connect_args."""

    def test_connection_timeout_wired_to_sqlite(self):
        from unittest.mock import patch

        from orb.infrastructure.storage.components.sql_connection_manager import (
            SQLConnectionManager,
        )

        captured = {}

        original_create_engine = __import__("sqlalchemy").create_engine

        def mock_create_engine(url, **kwargs):
            captured["connect_args"] = kwargs.get("connect_args", {})
            return original_create_engine(url, **kwargs)

        config = {
            "type": "sqlite",
            "name": "test.db",
            "connection_timeout": 15,
        }

        with patch(
            "orb.infrastructure.storage.components.sql_connection_manager.create_engine",
            side_effect=mock_create_engine,
        ):
            manager = SQLConnectionManager(config)

        assert manager.engine is not None
        assert captured["connect_args"].get("timeout") == 15

    def test_connection_timeout_defaults_to_30_if_missing(self):
        from unittest.mock import patch

        from orb.infrastructure.storage.components.sql_connection_manager import (
            SQLConnectionManager,
        )

        captured = {}

        original_create_engine = __import__("sqlalchemy").create_engine

        def mock_create_engine(url, **kwargs):
            captured["connect_args"] = kwargs.get("connect_args", {})
            return original_create_engine(url, **kwargs)

        config = {"type": "sqlite", "name": "test.db"}

        with patch(
            "orb.infrastructure.storage.components.sql_connection_manager.create_engine",
            side_effect=mock_create_engine,
        ):
            manager = SQLConnectionManager(config)

        assert manager.engine is not None
        assert captured["connect_args"].get("timeout") == 30
