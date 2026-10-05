"""Unit tests for ProviderValidationService.select_and_validate_provider.

Covers:
- Happy path with no optional validator.
- Optional validator rejecting the template configuration.
- Provider selection port reporting validation errors.
- Warnings are logged but do not block selection.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.provider_validation_service import ProviderValidationService
from orb.domain.base.exceptions import ApplicationError
from orb.domain.base.results import ProviderSelectionResult, ValidationResult


def _make_selection_result(provider_name="aws-1") -> ProviderSelectionResult:
    return ProviderSelectionResult(
        provider_type="aws",
        provider_name=provider_name,
        selection_reason="default",
    )


def _make_validation_result(is_valid=True, errors=None, warnings=None) -> ValidationResult:
    return ValidationResult(
        is_valid=is_valid,
        provider_instance="aws-1",
        errors=errors or [],
        warnings=warnings or [],
        supported_features=[],
        unsupported_features=[],
    )


def _make_service(validator=None, validation_result=None, selection_result=None):
    container = MagicMock()
    logger = MagicMock()
    provider_selection_port = MagicMock()
    provider_selection_port.select_provider_for_template.return_value = (
        selection_result or _make_selection_result()
    )
    provider_selection_port.validate_template_requirements.return_value = (
        validation_result or _make_validation_result()
    )

    service = ProviderValidationService(
        container=container,
        logger=logger,
        provider_selection_port=provider_selection_port,
        validator=validator,
    )
    return service, provider_selection_port, logger


@pytest.mark.unit
class TestSelectAndValidateProvider:
    @pytest.mark.asyncio
    async def test_happy_path_returns_selection_result(self):
        service, port, logger = _make_service()
        template = MagicMock()

        result = await service.select_and_validate_provider(template)

        assert result.provider_name == "aws-1"
        port.select_provider_for_template.assert_called_once_with(template)
        port.validate_template_requirements.assert_called_once_with(template, "aws-1")
        logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_optional_validator_rejects_raises_application_error(self):
        validator = MagicMock()
        validator.validate_template_configuration.return_value = {
            "valid": False,
            "errors": ["missing field: image_id"],
        }
        service, _, _ = _make_service(validator=validator)
        template = MagicMock()

        with pytest.raises(ApplicationError, match="missing field: image_id"):
            await service.select_and_validate_provider(template)

    @pytest.mark.asyncio
    async def test_optional_validator_accepts_continues_to_port_validation(self):
        validator = MagicMock()
        validator.validate_template_configuration.return_value = {"valid": True}
        service, port, _ = _make_service(validator=validator)
        template = MagicMock()

        result = await service.select_and_validate_provider(template)

        assert result.provider_name == "aws-1"
        port.validate_template_requirements.assert_called_once()

    @pytest.mark.asyncio
    async def test_port_validation_errors_raise_application_error(self):
        validation_result = _make_validation_result(
            is_valid=False, errors=["unsupported instance type"]
        )
        service, _, _ = _make_service(validation_result=validation_result)
        template = MagicMock()

        with pytest.raises(ApplicationError, match="unsupported instance type"):
            await service.select_and_validate_provider(template)

    @pytest.mark.asyncio
    async def test_warnings_are_logged_but_do_not_raise(self):
        validation_result = _make_validation_result(
            is_valid=True, warnings=["deprecated AMI family"]
        )
        service, _, logger = _make_service(validation_result=validation_result)
        template = MagicMock()

        result = await service.select_and_validate_provider(template)

        assert result.provider_name == "aws-1"
        logger.warning.assert_called_once()
        assert "deprecated AMI family" in logger.warning.call_args[0][1]

    @pytest.mark.asyncio
    async def test_dict_template_passed_directly_to_validator(self):
        validator = MagicMock()
        validator.validate_template_configuration.return_value = {"valid": True}
        service, _, _ = _make_service(validator=validator)
        template = {"template_id": "tmpl-1"}

        await service.select_and_validate_provider(template)  # type: ignore[arg-type]

        validator.validate_template_configuration.assert_called_once_with(template)
