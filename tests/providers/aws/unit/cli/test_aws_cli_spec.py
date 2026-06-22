"""Tests for AWSCLISpec.generate_name()."""

import argparse

import pytest

from orb.providers.aws.cli.aws_cli_spec import AWSCLISpec


def _args(profile: str | None, region: str | None) -> argparse.Namespace:
    ns = argparse.Namespace()
    ns.aws_profile = profile
    ns.aws_region = region
    return ns


@pytest.fixture()
def spec() -> AWSCLISpec:
    return AWSCLISpec()


def test_generate_name_basic(spec: AWSCLISpec) -> None:
    """Basic profile and region produce expected name."""
    assert spec.generate_name(_args("myprofile", "us-east-1")) == "aws_myprofile_us-east-1"


def test_generate_name_special_chars_sanitized(spec: AWSCLISpec) -> None:
    """Special characters in profile are replaced with hyphens."""
    assert (
        spec.generate_name(_args("my.profile@org", "eu-west-1")) == "aws_my-profile-org_eu-west-1"
    )


def test_generate_name_none_profile(spec: AWSCLISpec) -> None:
    """None profile produces empty segment."""
    assert spec.generate_name(_args(None, "us-west-2")) == "aws__us-west-2"


def test_generate_name_none_region(spec: AWSCLISpec) -> None:
    """None region produces empty segment."""
    assert spec.generate_name(_args("default", None)) == "aws_default_"


def test_generate_name_empty_strings(spec: AWSCLISpec) -> None:
    """Empty profile and region produce double underscore name."""
    assert spec.generate_name(_args("", "")) == "aws__"
