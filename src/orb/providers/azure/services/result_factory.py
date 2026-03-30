"""Azure strategy result and error normalization."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError as PydanticValidationError

from orb.domain.base.exceptions import ValidationError as DomainValidationError
from orb.providers.azure.exceptions.azure_exceptions import AzureError, AzureValidationError
from orb.providers.base.strategy import ProviderResult


class AzureStrategyResultFactory:
    """Normalize Azure validation and provider errors into ProviderResult values."""

    @staticmethod
    def validation_error_code(
        exc: AzureValidationError | DomainValidationError | PydanticValidationError,
        *,
        default: str,
    ) -> str:
        error_code = getattr(exc, "error_code", None)
        if isinstance(error_code, str) and error_code:
            return error_code
        return default

    @staticmethod
    def azure_error_metadata(exc: AzureError) -> dict[str, Any]:
        return {"provider_error": exc.to_dict()}

    def validation_error_result(
        self,
        *,
        message: str,
        exc: AzureValidationError | DomainValidationError | PydanticValidationError,
        default_error_code: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProviderResult:
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("error_class", exc.__class__.__name__)
        if isinstance(exc, AzureError):
            merged_metadata.update(self.azure_error_metadata(exc))
        return ProviderResult.error_result(
            message,
            self.validation_error_code(exc, default=default_error_code),
            merged_metadata,
        )

    def azure_error_result(
        self,
        *,
        message: str,
        exc: AzureError,
        default_error_code: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProviderResult:
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("error_class", exc.__class__.__name__)
        merged_metadata.update(self.azure_error_metadata(exc))
        return ProviderResult.error_result(
            message,
            exc.error_code or default_error_code,
            merged_metadata,
        )
