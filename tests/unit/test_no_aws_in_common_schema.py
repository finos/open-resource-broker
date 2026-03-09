"""Tests asserting common_schema.py has no AWS-specific handler type leakage."""

import inspect

import orb.config.schemas.common_schema as common_schema_module


def test_naming_config_has_no_handler_types_field() -> None:
    """NamingConfig must not expose a handler_types field."""
    assert "handler_types" not in common_schema_module.NamingConfig.model_fields


def test_common_schema_has_no_aws_handler_names() -> None:
    """common_schema.py source must not contain AWS API handler names."""
    source = inspect.getsource(common_schema_module)
    forbidden = ["EC2Fleet", "SpotFleet", "ASG", "RunInstances", "AutoScalingGroup"]
    found = [name for name in forbidden if name in source]
    assert not found, f"AWS handler names found in common_schema.py: {found}"
