"""HandlersConfig keeps legacy flags and capabilities consistent."""

import pytest
from pydantic import ValidationError

from orb.providers.aws.configuration.config import HandlersConfig


def test_capability_false_is_not_overwritten_by_legacy_default():
    cfg = HandlersConfig.model_validate({"capabilities": {"ec2_fleet": False}})

    assert cfg.capabilities.ec2_fleet is False
    assert cfg.ec2_fleet is False
    assert cfg.spot_fleet is True


def test_legacy_false_propagates_to_capabilities():
    cfg = HandlersConfig.model_validate({"spot_fleet": False})

    assert cfg.capabilities.spot_fleet is False
    assert cfg.capabilities.ec2_fleet is True


def test_agreeing_values_are_accepted():
    cfg = HandlersConfig.model_validate({"asg": False, "capabilities": {"asg": False}})

    assert cfg.asg is False
    assert cfg.capabilities.asg is False


def test_conflicting_values_are_rejected():
    with pytest.raises(ValidationError, match="conflicts"):
        HandlersConfig.model_validate(
            {"run_instances": False, "capabilities": {"run_instances": True}}
        )
