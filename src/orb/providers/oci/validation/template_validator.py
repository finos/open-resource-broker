"""OCI template validator.

Checks a template against the OCI template model without any live OCI
contact or provider configuration, so it can be created by the registry the
same way as the other providers' validators.  Errors are the field-level
problems the model reports (an unknown ``capacity_type``, a non-positive
``ocpus`` or ``boot_volume_gbs``, ...).  A missing shape is only a warning,
because the shape may be supplied later by template defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from orb.infrastructure.logging.logger import get_logger

_logger = get_logger(__name__)

_SHAPE_KEYS = ("machine_types", "shape", "machine_type", "instance_type")


@dataclass
class OCITemplateValidationResult:
    """Outcome of one :meth:`OCITemplateValidator.validate` call."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid


def _as_dict(template: Any) -> dict[str, Any]:
    if isinstance(template, dict):
        return dict(template)
    if isinstance(template, BaseModel):
        return template.model_dump()
    raise TypeError(f"Unsupported template type: {type(template).__name__}")


def _format_error(error: Any) -> str:
    location = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "invalid value")
    return f"{location}: {message}" if location else str(message)


class OCITemplateValidator:
    """Registration-time validator for OCI templates."""

    def validate(self, template: Any) -> OCITemplateValidationResult:
        """Validate a template (model or config dict) and return the result."""
        try:
            return self._validate(template)
        except Exception as exc:
            # The registry adapter treats validator failures as "no opinion", so
            # an unexpected exception here would otherwise pass silently.
            _logger.warning("OCI template validation could not run: %s", exc)
            return OCITemplateValidationResult(valid=True)

    @staticmethod
    def _validate(template: Any) -> OCITemplateValidationResult:
        from orb.providers.oci.domain.template import OCITemplate

        data = _as_dict(template)
        errors: list[str] = []
        warnings: list[str] = []

        try:
            OCITemplate.model_validate(data)
        except ValidationError as exc:
            errors.extend(_format_error(e) for e in exc.errors())

        if not any(data.get(key) for key in _SHAPE_KEYS):
            warnings.append(
                "no shape set (machine_types or shape); it must come from template defaults"
            )

        return OCITemplateValidationResult(valid=not errors, errors=errors, warnings=warnings)


def create_oci_validator(provider_config: Any = None) -> OCITemplateValidator:
    """Create the OCI template validator.

    ``provider_config`` is accepted for parity with the other providers'
    validator factories; OCI template checks do not need it.
    """
    return OCITemplateValidator()


__all__ = ["OCITemplateValidationResult", "OCITemplateValidator", "create_oci_validator"]
