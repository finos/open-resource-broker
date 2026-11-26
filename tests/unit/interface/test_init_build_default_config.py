"""The non-interactive init configuration is built from plain values."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock, patch

from orb.interface import init_command_handler as init_mod
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


def _build(call):
    with (
        patch.object(
            init_mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "aws", "description": ""}],
        ),
        patch.object(init_mod, "_get_provider_strategy", return_value=AWSProviderStrategy),
    ):
        return call()


def test_plain_values_build_the_same_config_as_command_line_arguments():
    options = {
        "aws_region": "eu-west-2",
        "aws_profile": "ops",
        "subnet_ids": "subnet-1, subnet-2",
        "security_group_ids": "sg-1",
        "fleet_role": "arn:aws:iam::123456789012:role/fleet",
    }
    args = Namespace(provider_type="aws", scheduler="hostfactory", **options)

    from_args = _build(lambda: init_mod._get_default_config(args, MagicMock()))
    from_values = _build(
        lambda: init_mod.build_default_config(
            MagicMock(), provider_type="aws", scheduler_type="hostfactory", options=options
        )
    )

    assert from_values == from_args
    provider = from_values["providers"][0]
    assert provider["config"] == {"profile": "ops", "region": "eu-west-2"}
    assert provider["infrastructure_defaults"]["subnet_ids"] == ["subnet-1", "subnet-2"]
    assert from_values["scheduler_type"] == "hostfactory"


def test_unset_provider_type_falls_back_to_the_first_registered_provider():
    config = _build(
        lambda: init_mod.build_default_config(
            MagicMock(), provider_type=None, scheduler_type="default", options={}
        )
    )

    assert config["providers"][0]["type"] == "aws"
    assert config["providers"][0]["config"]["region"]


def test_core_init_options_match_the_init_subparser():
    import sys

    from orb.cli.args import parse_args

    argv = ["orb", "init", "--subnet-ids", "s", "--security-group-ids", "g", "--fleet-role", "r"]
    with patch.object(sys, "argv", argv):
        namespace, _ = parse_args()

    parsed = {name for name in init_mod._CORE_INIT_OPTIONS if getattr(namespace, name, None)}
    assert parsed == set(init_mod._CORE_INIT_OPTIONS)


def test_documented_init_options_are_accepted():
    init_mod.validate_init_options(
        {
            "aws_region": "eu-west-2",
            "aws_profile": "ops",
            "subnet_ids": "subnet-1",
            "security_group_ids": "sg-1",
            "fleet_role": "role",
        }
    )


def test_unknown_init_options_are_rejected():
    import pytest

    for key in ("bogus", "__class__", "__dict__", "provider_type", "config_dir", "force"):
        with pytest.raises(init_mod.InvalidInitInputError):
            init_mod.validate_init_options({key: "x"})
