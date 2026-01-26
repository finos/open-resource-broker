from providers.aws.configuration.config import AWSProviderConfig


def test_legacy_connection_timeout_ms_converts_to_seconds() -> None:
    config = AWSProviderConfig(
        region="us-east-1",
        profile="default",
        connection_timeout_ms=12000,
    )

    assert config.aws_connect_timeout == 12


def test_legacy_aws_connection_timeout_converts_to_seconds() -> None:
    config = AWSProviderConfig(
        region="us-east-1",
        profile="default",
        aws_connection_timeout=15000,
    )

    assert config.aws_connect_timeout == 15
