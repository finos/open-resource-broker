"""Tests asserting common_schema.py has no AWS-specific handler type leakage."""

import inspect

import orb.config.schemas.common_schema as common_schema_module
from orb.config.schemas.common_schema import NamingConfig


def test_naming_config_has_no_handler_types_field() -> None:
    """NamingConfig must not expose a handler_types field."""
    assert "handler_types" not in common_schema_module.NamingConfig.model_fields


def test_common_schema_has_no_aws_handler_names() -> None:
    """common_schema.py source must not contain AWS API handler names."""
    source = inspect.getsource(common_schema_module)
    forbidden = ["EC2Fleet", "SpotFleet", "ASG", "RunInstances", "AutoScalingGroup"]
    found = [name for name in forbidden if name in source]
    assert not found, f"AWS handler names found in common_schema.py: {found}"


def test_naming_config_has_no_limits_field() -> None:
    """NamingConfig must not expose AWS-specific limits."""
    assert "limits" not in common_schema_module.NamingConfig.model_fields


def test_common_schema_has_no_limits_config_class() -> None:
    """LimitsConfig must not exist in common_schema — it is AWS-specific."""
    assert not hasattr(common_schema_module, "LimitsConfig")


def test_naming_config_patterns_has_no_ami_id() -> None:
    assert "ami_id" not in NamingConfig().patterns


def test_naming_config_patterns_has_no_subnet() -> None:
    assert "subnet" not in NamingConfig().patterns


def test_naming_config_patterns_has_no_security_group() -> None:
    assert "security_group" not in NamingConfig().patterns


def test_naming_config_patterns_has_no_account_id() -> None:
    assert "account_id" not in NamingConfig().patterns


def test_naming_config_patterns_has_no_launch_template() -> None:
    assert "launch_template" not in NamingConfig().patterns


def test_naming_config_patterns_has_no_arn() -> None:
    assert "arn" not in NamingConfig().patterns
