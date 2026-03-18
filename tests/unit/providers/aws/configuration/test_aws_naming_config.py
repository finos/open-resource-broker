"""Tests for AWSNamingConfig patterns."""

import re

import pytest

from orb.providers.aws.configuration.validator import AWSNamingConfig


@pytest.fixture
def patterns() -> dict[str, str]:
    return AWSNamingConfig().patterns


def test_ami_pattern_matches_direct_ami_id(patterns: dict[str, str]) -> None:
    assert re.match(patterns["ami"], "ami-0abcdef1234567890")


def test_ami_pattern_matches_ssm_path(patterns: dict[str, str]) -> None:
    assert re.match(
        patterns["ami"], "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
    )


def test_account_id_pattern_present(patterns: dict[str, str]) -> None:
    assert "account_id" in patterns
    assert re.match(patterns["account_id"], "123456789012")
    assert not re.match(patterns["account_id"], "12345")


def test_subnet_pattern_present(patterns: dict[str, str]) -> None:
    assert "subnet" in patterns


def test_security_group_pattern_present(patterns: dict[str, str]) -> None:
    assert "security_group" in patterns


def test_launch_template_pattern_present(patterns: dict[str, str]) -> None:
    assert "launch_template" in patterns


def test_arn_pattern_present(patterns: dict[str, str]) -> None:
    assert "arn" in patterns
