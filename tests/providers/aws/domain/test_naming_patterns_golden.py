"""Golden values for AWS identifier patterns and limits.

These pin the exact validation rules so an edit to a regex or a limit is caught
instead of silently changing which identifiers are accepted.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from orb.providers.aws.domain.template import naming_patterns
from orb.providers.aws.domain.template.value_objects import (
    AWSARN,
    AWSFleetId,
    AWSImageId,
    AWSInstanceType,
    AWSLaunchTemplateId,
    AWSSecurityGroupId,
    AWSSubnetId,
    AWSTags,
)

GOLDEN_PATTERNS = {
    "subnet": r"^subnet-[0-9a-f]{8,17}$",
    "security_group": r"^sg-[0-9a-f]{8,17}$",
    "ec2_instance": r"^i-[0-9a-f]{8,17}$",
    "ami": r"^(ami-[0-9a-f]{8,17}|/aws/service/.+)$",
    "ec2_fleet": r"^fleet-[0-9a-f]{8,17}$",
    "launch_template": r"^lt-[0-9a-f]{8,17}$",
    "instance_type": r"^[a-z][0-9]+[a-z]*\.[a-z0-9]+$",
    "tag_key": r"^[a-zA-Z0-9\s\._:/=+\-@]{1,128}$",
    "arn": r"^arn:aws:[a-zA-Z0-9\-]+:[a-zA-Z0-9\-]*:[0-9]{12}:.+$",
    "account_id": r"^\d{12}$",
    "region": r"^[a-z]{2}-[a-z]+-\d$",
}

GOLDEN_LIMITS = {
    "TAG_KEY_LENGTH": 128,
    "TAG_VALUE_LENGTH": 256,
    "MAX_TAGS_PER_RESOURCE": 50,
    "MAX_SECURITY_GROUPS": 5,
    "MAX_SUBNETS": 16,
}


def test_pattern_strings_are_pinned() -> None:
    assert naming_patterns.PATTERNS == GOLDEN_PATTERNS


@pytest.mark.parametrize(("name", "value"), sorted(GOLDEN_LIMITS.items()))
def test_limits_are_pinned(name: str, value: int) -> None:
    assert getattr(naming_patterns, name) == value


def _check(cls, accepted: list[str], rejected: list[str]) -> None:
    for sample in accepted:
        assert cls(value=sample).value == sample
    for sample in rejected:
        with pytest.raises(ValidationError):
            cls(value=sample)


@pytest.mark.parametrize(
    ("cls", "prefix"),
    [
        (AWSSubnetId, "subnet-"),
        (AWSSecurityGroupId, "sg-"),
        (AWSFleetId, "fleet-"),
        (AWSLaunchTemplateId, "lt-"),
        (AWSImageId, "ami-"),
    ],
)
def test_prefixed_ids_accept_8_to_17_lowercase_hex(cls, prefix: str) -> None:
    _check(
        cls,
        accepted=[prefix + "a" * 8, prefix + "0123456789abcdef0", prefix + "f" * 17],
        rejected=[
            prefix + "a" * 7,
            prefix + "a" * 18,
            prefix + "A" * 8,
            prefix + "g" * 8,
            "x" + prefix + "a" * 8,
            "",
        ],
    )


def test_image_id_also_accepts_ssm_parameter_paths() -> None:
    _check(
        AWSImageId,
        accepted=["/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"],
        rejected=["/aws/service/", "/other/service/x"],
    )


def test_instance_type_samples() -> None:
    _check(
        AWSInstanceType,
        accepted=["t2.micro", "m5.large", "c5n.18xlarge", "p4d.24xlarge", "m7i.metal48"],
        rejected=["t2", "T2.micro", "2t.micro", "t2.", ".micro", "t2.Micro", "t2.micro.x"],
    )


def test_arn_samples() -> None:
    _check(
        AWSARN,
        accepted=[
            "arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0",
            "arn:aws:s3::123456789012:bucket",
        ],
        rejected=[
            "arn:aws:ec2:us-east-1:12345678901:instance/i-1",
            "arn:aws:ec2:us-east-1:1234567890123:instance/i-1",
            "arn:aws-cn:ec2:us-east-1:123456789012:instance/i-1",
            "arn:aws:ec2:us-east-1:123456789012:",
        ],
    )


def test_unbound_patterns_samples() -> None:
    cases = {
        "ec2_instance": (["i-" + "a" * 8, "i-" + "a" * 17], ["i-" + "a" * 7, "i-" + "a" * 18]),
        "account_id": (["123456789012"], ["12345678901", "1234567890123", "12345678901a"]),
        "region": (["us-east-1", "eu-west-2"], ["us-east", "us-east-12", "USA-east-1"]),
    }
    for key, (accepted, rejected) in cases.items():
        pattern = naming_patterns.PATTERNS[key]
        assert all(re.match(pattern, s) for s in accepted), key
        assert not any(re.match(pattern, s) for s in rejected), key


def test_tag_limits_enforced_at_exact_boundaries() -> None:
    AWSTags(tags={"k" * 128: "v" * 256})
    with pytest.raises(ValidationError):
        AWSTags(tags={"k" * 129: "v"})
    with pytest.raises(ValidationError):
        AWSTags(tags={"k": "v" * 257})
    with pytest.raises(ValidationError):
        AWSTags(tags={"bad#key": "v"})
    AWSTags(tags={"aws:name/x=y+z@-. _": "v"})
