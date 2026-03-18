"""Unit tests for storage registration functions using typed AWSProviderConfig."""

from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.exceptions import ConfigurationError
from orb.infrastructure.storage.exceptions import StorageError
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.storage.config import AWSStorageConfig, DynamodbStrategyConfig
from orb.providers.aws.storage.registration import (
    create_aurora_strategy,
    create_aurora_unit_of_work,
    create_dynamodb_strategy,
    create_dynamodb_unit_of_work,
)


def _make_config_manager(aws_provider_config: AWSProviderConfig) -> MagicMock:
    """Return a mock ConfigurationManager whose get_typed returns the given config."""
    from orb.config.manager import ConfigurationManager
    from orb.config.schemas.common_schema import NamingConfig

    mock = MagicMock(spec=ConfigurationManager)
    mock.get_typed.return_value = aws_provider_config
    mock.app_config.naming = NamingConfig()  # type: ignore[call-arg]
    return mock


def test_create_dynamodb_strategy_uses_typed_config():
    dynamodb_cfg = DynamodbStrategyConfig(region="eu-west-1", profile="prod", table_prefix="myapp")
    aws_cfg = AWSProviderConfig(  # type: ignore[call-arg]
        region="us-east-1",
        storage=AWSStorageConfig(dynamodb=dynamodb_cfg),  # type: ignore[call-arg]
    )
    config = _make_config_manager(aws_cfg)

    with patch(
        "orb.providers.aws.storage.strategy.DynamoDBStorageStrategy.__init__",
        return_value=None,
    ) as MockInit:
        create_dynamodb_strategy(config)

    MockInit.assert_called_once()
    _, call_kwargs = MockInit.call_args
    assert call_kwargs["region"] == "eu-west-1"
    assert call_kwargs["table_name"] == "myapp-generic"
    assert call_kwargs["profile"] == "prod"


def test_create_dynamodb_strategy_defaults_when_dynamodb_none():
    aws_cfg = AWSProviderConfig(region="us-east-1", storage=AWSStorageConfig(dynamodb=None))  # type: ignore[call-arg]
    config = _make_config_manager(aws_cfg)

    with patch(
        "orb.providers.aws.storage.strategy.DynamoDBStorageStrategy.__init__",
        return_value=None,
    ) as MockInit:
        create_dynamodb_strategy(config)

    MockInit.assert_called_once()
    _, call_kwargs = MockInit.call_args
    assert call_kwargs["region"] is None
    assert call_kwargs["table_name"] == "hostfactory-generic"
    assert call_kwargs["profile"] == "default"


def test_create_dynamodb_strategy_dict_path():
    config = {"region": "us-east-1", "profile": "default", "table_prefix": "hf"}

    with patch(
        "orb.providers.aws.storage.strategy.DynamoDBStorageStrategy.__init__",
        return_value=None,
    ) as MockInit:
        create_dynamodb_strategy(config)

    MockInit.assert_called_once()
    _, call_kwargs = MockInit.call_args
    assert call_kwargs["region"] == "us-east-1"
    assert call_kwargs["table_name"] == "hf-generic"
    assert call_kwargs["profile"] == "default"


def test_create_dynamodb_unit_of_work_uses_typed_config():
    dynamodb_cfg = DynamodbStrategyConfig(region="eu-west-1", profile="prod", table_prefix="myapp")
    aws_cfg = AWSProviderConfig(  # type: ignore[call-arg]
        region="us-east-1",
        storage=AWSStorageConfig(dynamodb=dynamodb_cfg),  # type: ignore[call-arg]
    )
    config = _make_config_manager(aws_cfg)

    with (
        patch("orb.providers.aws.session_factory.AWSSessionFactory.create_session") as MockSession,
        patch(
            "orb.providers.aws.storage.unit_of_work.DynamoDBUnitOfWork.__init__",
            return_value=None,
        ) as MockUoW,
    ):
        mock_session = MagicMock()
        MockSession.return_value = mock_session
        mock_session.client.return_value = MagicMock()
        create_dynamodb_unit_of_work(config)

    MockUoW.assert_called_once()
    _, call_kwargs = MockUoW.call_args
    assert call_kwargs["machine_table"] == "myapp-machines"


def test_create_aurora_strategy_raises_when_aurora_none():
    aws_cfg = AWSProviderConfig(region="us-east-1", storage=AWSStorageConfig(aurora=None))  # type: ignore[call-arg]
    config = _make_config_manager(aws_cfg)

    with pytest.raises(StorageError):
        create_aurora_strategy(config)


def test_create_aurora_unit_of_work_raises_when_aurora_none():
    aws_cfg = AWSProviderConfig(region="us-east-1", storage=AWSStorageConfig(aurora=None))  # type: ignore[call-arg]
    config = _make_config_manager(aws_cfg)

    with pytest.raises(StorageError):
        create_aurora_unit_of_work(config)


def test_get_typed_failure_propagates():
    from orb.config.manager import ConfigurationManager

    config = MagicMock(spec=ConfigurationManager)
    config.get_typed.side_effect = ConfigurationError("bad config")

    with pytest.raises(ConfigurationError):
        create_dynamodb_strategy(config)
