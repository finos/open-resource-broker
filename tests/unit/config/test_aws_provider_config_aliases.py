"""Tests for AWSProviderConfig validation_alias paths."""

from orb.providers.aws.configuration.config import AWSProviderConfig


def test_canonical_field_names():
    """Constructing with canonical field names works."""
    config = AWSProviderConfig(region="us-east-1", aws_max_retries=5)
    assert config.aws_max_retries == 5


def test_validation_alias_names():
    """model_validate with alias names sets the canonical fields."""
    config = AWSProviderConfig.model_validate(
        {"region": "us-east-1", "max_retries": 5, "timeout": 60}
    )
    assert config.aws_max_retries == 5
    assert config.aws_read_timeout == 60


def test_defaults_when_omitted():
    """Omitting optional fields yields documented defaults."""
    config = AWSProviderConfig(region="us-east-1")
    assert config.aws_max_retries == 3
    assert config.aws_read_timeout == 30
