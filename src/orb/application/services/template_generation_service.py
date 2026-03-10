"""Template Generation Service - Application Layer."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from orb.application.services.provider_registry_service import ProviderRegistryService
from pathlib import Path

from orb.application.dto.template_generation_dto import (
    ProviderTemplateResult,
    TemplateGenerationRequest,
    TemplateGenerationResult,
)
from orb.domain.base.ports import ConfigurationPort, LoggingPort, SchedulerPort
from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort
from orb.domain.base.utils import extract_provider_type
from orb.domain.constants import PROVIDER_TYPE_AWS


class TemplateGenerationService:
    """
    Application service for template generation.

    Handles template generation business logic while maintaining proper
    layer separation and dependency injection.
    """

    def __init__(
        self,
        config_manager: ConfigurationPort,
        scheduler_strategy: SchedulerPort,
        logger: LoggingPort,
        provider_registry_service: "ProviderRegistryService",
        template_example_generator: TemplateExampleGeneratorPort,
    ):
        self._config_manager = config_manager
        self._scheduler_strategy = scheduler_strategy
        self._logger = logger
        self._provider_registry_service = provider_registry_service
        self._template_example_generator = template_example_generator

    async def generate_templates(
        self, request: TemplateGenerationRequest
    ) -> TemplateGenerationResult:
        """
        Generate templates based on request parameters.

        Args:
            request: Template generation request with provider selection and options

        Returns:
            TemplateGenerationResult with generation status and results
        """
        try:
            # Determine target providers
            providers = self._determine_target_providers(request)

            if request.provider_specific:
                # Provider-specific mode: separate files per provider
                results = []
                for provider in providers:
                    result = await self._generate_templates_for_provider(provider, request)
                    results.append(result)
            else:
                # Generic mode: merge templates by provider type
                results = await self._generate_merged_templates_by_type(providers, request)

            # Calculate summary
            created_results = [r for r in results if r.status == "created"]
            skipped_results = [r for r in results if r.status == "skipped"]
            total_templates = sum(r.templates_count for r in created_results)

            return TemplateGenerationResult(
                status="success",
                message=f"Generated templates for {len(results)} providers",
                providers=results,
                total_templates=total_templates,
                created_count=len(created_results),
                skipped_count=len(skipped_results),
            )

        except Exception as e:
            self._logger.error("Template generation failed: %s", str(e))
            return TemplateGenerationResult(
                status="error",
                message=f"Failed to generate templates: {e}",
                providers=[],
                total_templates=0,
                created_count=0,
                skipped_count=0,
            )

    def _determine_target_providers(
        self, request: TemplateGenerationRequest
    ) -> List[Dict[str, str]]:
        """Determine which providers to generate templates for."""
        if request.specific_provider:
            return [self._get_provider_config(request.specific_provider)]
        elif request.all_providers:
            return self._get_active_providers()
        else:
            # Default: generate for all active providers
            return self._get_active_providers()

    async def _generate_templates_for_provider(
        self, provider: Dict[str, str], request: TemplateGenerationRequest
    ) -> ProviderTemplateResult:
        """Generate templates for a single provider."""
        provider_name = provider["name"]
        provider_type = provider["type"]

        try:
            # Generate example templates using provider registry
            examples = await self._generate_examples_from_provider(
                provider_type, provider_name, request.provider_api
            )

            # Format templates using scheduler strategy
            formatted_examples = self._format_templates(examples, request)

            # Determine output file path
            filename = self._determine_filename(provider, request)
            templates_file = self._get_templates_file_path(filename)

            # Check for existing file
            if templates_file.exists() and not request.force_overwrite:
                return ProviderTemplateResult(
                    provider=provider_name,
                    filename=filename,
                    templates_count=0,
                    path=str(templates_file),
                    status="skipped",
                    reason="file_exists",
                )

            # Write templates to file (bulk operation)
            self._write_templates_file(templates_file, formatted_examples)

            return ProviderTemplateResult(
                provider=provider_name,
                filename=filename,
                templates_count=len(examples),
                path=str(templates_file),
                status="created",
            )

        except Exception as e:
            self._logger.error(
                "Failed to generate templates for provider %s: %s", provider_name, str(e)
            )
            return ProviderTemplateResult(
                provider=provider_name,
                filename="",
                templates_count=0,
                path="",
                status="error",
                reason=str(e),
            )

    async def _generate_examples_from_provider(
        self, provider_type: str, provider_name: str, provider_api: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Generate example templates using provider registry."""

        # Ensure provider type is registered via service
        if not self._provider_registry_service.register_provider_strategy(provider_type):
            error_msg = (
                f"Provider type '{provider_type}' is not available or could not be registered"
            )
            raise ValueError(error_msg)

        # Generate example templates via injected port
        example_templates = self._template_example_generator.generate_example_templates(
            provider_type, provider_name, provider_api
        )
        if not example_templates:
            raise ValueError(f"No example templates generated for provider: {provider_name}")

        return example_templates  # type: ignore[return-value]

    def _determine_filename(
        self, provider: Dict[str, str], request: TemplateGenerationRequest
    ) -> str:
        """Determine the output filename based on generation mode."""
        provider_name = provider["name"]
        provider_type = provider["type"]

        if request.provider_specific:
            # Provider-specific mode: use provider name pattern
            return self._scheduler_strategy.get_templates_filename(
                provider_name,
                provider_type,
                config=self._get_config_dict(),
            )
        elif request.provider_type_filter:
            # Provider-type mode: use specified provider type
            return f"{request.provider_type_filter}_templates.json"
        else:
            # Generic mode: use provider_type pattern
            return f"{provider_type}_templates.json"

    def _format_templates(
        self, examples: List[Dict[str, Any]], request: TemplateGenerationRequest
    ) -> List[Dict[str, Any]]:
        """Format templates using scheduler strategy."""
        # Convert Template objects to dict format
        template_dicts = []
        for template in examples:
            if hasattr(template, "model_dump"):
                template_dict = template.model_dump(exclude_none=True, mode="json")  # type: ignore[union-attr]
            else:
                template_dict = template
            template_dicts.append(template_dict)

        # Apply scheduler formatting
        return self._scheduler_strategy.format_templates_for_generation(template_dicts)

    def _get_templates_file_path(self, filename: str) -> Path:
        """Get the full path for templates file."""
        from orb.config.platform_dirs import get_config_location

        config_dir = get_config_location()
        return config_dir / filename

    def _write_templates_file(
        self, templates_file: Path, formatted_examples: List[Dict[str, Any]]
    ) -> None:
        """Write templates to file (bulk operation)."""
        import json
        from datetime import datetime

        templates_data = {
            "scheduler_type": self._scheduler_strategy.get_scheduler_type(),
            "templates": formatted_examples,
        }

        class DateTimeEncoder(json.JSONEncoder):
            def default(self, obj: object) -> object:  # type: ignore[override]
                if isinstance(obj, datetime):
                    return obj.isoformat()
                try:
                    return super().default(obj)
                except TypeError:
                    return str(obj)

        # Ensure directory exists
        templates_file.parent.mkdir(parents=True, exist_ok=True)

        # Write templates file
        with open(templates_file, "w") as f:
            json.dump(templates_data, f, indent=2, cls=DateTimeEncoder)
            f.write("\n")  # Add final newline

    def _get_active_providers(self) -> List[Dict[str, str]]:
        """Get all active providers from configuration."""
        try:
            provider_config = self._config_manager.get_provider_config()
            providers = provider_config.get_active_providers()  # type: ignore[union-attr]
            return [{"name": p.name, "type": p.type} for p in providers]
        except Exception as e:
            self._logger.warning("Failed to get providers from config: %s", str(e))
            # Fallback to single default provider
            return [{"name": "aws_default_us-east-1", "type": PROVIDER_TYPE_AWS}]

    def _get_config_dict(self) -> Dict[str, Any]:
        """Get template configuration for filename generation."""
        return self._config_manager.get_template_config()

    def _get_provider_config(self, provider_name: str) -> Dict[str, str]:
        """Get configuration for specific provider."""
        try:
            provider_config = self._config_manager.get_provider_config()
            providers = provider_config.get_active_providers()  # type: ignore[union-attr]

            # Find specific provider
            for provider in providers:
                if provider.name == provider_name:
                    return {"name": provider.name, "type": provider.type}

            # Provider not found, create from name
            return {
                "name": provider_name,
                "type": extract_provider_type(provider_name),
            }
        except Exception:
            # Fallback
            return {
                "name": provider_name,
                "type": extract_provider_type(provider_name),
            }

    async def _generate_merged_templates_by_type(
        self, providers: List[Dict[str, str]], request: TemplateGenerationRequest
    ) -> List[ProviderTemplateResult]:
        """Generate merged templates grouped by provider type."""

        # Group providers by type
        providers_by_type = {}
        for provider in providers:
            provider_type = provider["type"]
            if provider_type not in providers_by_type:
                providers_by_type[provider_type] = []
            providers_by_type[provider_type].append(provider)

        results = []
        for provider_type, type_providers in providers_by_type.items():
            # Collect templates from all providers of this type
            all_templates = []
            for provider in type_providers:
                templates = await self._generate_examples_from_provider(
                    provider["type"], provider["name"], request.provider_api
                )
                all_templates.extend(templates)

            # Deduplicate templates
            unique_templates = self._deduplicate_templates(all_templates)

            # Create single merged file for this provider type
            filename = f"{provider_type}_templates.json"
            templates_file = self._get_templates_file_path(filename)

            # Check for existing file
            if templates_file.exists() and not request.force_overwrite:
                results.append(
                    ProviderTemplateResult(
                        provider=f"{provider_type} (merged)",
                        filename=filename,
                        templates_count=0,
                        path=str(templates_file),
                        status="skipped",
                        reason="file_exists",
                    )
                )
            else:
                # Format and write merged templates
                formatted_templates = self._format_merged_templates(unique_templates, request)
                self._write_templates_file(templates_file, formatted_templates)

                results.append(
                    ProviderTemplateResult(
                        provider=f"{provider_type} (merged)",
                        filename=filename,
                        templates_count=len(unique_templates),
                        path=str(templates_file),
                        status="created",
                    )
                )

        return results

    def _deduplicate_templates(self, templates: List[Any]) -> List[Dict[str, Any]]:
        """Deduplicate templates by template_id, keeping first occurrence."""
        seen_ids = set()
        unique_templates = []

        for template in templates:
            # Templates from _generate_examples_from_provider are Template objects
            if hasattr(template, "template_id"):
                template_id = template.template_id
                template_dict = template.model_dump(exclude_none=True, mode="json")
            else:
                # Already a dict
                template_id = template.get("template_id") or template.get("templateId")
                template_dict = template

            if template_id and template_id not in seen_ids:
                seen_ids.add(template_id)
                unique_templates.append(template_dict)

        return unique_templates

    def _format_merged_templates(
        self, template_dicts: List[Dict[str, Any]], request: TemplateGenerationRequest
    ) -> List[Dict[str, Any]]:
        """Format merged template dictionaries using scheduler strategy."""
        # Templates are already dicts, just apply scheduler formatting
        return self._scheduler_strategy.format_templates_for_generation(template_dicts)
