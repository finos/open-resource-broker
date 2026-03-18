"""Tests for DatabaseConfig moving into StorageConfig."""

import importlib.resources
import json

import pytest

from orb.config.schemas.common_schema import DatabaseConfig
from orb.config.schemas.storage_schema import StorageConfig


@pytest.mark.unit
class TestStorageConfigHasDatabaseField:
    """StorageConfig must carry a database field of type DatabaseConfig."""

    def test_storage_config_has_database_attribute(self):
        config = StorageConfig()  # type: ignore[call-arg]
        assert hasattr(config, "database")

    def test_database_field_is_database_config_instance(self):
        config = StorageConfig()  # type: ignore[call-arg]
        assert isinstance(config.database, DatabaseConfig)

    def test_database_defaults_connection_timeout(self):
        config = StorageConfig()  # type: ignore[call-arg]
        assert config.database.connection_timeout == 30

    def test_database_defaults_query_timeout(self):
        config = StorageConfig()  # type: ignore[call-arg]
        assert config.database.query_timeout == 60

    def test_database_defaults_max_connections(self):
        config = StorageConfig()  # type: ignore[call-arg]
        assert config.database.max_connections == 10

    def test_database_values_can_be_overridden(self):
        config = StorageConfig(  # type: ignore[call-arg]
            database=DatabaseConfig(  # type: ignore[call-arg]
                connection_timeout=5,
                query_timeout=15,
                max_connections=3,
            )
        )
        assert config.database.connection_timeout == 5
        assert config.database.query_timeout == 15
        assert config.database.max_connections == 3


@pytest.mark.unit
class TestAppConfigNoDatabaseField:
    """AppConfig must NOT have a top-level database field."""

    def test_app_config_has_no_database_field(self):
        from orb.config.schemas.app_schema import AppConfig

        assert (
            not hasattr(AppConfig.model_fields, "database")
            or "database" not in AppConfig.model_fields
        )


@pytest.mark.unit
class TestDefaultConfigJsonStorageDatabase:
    """default_config.json must have database nested under storage, not at top level."""

    def _load(self) -> dict:
        resource = importlib.resources.files("orb.config").joinpath("default_config.json")
        return json.loads(resource.read_text(encoding="utf-8"))

    def test_no_top_level_database_key(self):
        data = self._load()
        assert "database" not in data, "database must not be a top-level key"

    def test_storage_has_database_key(self):
        data = self._load()
        assert "database" in data["storage"], "storage must contain a database key"

    def test_storage_database_connection_timeout(self):
        data = self._load()
        assert data["storage"]["database"]["connection_timeout"] == 30

    def test_storage_database_query_timeout(self):
        data = self._load()
        assert data["storage"]["database"]["query_timeout"] == 60

    def test_storage_database_max_connections(self):
        data = self._load()
        assert data["storage"]["database"]["max_connections"] == 10

    def test_full_config_loads_via_app_config(self):
        from orb.config.schemas.app_schema import AppConfig

        data = self._load()
        config = AppConfig(**data)
        assert config.storage.database.connection_timeout == 30
        assert config.storage.database.query_timeout == 60
        assert config.storage.database.max_connections == 10
