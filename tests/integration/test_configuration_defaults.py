"""Configuration defaults applied to templates by TemplateDefaultsService."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.storage.repositories.template_repository import TemplateSerializer

_INSTANCE_DEFAULTS = {
    "subnet_ids": ["subnet-default-1"],
    "security_group_ids": ["sg-default-1"],
    "machine_types": {"t3.micro": 1},
}


def _defaults_service(instance_defaults: dict[str, Any]) -> TemplateDefaultsService:
    """A defaults service backed by one configured ``aws-default`` provider instance."""
    config = Mock()
    config.get_template_config.return_value = {}
    config.get_provider_config.return_value = SimpleNamespace(
        providers=[
            SimpleNamespace(name="aws-default", type="aws", template_defaults=instance_defaults)
        ],
        provider_defaults={},
    )
    return TemplateDefaultsService(cast(ConfigurationPort, config), cast(LoggingPort, Mock()))


def test_serializer_applies_provider_instance_defaults():
    """Fields missing from stored data are filled from the provider instance defaults."""
    serializer = TemplateSerializer(defaults_service=_defaults_service(_INSTANCE_DEFAULTS))

    template = serializer.from_dict(
        {
            "template_id": "test-minimal-template",
            "image_id": "ami-123456",
            "provider_name": "aws-default",
        }
    )

    assert template.subnet_ids == ["subnet-default-1"]
    assert template.security_group_ids == ["sg-default-1"]
    assert template.machine_types == {"t3.micro": 1}


def test_serializer_keeps_explicit_values_over_defaults():
    """Values present in the stored data are not replaced by defaults."""
    serializer = TemplateSerializer(defaults_service=_defaults_service(_INSTANCE_DEFAULTS))

    template = serializer.from_dict(
        {
            "template_id": "test-explicit-template",
            "image_id": "ami-123456",
            "provider_name": "aws-default",
            "subnet_ids": ["subnet-explicit-123"],
            "security_group_ids": ["sg-explicit-456"],
            "machine_types": {"t3.large": 1},
        }
    )

    assert template.subnet_ids == ["subnet-explicit-123"]
    assert template.security_group_ids == ["sg-explicit-456"]
    assert template.machine_types == {"t3.large": 1}


def test_defaults_service_fills_missing_fields_from_instance_defaults():
    """resolve_template_defaults adds the instance defaults to a minimal template."""
    service = _defaults_service(_INSTANCE_DEFAULTS)

    resolved = service.resolve_template_defaults(
        {"template_id": "test-direct", "image_id": "ami-123456"},
        provider_instance_name="aws-default",
    )

    assert resolved["template_id"] == "test-direct"
    assert resolved["image_id"] == "ami-123456"
    assert resolved["subnet_ids"] == ["subnet-default-1"]
    assert resolved["security_group_ids"] == ["sg-default-1"]
    assert resolved["machine_types"] == {"t3.micro": 1}
