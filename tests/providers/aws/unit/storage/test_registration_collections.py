"""Tests for naming.collections integration in DynamoDB registration."""

from unittest.mock import MagicMock, patch

from orb.config.manager import ConfigurationManager
from orb.config.schemas.common_schema import NamingConfig
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.storage.config import AWSStorageConfig, DynamodbStrategyConfig
from orb.providers.aws.storage.registration import create_dynamodb_unit_of_work


def _make_config_manager_with_collections(
    collections: dict[str, str], table_prefix: str = "myapp"
) -> MagicMock:
    """Create a mock ConfigurationManager with custom collections config."""
    naming_config = NamingConfig(collections=collections)  # type: ignore[call-arg]

    # app_config is a plain namespace — only .naming.collections is accessed
    app_config = MagicMock()
    app_config.naming = naming_config

    # Create DynamoDB config
    dynamodb_cfg = DynamodbStrategyConfig(
        region="us-east-1", profile="default", table_prefix=table_prefix
    )

    # Create AWS provider config
    aws_cfg = AWSProviderConfig(  # type: ignore[call-arg]
        region="us-east-1",
        storage=AWSStorageConfig(dynamodb=dynamodb_cfg),  # type: ignore[call-arg]
    )

    # Create mock ConfigurationManager
    mock_cm = MagicMock(spec=ConfigurationManager)
    mock_cm.get_typed.return_value = aws_cfg
    mock_cm.app_config = app_config

    return mock_cm


class TestDynamoDBCollectionsNaming:
    """Test that naming.collections config is used for DynamoDB table names."""

    def test_custom_collection_names_produce_correct_table_names(self) -> None:
        """Custom collection names (hosts, jobs, specs) produce correct table names."""
        custom_collections = {
            "machines": "hosts",
            "requests": "jobs",
            "templates": "specs",
        }
        config = _make_config_manager_with_collections(custom_collections, "prod")

        captured: dict[str, str] = {}

        def fake_uow_init(self: object, **kwargs: object) -> None:
            captured.update(kwargs)  # type: ignore[arg-type]

        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()

        with (
            patch(
                "orb.providers.aws.session_factory.AWSSessionFactory.create_session",
                return_value=mock_session,
            ),
            patch(
                "orb.providers.aws.storage.unit_of_work.DynamoDBUnitOfWork.__init__",
                fake_uow_init,
            ),
        ):
            create_dynamodb_unit_of_work(config)

        assert captured["machine_table"] == "prod-hosts"
        assert captured["request_table"] == "prod-jobs"
        assert captured["template_table"] == "prod-specs"

    def test_default_collection_names_produce_same_result_as_hardcoded(self) -> None:
        """Default collection names produce same result as old hardcoded behaviour."""
        default_collections = {
            "machines": "machines",
            "requests": "requests",
            "templates": "templates",
        }
        config = _make_config_manager_with_collections(default_collections, "hf")

        captured: dict[str, str] = {}

        def fake_uow_init(self: object, **kwargs: object) -> None:
            captured.update(kwargs)  # type: ignore[arg-type]

        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()

        with (
            patch(
                "orb.providers.aws.session_factory.AWSSessionFactory.create_session",
                return_value=mock_session,
            ),
            patch(
                "orb.providers.aws.storage.unit_of_work.DynamoDBUnitOfWork.__init__",
                fake_uow_init,
            ),
        ):
            create_dynamodb_unit_of_work(config)

        # Should match old hardcoded behavior
        assert captured["machine_table"] == "hf-machines"
        assert captured["request_table"] == "hf-requests"
        assert captured["template_table"] == "hf-templates"

    def test_partial_collections_dict_falls_back_to_hardcoded_suffix(self) -> None:
        """Partial collections dict (missing keys) falls back to hardcoded suffix."""
        partial_collections = {
            "machines": "hosts",
            # "requests" missing
            "templates": "specs",
        }
        config = _make_config_manager_with_collections(partial_collections, "test")

        captured: dict[str, str] = {}

        def fake_uow_init(self: object, **kwargs: object) -> None:
            captured.update(kwargs)  # type: ignore[arg-type]

        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()

        with (
            patch(
                "orb.providers.aws.session_factory.AWSSessionFactory.create_session",
                return_value=mock_session,
            ),
            patch(
                "orb.providers.aws.storage.unit_of_work.DynamoDBUnitOfWork.__init__",
                fake_uow_init,
            ),
        ):
            create_dynamodb_unit_of_work(config)

        # Custom names used where provided
        assert captured["machine_table"] == "test-hosts"
        assert captured["template_table"] == "test-specs"
        # Fallback to hardcoded for missing key
        assert captured["request_table"] == "test-requests"
