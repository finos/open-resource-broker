"""Unit tests for create_aws_strategy — console injection."""
from unittest.mock import MagicMock, patch


def test_create_aws_strategy_injects_console_into_strategy():
    """ConsolePort from DI container must be passed to AWSProviderStrategy, not fall back to NullConsoleAdapter."""
    from orb.infrastructure.adapters.null_console_adapter import NullConsoleAdapter
    from orb.providers.aws.registration import create_aws_strategy

    mock_console = MagicMock()
    mock_config_port = MagicMock()

    def mock_container_get(port):
        port_name = str(port)
        if "Console" in port_name:
            return mock_console
        return mock_config_port

    mock_container = MagicMock()
    mock_container.get.side_effect = mock_container_get

    # get_container is imported locally inside create_aws_strategy — patch at source
    with patch("orb.infrastructure.di.container.get_container", return_value=mock_container):
        strategy = create_aws_strategy({"region": "us-east-1"})

    assert strategy._console is mock_console
    assert not isinstance(strategy._console, NullConsoleAdapter)


def test_create_aws_strategy_handles_missing_console_gracefully():
    """If ConsolePort lookup fails, strategy is still created (console falls back to None)."""
    from orb.providers.aws.registration import create_aws_strategy

    # get_container is imported locally — patch at source
    with patch("orb.infrastructure.di.container.get_container", side_effect=Exception("DI not ready")):
        strategy = create_aws_strategy({"region": "us-east-1"})

    assert strategy is not None
