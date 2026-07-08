"""Moto-specific conftest for full-pipeline mocked AWS tests.

Applies the moto compat patch to every test in this subtree and re-exports
helpers from the parent AWS conftest so that imports such as:

    from tests.providers.aws.mocked.conftest import _inject_moto_factory

continue to work without modification.  Fake AWS credentials are supplied
by the parent conftest (``_aws_fake_credentials``) for the whole AWS
non-live subtree.
"""

import pytest

# Re-export helpers so existing imports remain valid
from tests.providers.aws.conftest import (
    REGION,
    _inject_moto_factory,
    _make_config_port,
    _make_launch_template_manager,
    _make_logger,
    _make_moto_aws_client,
    make_asg_handler,
    make_aws_template,
    make_ec2_fleet_handler,
    make_patch_moto_compat,
    make_request,
    make_run_instances_handler,
    make_spot_fleet_handler,
)

__all__ = [
    "REGION",
    "_inject_moto_factory",
    "_make_config_port",
    "_make_launch_template_manager",
    "_make_logger",
    "_make_moto_aws_client",
    "make_asg_handler",
    "make_aws_template",
    "make_ec2_fleet_handler",
    "make_patch_moto_compat",
    "make_request",
    "make_run_instances_handler",
    "make_spot_fleet_handler",
]


@pytest.fixture(autouse=True)
def patch_moto_compat():
    """Patch moto-incompatible behaviours for all moto/ tests.

    1. AWSImageResolutionService.is_resolution_needed -> False
       Prevents SSM path resolution which moto cannot fulfil.

    2. AWSProvisioningAdapter._provision_via_handlers synthesises instances
       from instance_ids so the orchestration loop sees fulfilled_count > 0.
    """
    with make_patch_moto_compat():
        yield
