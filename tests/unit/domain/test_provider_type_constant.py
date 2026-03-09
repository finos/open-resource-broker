"""Tests for PROVIDER_TYPE_AWS domain constant (task 1717)."""


def test_provider_type_aws_importable():
    from orb.domain.constants import PROVIDER_TYPE_AWS  # noqa: F401


def test_provider_type_aws_value():
    from orb.domain.constants import PROVIDER_TYPE_AWS

    assert PROVIDER_TYPE_AWS == "aws"
