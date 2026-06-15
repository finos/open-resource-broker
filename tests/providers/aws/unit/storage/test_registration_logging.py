"""Tests verifying LoggingPort DIP compliance in AWS storage registration."""

import logging
from typing import Any
from unittest.mock import MagicMock, patch

from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.adapters.logging_adapter import LoggingAdapter


def _make_config_stub(**kwargs: Any) -> MagicMock:
    """Return a minimal config stub with no dynamodb_strategy or provider attrs."""
    stub = MagicMock(spec=[])  # no attributes by default
    for k, v in kwargs.items():
        setattr(stub, k, v)
    return stub


class TestCreateDynamodbStrategyLogging:
    def test_create_dynamodb_strategy_passes_logging_port(self) -> None:
        """Logger passed to DynamoDBStorageStrategy must satisfy LoggingPort, not logging.Logger."""
        from orb.providers.aws.storage.registration import create_dynamodb_strategy

        captured: dict[str, Any] = {}

        def fake_init(self: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        config = _make_config_stub(region="us-east-1", profile="default")

        with patch(
            "orb.providers.aws.storage.strategy.DynamoDBStorageStrategy.__init__",
            fake_init,
        ):
            create_dynamodb_strategy(config)

        logger = captured["logger"]
        assert isinstance(logger, LoggingPort)
        assert not isinstance(logger, logging.Logger)

    def test_create_dynamodb_strategy_logger_name(self) -> None:
        """LoggingAdapter must be constructed with the registration module name."""
        from orb.providers.aws.storage.registration import create_dynamodb_strategy

        captured: dict[str, Any] = {}

        def fake_init(self: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        config = _make_config_stub(region="us-east-1", profile="default")

        with patch(
            "orb.providers.aws.storage.strategy.DynamoDBStorageStrategy.__init__",
            fake_init,
        ):
            create_dynamodb_strategy(config)

        logger = captured["logger"]
        assert isinstance(logger, LoggingAdapter)
        assert logger._logger.name == "orb.providers.aws.storage.registration"


class TestCreateDynamodbUnitOfWorkLogging:
    def test_create_dynamodb_unit_of_work_config_manager_branch_passes_logging_port(
        self,
    ) -> None:
        """ConfigurationManager branch: logger arg must be a LoggingPort instance."""
        from orb.config.manager import ConfigurationManager
        from orb.providers.aws.storage.registration import create_dynamodb_unit_of_work

        captured: dict[str, Any] = {}

        def fake_uow_init(self: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        raw_config = {
            "storage": {
                "dynamodb_strategy": {
                    "region": "us-east-1",
                    "profile": "default",
                    "table_prefix": "hf",
                }
            }
        }

        # Make mock pass isinstance(config, ConfigurationManager) check
        mock_cm = MagicMock(spec=ConfigurationManager)
        mock_cm.get_raw_config.return_value = raw_config

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
            create_dynamodb_unit_of_work(mock_cm)

        assert isinstance(captured["logger"], LoggingPort)

    def test_create_dynamodb_unit_of_work_dict_branch_passes_logging_port(
        self,
    ) -> None:
        """Dict branch: logger arg must be a LoggingPort instance."""
        from orb.providers.aws.storage.registration import create_dynamodb_unit_of_work

        captured: dict[str, Any] = {}

        def fake_uow_init(self: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        config = {"region": "us-east-1", "profile": "default", "table_prefix": "hf"}

        mock_session = MagicMock()
        mock_client = MagicMock()
        mock_session.client.return_value = mock_client

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

        assert isinstance(captured["logger"], LoggingPort)


class TestRegisterAuroraStorageSmoke:
    def test_register_aurora_storage_smoke(self) -> None:
        """register_aurora_storage must not raise when given a mock registry and logger."""
        from orb.providers.aws.storage.registration import register_aurora_storage

        mock_registry = MagicMock()
        mock_logger = MagicMock(spec=LoggingPort)

        register_aurora_storage(registry=mock_registry, logger=mock_logger)

        mock_registry.register_storage.assert_called_once()
        call_kwargs = mock_registry.register_storage.call_args.kwargs
        assert call_kwargs["storage_type"] == "aurora"
