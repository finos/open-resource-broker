"""Tests for AWSBatchSizesConfig — AWS-specific batch sizes configuration."""

import pytest
from pydantic import ValidationError


def test_aws_batch_sizes_importable_from_aws_configuration():
    from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig

    assert issubclass(AWSBatchSizesConfig, object)


def test_default_values():
    from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig

    cfg = AWSBatchSizesConfig(
        terminate_instances=25,
        create_tags=20,
        describe_instances=25,
        run_instances=10,
        describe_spot_fleet_instances=20,
        describe_auto_scaling_groups=20,
        describe_launch_templates=20,
        describe_spot_fleet_requests=20,
        describe_ec2_fleet_instances=20,
        describe_images=15,
        describe_security_groups=25,
        describe_subnets=25,
    )
    assert cfg.terminate_instances == 25
    assert cfg.run_instances == 10
    assert cfg.describe_images == 15


def test_batch_size_must_be_at_least_one():
    from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig

    with pytest.raises(ValidationError):
        AWSBatchSizesConfig(
            terminate_instances=0,
            create_tags=20,
            describe_instances=25,
            run_instances=10,
            describe_spot_fleet_instances=20,
            describe_auto_scaling_groups=20,
            describe_launch_templates=20,
            describe_spot_fleet_requests=20,
            describe_ec2_fleet_instances=20,
            describe_images=15,
            describe_security_groups=25,
            describe_subnets=25,
        )


def test_performance_config_has_no_batch_sizes():
    from orb.config.schemas.performance_schema import PerformanceConfig

    assert not hasattr(
        PerformanceConfig(
            enable_batching=True,
            enable_parallel=True,
            max_workers=10,
            enable_adaptive_batch_sizing=True,
        ),
        "batch_sizes",
    )


def test_batch_sizes_config_not_in_generic_schemas():
    from orb.config import schemas

    assert not hasattr(schemas, "BatchSizesConfig")
