"""Unit tests for AWSStorageConfig and AWSProviderConfig.storage field."""

import pytest

from orb.config.schemas.storage_schema import StorageConfig, _get_valid_storage_strategies
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


def test_storage_config_rejects_unregistered_strategy():
    """An unknown/unregistered strategy is still rejected."""
    with pytest.raises(ValueError):
        StorageConfig(strategy="not-a-real-backend")


def test_storage_config_baseline_strategies_always_valid():
    """json/sql are accepted regardless of registry state."""
    assert StorageConfig(strategy="json").strategy == "json"
    assert StorageConfig(strategy="sql").strategy == "sql"


def test_storage_config_accepts_strategy_once_registered(monkeypatch):
    """A backend becomes valid exactly when the storage registry advertises it.

    The generic schema names no provider backend; validity is derived from the
    registry, so 'dynamodb' is accepted only after it is registered.
    """
    # Not registered (registry reports baseline only) -> rejected.
    monkeypatch.setattr(
        "tests.unit.providers.aws.storage.test_aws_storage_config._get_valid_storage_strategies",
        lambda: {"json", "sql"},
    )
    with pytest.raises(ValueError):
        StorageConfig(strategy="dynamodb")

    # Registered (registry now advertises it) -> accepted.
    monkeypatch.setattr(
        "tests.unit.providers.aws.storage.test_aws_storage_config._get_valid_storage_strategies",
        lambda: {"json", "sql", "dynamodb"},
    )
    assert StorageConfig(strategy="dynamodb").strategy == "dynamodb"
