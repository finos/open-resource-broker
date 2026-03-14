"""Unit tests for AWSProviderStrategy infrastructure discovery — console propagation."""
from unittest.mock import MagicMock, patch


def _make_aws_strategy(console=None):
    """Create a minimal AWSProviderStrategy with mocked dependencies."""
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.providers.aws.configuration.config import AWSProviderConfig
    from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

    config = AWSProviderConfig(region="us-east-1")  # type: ignore[call-arg]
    strategy = AWSProviderStrategy.__new__(AWSProviderStrategy)
    strategy._aws_config = config
    strategy._console = console
    strategy._logger = LoggingAdapter()
    strategy._provider_instance_config = None
    strategy._provider_name = None
    strategy._aws_client = None
    strategy._aws_client_resolver = None
    strategy._aws_provisioning_port = None
    strategy._aws_provisioning_port_resolver = None
    strategy._config_port = None
    strategy._infrastructure_service = None
    return strategy


def test_infrastructure_service_receives_injected_console():
    """_get_infrastructure_service must pass strategy._console to AWSInfrastructureDiscoveryService."""
    from orb.infrastructure.adapters.null_console_adapter import NullConsoleAdapter

    mock_console = MagicMock()
    strategy = _make_aws_strategy(console=mock_console)

    # AWSSessionFactory is imported locally inside AWSInfrastructureDiscoveryService.__init__
    with patch("orb.providers.aws.session_factory.AWSSessionFactory.create_session") as mock_create:
        mock_session = MagicMock()
        mock_create.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        service = strategy._get_infrastructure_service()

    assert service._console is mock_console
    assert not isinstance(service._console, NullConsoleAdapter)


def test_infrastructure_service_falls_back_to_null_console_when_none():
    """When strategy._console is None, service uses NullConsoleAdapter (documents current fallback)."""
    from orb.infrastructure.adapters.null_console_adapter import NullConsoleAdapter

    strategy = _make_aws_strategy(console=None)

    with patch("orb.providers.aws.session_factory.AWSSessionFactory.create_session") as mock_create:
        mock_session = MagicMock()
        mock_create.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        service = strategy._get_infrastructure_service()

    assert isinstance(service._console, NullConsoleAdapter)
