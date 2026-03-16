"""Unit tests for AWSStorageConfig and AWSProviderConfig.storage field."""

import pytest

from orb.config.schemas.storage_schema import StorageConfig
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.storage.config import AWSStorageConfig


def test_defaults_when_no_storage_block():
    cfg = AWSProviderConfig(region="us-east-1")  # type: ignore[call-arg]
    assert cfg.storage.dynamodb is None


def test_dynamodb_field_parsed():
    cfg = AWSProviderConfig(  # type: ignore[call-arg]
        region="us-east-1",
        storage={"dynamodb": {"region": "eu-west-1", "profile": "prod", "table_prefix": "myapp"}},
    )
    assert cfg.storage.dynamodb is not None
    assert cfg.storage.dynamodb.region == "eu-west-1"
    assert cfg.storage.dynamodb.profile == "prod"
    assert cfg.storage.dynamodb.table_prefix == "myapp"


def test_aurora_field_parsed():
    cfg = AWSProviderConfig(  # type: ignore[call-arg]
        region="us-east-1",
        storage={
            "aurora": {
                "host": "cluster.rds.amazonaws.com",
                "port": 3306,
                "name": "mydb",
                "username": "admin",
                "password": "secret",
                "cluster_endpoint": "cluster.rds.amazonaws.com",
            }
        },
    )
    assert cfg.storage.aurora is not None
    assert cfg.storage.aurora.cluster_endpoint == "cluster.rds.amazonaws.com"


def test_empty_storage_block():
    cfg = AWSProviderConfig(region="us-east-1", storage={})  # type: ignore[call-arg]
    assert cfg.storage.dynamodb is None


def test_storage_as_json_string():
    cfg = AWSProviderConfig(  # type: ignore[call-arg]
        region="us-east-1",
        storage='{"dynamodb": {"region": "us-west-2", "profile": "default", "table_prefix": "x"}}',
    )
    assert cfg.storage.dynamodb is not None
    assert cfg.storage.dynamodb.region == "us-west-2"


def test_invalid_json_string_raises():
    with pytest.raises(ValueError):
        AWSProviderConfig(region="us-east-1", storage="not json")  # type: ignore[call-arg]


def test_aws_storage_config_both_none():
    cfg = AWSStorageConfig()  # type: ignore[call-arg]
    assert cfg.dynamodb is None
    assert cfg.aurora is None


def test_storage_config_valid_strategies_unchanged():
    """Regression guard: core StorageConfig must NOT accept dynamodb as a strategy."""
    with pytest.raises(ValueError):
        StorageConfig(strategy="dynamodb")
