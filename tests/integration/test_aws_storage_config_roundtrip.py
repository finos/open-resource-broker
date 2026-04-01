"""Integration test: AWSStorageConfig round-trip through ConfigurationManager."""

import os

import pytest

from orb.config.manager import ConfigurationManager
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.storage.config import AWSStorageConfig


@pytest.fixture
def config_manager():
    config_path = os.path.join(os.path.dirname(__file__), "../..", "config", "default_config.json")
    return ConfigurationManager(config_file=os.path.abspath(config_path))


def test_full_config_roundtrip(config_manager):
    aws_cfg = config_manager.get_typed(AWSProviderConfig)

    assert isinstance(aws_cfg.storage, AWSStorageConfig)
    # default_config.json has no provider-level storage block, so both are None
    assert aws_cfg.storage.dynamodb is None
    assert aws_cfg.storage.aurora is None
