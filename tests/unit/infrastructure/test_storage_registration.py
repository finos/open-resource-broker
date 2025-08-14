"""Unit tests for Storage Registration modules."""

from unittest.mock import Mock, patch

import pytest

from src.infrastructure.registry.storage_registry import (
    get_storage_registry,
    reset_storage_registry,
)


class TestJSONStorageRegistration:
    """Test JSON storage registration."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_storage_registry()

    def teardown_method(self):
        """Clean up after tests."""
        reset_storage_registry()

    @patch("src.infrastructure.persistence.json.registration.JSONStorageStrategy")
    def test_create_json_strategy(self, mock_strategy_class):
        """Test creating JSON storage strategy."""
        from src.infrastructure.persistence.json.registration import (
            create_json_strategy,
        )

        # Mock configuration
        mock_config = Mock()
        mock_config.json_strategy.base_path = "test_data"
        mock_config.json_strategy.storage_type = "single_file"
        mock_config.json_strategy.filenames = {"single_file": "test.json"}

        mock_strategy = Mock()
        mock_strategy_class.return_value = mock_strategy

        # Test strategy creation
        result = create_json_strategy(mock_config)

        assert result == mock_strategy
        mock_strategy_class.assert_called_once_with(
            file_path="test_data/test.json", create_dirs=True, entity_type="generic"
        )

    @patch("src.config.schemas.storage_schema.JsonStrategyConfig")
    def test_create_json_config(self, mock_config_class):
        """Test creating JSON storage configuration."""
        from src.infrastructure.persistence.json.registration import create_json_config

        mock_config = Mock()
        mock_config_class.return_value = mock_config

        data = {"base_path": "test_data", "storage_type": "single_file"}
        result = create_json_config(data)

        assert result == mock_config
        mock_config_class.assert_called_once_with(**data)

    @patch("src.infrastructure.persistence.repositories.request_repository.RequestRepository")
    @patch("src.infrastructure.persistence.json.registration.JSONStorageStrategy")
    def test_create_json_request_repository(self, mock_strategy_class, mock_repo_class):
        """Test creating JSON request repository."""
        from src.infrastructure.persistence.json.registration import (
            create_json_request_repository,
        )

        mock_config = Mock()
        mock_config.json_strategy.base_path = "test_data"
        mock_config.json_strategy.storage_type = "split_files"
        mock_config.json_strategy.filenames = {
            "split_files": {"requests": "requests.json"}}

        mock_strategy = Mock()
        mock_strategy_class.return_value = mock_strategy
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        result = create_json_request_repository(mock_config)

        assert result == mock_repo
        mock_strategy_class.assert_called_once_with(
            file_path="test_data/requests.json",
            create_dirs=True,
            entity_type="requests",
        )
        mock_repo_class.assert_called_once_with(mock_strategy)

    def test_register_json_storage(self):
        """Test registering JSON storage type."""
        from src.infrastructure.persistence.json.registration import (
            register_json_storage,
        )

        registry = get_storage_registry()

        # Mock the imports to avoid dependency issues
        with patch("src.infrastructure.persistence.json.registration.JSONStorageStrategy"), patch(
            "src.infrastructure.persistence.repositories.request_repository.RequestRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.machine_repository.MachineRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.template_repository.TemplateRepository"
        ), patch(
            "src.infrastructure.persistence.json.registration.JSONUnitOfWork"
        ):

            register_json_storage()

            # Verify registration
            assert registry.is_storage_registered("json")
            assert "json" in registry.get_registered_storage_types()

            # Verify available repositories
            repositories = registry.get_available_repositories("json")
            assert set(repositories) == {"request", "machine", "template"}


class TestSQLStorageRegistration:
    """Test SQL storage registration."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_storage_registry()

    def teardown_method(self):
        """Clean up after tests."""
        reset_storage_registry()

    @patch("src.infrastructure.persistence.sql.registration.SQLStorageStrategy")
    def test_create_sql_strategy(self, mock_strategy_class):
        """Test creating SQL storage strategy."""
        from src.infrastructure.persistence.sql.registration import create_sql_strategy

        # Mock configuration
        mock_config = Mock()
        mock_config.sql_strategy.type = "sqlite"
        mock_config.sql_strategy.name = "test.db"

        mock_strategy = Mock()
        mock_strategy_class.return_value = mock_strategy

        # Test strategy creation
        result = create_sql_strategy(mock_config)

        assert result == mock_strategy
        mock_strategy_class.assert_called_once_with(
            connection_string="sqlite:///test.db",
            table_name="generic_storage",
            columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"},
        )

    @patch("src.config.schemas.storage_schema.SqlStrategyConfig")
    def test_create_sql_config(self, mock_config_class):
        """Test creating SQL storage configuration."""
        from src.infrastructure.persistence.sql.registration import create_sql_config

        mock_config = Mock()
        mock_config_class.return_value = mock_config

        data = {"type": "sqlite", "name": "test.db"}
        result = create_sql_config(data)

        assert result == mock_config
        mock_config_class.assert_called_once_with(**data)

    def test_build_connection_string_sqlite(self):
        """Test building SQLite connection string."""
        from src.infrastructure.persistence.sql.registration import (
            _build_connection_string,
        )

        mock_config = Mock()
        mock_config.type = "sqlite"
        mock_config.name = "test.db"

        result = _build_connection_string(mock_config)
        assert result == "sqlite:///test.db"

    def test_build_connection_string_postgresql(self):
        """Test building PostgreSQL connection string."""
        from src.infrastructure.persistence.sql.registration import (
            _build_connection_string,
        )

        mock_config = Mock()
        mock_config.type = "postgresql"
        mock_config.username = "user"
        mock_config.password = "pass"
        mock_config.host = "localhost"
        mock_config.port = 5432
        mock_config.name = "testdb"

        result = _build_connection_string(mock_config)
        assert result == "postgresql://user:pass@localhost:5432/testdb"

    def test_register_sql_storage(self):
        """Test registering SQL storage type."""
        from src.infrastructure.persistence.sql.registration import register_sql_storage

        registry = get_storage_registry()

        # Mock the imports to avoid dependency issues
        with patch("src.infrastructure.persistence.sql.registration.SQLStorageStrategy"), patch(
            "src.infrastructure.persistence.repositories.request_repository.RequestRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.machine_repository.MachineRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.template_repository.TemplateRepository"
        ), patch(
            "src.infrastructure.persistence.sql.registration.SQLUnitOfWork"
        ):

            register_sql_storage()

            # Verify registration
            assert registry.is_storage_registered("sql")
            assert "sql" in registry.get_registered_storage_types()

            # Verify available repositories
            repositories = registry.get_available_repositories("sql")
            assert set(repositories) == {"request", "machine", "template"}


