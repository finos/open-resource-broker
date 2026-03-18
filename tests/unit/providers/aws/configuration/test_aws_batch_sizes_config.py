"""Tests for AWSBatchSizesConfig — AWS-specific batch sizes configuration."""

import pytest
from pydantic import ValidationError


def test_aws_batch_sizes_importable_from_aws_configuration():
    from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig

    assert issubclass(AWSBatchSizesConfig, object)


def test_default_values():
    from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig

    cfg = AWSBatchSizesConfig()
    assert cfg.terminate_instances == 25
    assert cfg.run_instances == 10
    assert cfg.describe_images == 15


def test_batch_size_must_be_at_least_one():
    from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig

    with pytest.raises(ValidationError):
        AWSBatchSizesConfig(terminate_instances=0)


def test_performance_config_has_no_batch_sizes():
    from orb.config.schemas.performance_schema import PerformanceConfig

    assert not hasattr(PerformanceConfig(), "batch_sizes")


def test_batch_sizes_config_not_in_generic_schemas():
    from orb.config import schemas

    assert not hasattr(schemas, "BatchSizesConfig")
