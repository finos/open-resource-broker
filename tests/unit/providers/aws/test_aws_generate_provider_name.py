"""Regression tests — AWS generate_provider_name preserves its existing naming shape."""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.providers.aws.services.capability_service import AWSCapabilityService


def _make_capability_service() -> AWSCapabilityService:
    return AWSCapabilityService(handler_registry=MagicMock(), logger=MagicMock())


class TestAWSGenerateProviderNamePreservesCurrentShape:
    """Ensure the AWS naming convention does not regress."""

    def test_aws_generate_provider_name_preserves_current_shape(self) -> None:
        svc = _make_capability_service()
        name = svc.generate_provider_name({"profile": "my-profile", "region": "us-east-1"})
        assert name == "aws_my-profile_us-east-1"

    def test_instance_profile_fallback(self) -> None:
        svc = _make_capability_service()
        name = svc.generate_provider_name({"profile": None, "region": "eu-west-1"})
        assert name == "aws_instance-profile_eu-west-1"

    def test_profile_sanitisation(self) -> None:
        svc = _make_capability_service()
        name = svc.generate_provider_name(
            {"profile": "arn:aws:iam::123:role/MyRole", "region": "us-east-1"}
        )
        assert ":" not in name
        assert name.startswith("aws_")
        assert name.endswith("_us-east-1")

    def test_region_preserved_verbatim(self) -> None:
        svc = _make_capability_service()
        name = svc.generate_provider_name({"profile": "default", "region": "ap-southeast-2"})
        assert name.endswith("_ap-southeast-2")

    def test_default_region_fallback(self) -> None:
        svc = _make_capability_service()
        name = svc.generate_provider_name({"profile": "default"})
        assert name.endswith("_us-east-1")