class TestDynamoDBStorageRegistration:
    """Test DynamoDB storage registration."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_storage_registry()

    def teardown_method(self):
        """Clean up after tests."""
        reset_storage_registry()

    @patch("src.providers.aws.persistence.dynamodb.registration.DynamoDBStorageStrategy")
    def test_create_dynamodb_strategy(self, mock_strategy_class):
        """Test creating DynamoDB storage strategy."""
        from src.providers.aws.persistence.dynamodb.registration import (
            create_dynamodb_strategy,
        )

        # Mock configuration
        mock_config = Mock()
        mock_config.dynamodb_strategy.region = "us-west-2"
        mock_config.dynamodb_strategy.profile = "test-profile"
        mock_config.dynamodb_strategy.table_prefix = "test-prefix"

        mock_strategy = Mock()
        mock_strategy_class.return_value = mock_strategy

        # Test strategy creation
        result = create_dynamodb_strategy(mock_config)

        assert result == mock_strategy
        mock_strategy_class.assert_called_once_with(
            aws_client=None,
            region="us-west-2",
            table_name="test-prefix-generic",
            profile="test-profile",
        )

    @patch("src.config.schemas.storage_schema.DynamodbStrategyConfig")
    def test_create_dynamodb_config(self, mock_config_class):
        """Test creating DynamoDB storage configuration."""
        from src.providers.aws.persistence.dynamodb.registration import (
            create_dynamodb_config,
        )

        mock_config = Mock()
        mock_config_class.return_value = mock_config

        data = {"region": "us-west-2", "profile": "test-profile"}
        result = create_dynamodb_config(data)

        assert result == mock_config
        mock_config_class.assert_called_once_with(**data)

    def test_register_dynamodb_storage(self):
        """Test registering DynamoDB storage type."""
        from src.providers.aws.persistence.dynamodb.registration import (
            register_dynamodb_storage,
        )

        registry = get_storage_registry()

        # Mock the imports to avoid dependency issues
        with patch(
            "src.providers.aws.persistence.dynamodb.registration.DynamoDBStorageStrategy"
        ), patch(
            "src.infrastructure.persistence.repositories.request_repository.RequestRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.machine_repository.MachineRepository"
        ), patch(
            "src.infrastructure.persistence.repositories.template_repository.TemplateRepository"
        ), patch(
            "src.providers.aws.persistence.dynamodb.registration.DynamoDBUnitOfWork"
        ):

            register_dynamodb_storage()

            # Verify registration
            assert registry.is_storage_registered("dynamodb")
            assert "dynamodb" in registry.get_registered_storage_types()

            # Verify available repositories
            repositories = registry.get_available_repositories("dynamodb")
            assert set(repositories) == {"request", "machine", "template"}


class TestCentralStorageRegistration:
    """Test central storage registration."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_storage_registry()

    def teardown_method(self):
        """Clean up after tests."""
        reset_storage_registry()

    @patch("src.infrastructure.persistence.registration.register_json_storage")
    @patch("src.infrastructure.persistence.registration.register_sql_storage")
    @patch("src.infrastructure.persistence.registration.register_dynamodb_storage")
    def test_register_all_storage_types_success(self, mock_dynamodb, mock_sql, mock_json):
        """Test successful registration of all storage types."""
        from src.infrastructure.persistence.registration import (
            register_all_storage_types,
        )

        # Mock successful registrations
        mock_json.return_value = None
        mock_sql.return_value = None
        mock_dynamodb.return_value = None

        # Test registration
        register_all_storage_types()

        # Verify all registration functions were called
        mock_json.assert_called_once()
        mock_sql.assert_called_once()
        mock_dynamodb.assert_called_once()

    @patch("src.infrastructure.persistence.registration.register_json_storage")
    @patch("src.infrastructure.persistence.registration.register_sql_storage")
    @patch("src.infrastructure.persistence.registration.register_dynamodb_storage")
    def test_register_all_storage_types_partial_failure(self, mock_dynamodb, mock_sql, mock_json):
        """Test registration with some failures."""
        from src.infrastructure.persistence.registration import (
            register_all_storage_types,
        )

        # Mock partial failures
        mock_json.return_value = None  # Success
        mock_sql.side_effect = Exception("SQL registration failed")  # Failure
        mock_dynamodb.return_value = None  # Success

        # Test registration (should not raise exception)
        register_all_storage_types()

        # Verify all registration functions were attempted
        mock_json.assert_called_once()
        mock_sql.assert_called_once()
        mock_dynamodb.assert_called_once()

    @patch("src.infrastructure.persistence.registration.register_json_storage")
    @patch("src.infrastructure.persistence.registration.register_sql_storage")
    @patch("src.infrastructure.persistence.registration.register_dynamodb_storage")
    def test_register_all_storage_types_complete_failure(self, mock_dynamodb, mock_sql, mock_json):
        """Test registration with complete failure."""
        from src.infrastructure.persistence.registration import (
            register_all_storage_types,
        )

        # Mock complete failures
        mock_json.side_effect = Exception("JSON registration failed")
        mock_sql.side_effect = Exception("SQL registration failed")
        mock_dynamodb.side_effect = Exception("DynamoDB registration failed")

        # Test registration (should raise exception)
        with pytest.raises(RuntimeError, match="Failed to register any storage types"):
            register_all_storage_types()

    def test_get_available_storage_types(self):
        """Test getting available storage types."""
        from src.infrastructure.persistence.registration import (
            get_available_storage_types,
        )

        # Mock successful imports
        with patch("src.infrastructure.persistence.registration.JSONStorageStrategy"), patch(
            "src.infrastructure.persistence.registration.SQLStorageStrategy"
        ), patch("src.infrastructure.persistence.registration.DynamoDBStorageStrategy"):

            available_types = get_available_storage_types()

            # Should include all types when imports succeed
            assert set(available_types) == {"json", "sql", "dynamodb"}

    def test_is_storage_type_available(self):
        """Test checking if storage type is available."""
        from src.infrastructure.persistence.registration import (
            is_storage_type_available,
        )

        # Mock available types
        with patch(
            "src.infrastructure.persistence.registration.get_available_storage_types"
        ) as mock_get_types:
            mock_get_types.return_value = ["json", "sql"]

            assert is_storage_type_available("json") is True
            assert is_storage_type_available("sql") is True
            assert is_storage_type_available("dynamodb") is False
            assert is_storage_type_available("unknown") is False

    @patch("src.infrastructure.persistence.registration.register_json_storage")
    def test_register_storage_type_success(self, mock_register):
        """Test successful registration of specific storage type."""
        from src.infrastructure.persistence.registration import register_storage_type

        mock_register.return_value = None

        result = register_storage_type("json")

        assert result is True
        mock_register.assert_called_once()

    @patch("src.infrastructure.persistence.registration.register_json_storage")
    def test_register_storage_type_failure(self, mock_register):
        """Test failed registration of specific storage type."""
        from src.infrastructure.persistence.registration import register_storage_type

        mock_register.side_effect = Exception("Registration failed")

        result = register_storage_type("json")

        assert result is False
        mock_register.assert_called_once()

    def test_register_storage_type_unknown(self):
        """Test registration of unknown storage type."""
        from src.infrastructure.persistence.registration import register_storage_type

        result = register_storage_type("unknown")

        assert result is False
