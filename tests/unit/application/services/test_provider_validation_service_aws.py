"""ProviderValidationService with the real AWS validation adapter injected.

The service guards a template only when a validator is injected, so these tests
build it with the real adapter explicitly and pass a real ``Template`` (the
service reads ``vars(template)``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from orb.application.services.provider_validation_service import ProviderValidationService
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.results import ProviderSelectionResult, ValidationResult
from orb.domain.template.exceptions import TemplateValidationError
from orb.domain.template.template_aggregate import Template
from orb.providers.aws.configuration.validator import AWSProviderConfig
from orb.providers.aws.infrastructure.adapters.aws_validation_adapter import (
    AWSValidationAdapter,
)


def _service() -> ProviderValidationService:
    selection = MagicMock()
    selection.select_provider_for_template.return_value = ProviderSelectionResult(
        provider_type="aws", provider_name="aws-1", selection_reason="default"
    )
    selection.validate_template_requirements.return_value = ValidationResult(
        is_valid=True,
        provider_instance="aws-1",
        errors=[],
        warnings=[],
        supported_features=[],
        unsupported_features=[],
    )
    validator = AWSValidationAdapter(Mock(spec=AWSProviderConfig), Mock(spec=LoggingPort))
    return ProviderValidationService(MagicMock(), selection, validator)


def _template(**extra) -> Template:
    fields = {
        "template_id": "t1",
        "provider_api": "EC2Fleet",
        "machine_image": "ami-0abc1234",
        "subnet_ids": ["subnet-1"],
        "security_group_ids": ["sg-1"],
        **extra,
    }
    return Template(**fields)


def test_template_state_carries_the_image_as_machine_image() -> None:
    state = vars(_template())
    assert state["machine_image"] == "ami-0abc1234"
    assert "image_id" not in state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "image",
    ["ami-0abc1234", "resolve:ssm:/p", "ssm:/p", "/aws/service/ami-amazon-linux-latest/x"],
)
async def test_accepts_each_valid_image_form(image) -> None:
    result = await _service().select_and_validate_provider(_template(machine_image=image))
    assert result.provider_name == "aws-1"


@pytest.mark.asyncio
async def test_rejects_a_missing_image() -> None:
    with pytest.raises(TemplateValidationError) as exc:
        await _service().select_and_validate_provider(_template(machine_image=None))
    assert "image_id is required" in exc.value.details["reason"]


@pytest.mark.asyncio
async def test_rejects_a_malformed_image() -> None:
    with pytest.raises(TemplateValidationError) as exc:
        await _service().select_and_validate_provider(_template(machine_image="not-an-ami"))
    assert "Invalid AWS AMI ID format" in exc.value.details["reason"]


@pytest.mark.asyncio
async def test_rejects_a_bad_subnet() -> None:
    with pytest.raises(TemplateValidationError) as exc:
        await _service().select_and_validate_provider(_template(subnet_ids=["bad"]))
    assert "Invalid AWS subnet ID format: bad" in exc.value.details["reason"]
