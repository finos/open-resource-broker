"""Azure Template Adapter.

Converts between configuration-layer template dicts and the domain
``AzureTemplate`` aggregate, following the adapter/port pattern.
"""

from typing import Any

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate


@injectable
class AzureTemplateAdapter:
    """Adapter for Azure template operations."""

    def __init__(self, logger: LoggingPort) -> None:
        self._logger = logger

    def create_template(self, template_data: dict[str, Any]) -> AzureTemplate:
        """Create an ``AzureTemplate`` from a raw config dict.

        The dict may use either snake_case or camelCase keys thanks
        to the ``AliasChoices`` defined on the aggregate.
        """
        self._logger.debug("Creating AzureTemplate from data: %s", template_data.get("template_id"))
        return AzureTemplate.from_azure_format(template_data)

    @staticmethod
    def template_to_dict(template: AzureTemplate) -> dict[str, Any]:
        """Serialise an ``AzureTemplate`` to a plain dict."""
        return template.model_dump(mode="json", by_alias=False, exclude_none=True)

    def validate_template_data(self, template_data: dict[str, Any]) -> dict[str, Any]:
        """Validate template data by attempting to construct the aggregate.

        Returns:
            dict with keys: valid (bool), errors (list[str]), warnings (list[str])
        """
        errors: list[str] = []
        warnings: list[str] = []

        try:
            self.create_template(template_data)
        except Exception as exc:
            errors.append(str(exc))

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": list(template_data.keys()),
        }
