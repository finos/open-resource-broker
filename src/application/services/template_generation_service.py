"""Template Generation Service - Application Layer."""

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from application.services.provider_registry_service import ProviderRegistryService
from pathlib import Path

from domain.base.ports import ConfigurationPort, LoggingPort, SchedulerPort
from application.dto.template_generation_dto import (
    TemplateGenerationRequest,
    TemplateGenerationResult,
    ProviderTemplateResult
)


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
    ):
        self._config_manager = config_manager
        self._scheduler_strategy = scheduler_strategy
        self._logger = logger
        self._provider_registry_service = provider_registry_service

    async def generate_templates(self, request: TemplateGenerationRequest) -> TemplateGenerationResult:
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
                # Provider-specific mode: generate separate files
                results = []
                for provider in providers:
                    result = await self._generate_templates_for_provider(provider, request)
                    results.append(result)
            else:
                # Default mode: collect templates and merge by filename
                results = await self._generate_with_deep_merge(providers, request)
            
            # Calculate summary
            created_results = [r for r in results if r.status == "created"]
            skipped_results = [r for r in results if r.status == "skipped"]
            total_templates = sum(r.templates_count for r in created_results)
            
            # Adjust message for deep merge vs provider-specific
            if request.provider_specific:
                message = f"Generated templates for {len(providers)} providers"
            else:
                message = f"Merged templates from {len(providers)} providers"
            
            return TemplateGenerationResult(
                status="success",
                message=message,
                providers=results,
                total_templates=total_templates,
                created_count=len(created_results),
                skipped_count=len(skipped_results)
            )
            
        except Exception as e:
            self._logger.error("Template generation failed: %s", str(e))
            return TemplateGenerationResult(
                status="error",
                message=f"Failed to generate templates: {e}",
                providers=[],
                total_templates=0,
                created_count=0,
                skipped_count=0
            )

    def _determine_target_providers(self, request: TemplateGenerationRequest) -> List[Dict[str, str]]:
        """Determine which providers to generate templates for."""
        if request.specific_provider:
            return [self._get_provider_config(request.specific_provider)]
        elif request.all_providers:
            return self._get_active_providers()
        else:
            # Default: generate for all active providers
            return self._get_active_providers()

    async def _generate_with_deep_merge(self, providers: List[Dict[str, str]], request: TemplateGenerationRequest) -> List[ProviderTemplateResult]:
        """Generate templates with deep merge for providers targeting same file."""
        # Group providers by target filename
        providers_by_file = {}
        for provider in providers:
            filename = self._determine_filename(provider, request)
            if filename not in providers_by_file:
                providers_by_file[filename] = []
            providers_by_file[filename].append(provider)
        
        results = []
        for filename, file_providers in providers_by_file.items():
            if len(file_providers) == 1:
                # Single provider for this file - generate normally
                result = await self._generate_templates_for_provider(file_providers[0], request)
                results.append(result)
            else:
                # Multiple providers for same file - merge templates
                merged_result = await self._generate_merged_templates(file_providers, filename, request)
                results.extend(merged_result)
        
        return results

    async def _generate_merged_templates(self, providers: List[Dict[str, str]], filename: str, request: TemplateGenerationRequest) -> List[ProviderTemplateResult]:
        """Generate and merge templates from multiple providers with deduplication."""
        templates_file = self._get_templates_file_path(filename)
        
        # Check for existing file
        if templates_file.exists() and not request.force_overwrite:
            return [ProviderTemplateResult(
                provider=p["name"],
                filename=filename,
                templates_count=0,
                path=str(templates_file),
                status="skipped",
                reason="file_exists"
            ) for p in providers]
        
        # Collect templates from all providers
        provider_templates = {}
        original_counts = {}
        
        for provider in providers:
            try:
                # Generate templates for this provider
                examples = await self._generate_examples_from_provider(
                    provider["type"], 
                    provider["name"], 
                    request.provider_api
                )
                
                formatted_templates = self._format_templates(examples, request)
                provider_templates[provider["name"]] = formatted_templates
                original_counts[provider["name"]] = len(examples)
                
            except Exception as e:
                self._logger.error("Failed to generate templates for provider %s: %s", provider["name"], str(e))
                provider_templates[provider["name"]] = []
                original_counts[provider["name"]] = 0
        
        # Merge templates with deduplication
        merged_templates = self._merge_templates_with_deduplication(provider_templates)
        
        # Calculate actual contributions after deduplication
        total_original = sum(original_counts.values())
        final_count = len(merged_templates)
        
        # Write merged templates to file
        if merged_templates:
            self._write_templates_file(templates_file, merged_templates)
        
        # Create accurate results after deduplication
        if len(providers) == 1 or request.provider_specific:
            # Provider-specific mode or single provider - report per provider
            provider_results = []
            for provider in providers:
                if provider["name"] in original_counts:
                    original_count = original_counts[provider["name"]]
                    if original_count > 0:
                        provider_results.append(ProviderTemplateResult(
                            provider=provider["name"],
                            filename=filename,
                            templates_count=final_count,
                            path=str(templates_file),
                            status="created"
                        ))
                    else:
                        provider_results.append(ProviderTemplateResult(
                            provider=provider["name"],
                            filename=filename,
                            templates_count=0,
                            path=str(templates_file),
                            status="error",
                            reason="no templates generated"
                        ))
        else:
            # Deep merge mode - report as single merged result
            provider_names = ", ".join([p["name"] for p in providers])
            provider_results = [ProviderTemplateResult(
                provider=f"{provider_names} (merged)",
                filename=filename,
                templates_count=final_count,
                path=str(templates_file),
                status="created",
                reason=f"Merged from {len(providers)} providers: {final_count} final templates (deduplicated from {total_original})"
            )]
        
        return provider_results

    async def _generate_templates_for_provider(
        self, 
        provider: Dict[str, str], 
        request: TemplateGenerationRequest
    ) -> ProviderTemplateResult:
        """Generate templates for a single provider."""
        provider_name = provider["name"]
        provider_type = provider["type"]
        
        try:
            # Generate example templates using provider registry
            examples = await self._generate_examples_from_provider(
                provider_type, 
                provider_name, 
                request.provider_api
            )
            
            # Determine output filename
            filename = self._determine_filename(provider, request)
            
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
                    reason="file_exists"
                )
            
            # Write templates to file (bulk operation)
            self._write_templates_file(templates_file, formatted_examples)
            
            return ProviderTemplateResult(
                provider=provider_name,
                filename=filename,
                templates_count=len(examples),
                path=str(templates_file),
                status="created"
            )
            
        except Exception as e:
            self._logger.error("Failed to generate templates for provider %s: %s", provider_name, str(e))
            return ProviderTemplateResult(
                provider=provider_name,
                filename="",
                templates_count=0,
                path="",
                status="error",
                reason=str(e)
            )

    async def _generate_examples_from_provider(
        self, 
        provider_type: str, 
        provider_name: str, 
        provider_api: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Generate example templates using provider registry."""
        from infrastructure.di.container import get_container
        container = get_container()
        
        # Ensure provider type is registered via registry
        from providers.registry import get_provider_registry
        registry = get_provider_registry()
        
        if not registry.is_provider_registered(provider_type):
            registry.ensure_provider_type_registered(provider_type)
        
        if not registry.is_provider_registered(provider_type):
            error_msg = registry.format_registry_error(provider_type, "provider")
            raise ValueError(error_msg)
        
        # For AWS provider, use the existing handler factory
        if provider_type == "aws":
            from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
            
            handler_factory = container.get(AWSHandlerFactory)
            if not handler_factory:
                raise ValueError(f"AWSHandlerFactory not available for provider: {provider_name}")
            
            # Generate example templates
            example_templates = handler_factory.generate_example_templates()
            if not example_templates:
                raise ValueError(f"No example templates generated for provider: {provider_name}")
            
            # Filter by provider_api if specified
            if provider_api:
                example_templates = [
                    template for template in example_templates 
                    if template.provider_api == provider_api
                ]
                if not example_templates:
                    raise ValueError(f"No templates found for provider API: {provider_api}")
            
            return example_templates
        else:
            # For other providers, return empty list for now
            return []

    def _determine_filename(self, provider: Dict[str, str], request: TemplateGenerationRequest) -> str:
        """Determine the output filename based on generation mode."""
        provider_name = provider["name"]
        provider_type = provider["type"]
        
        if request.provider_specific:
            # Provider-specific mode: use provider name pattern
            config_dict = self._get_config_dict()
            return self._scheduler_strategy.get_templates_filename(provider_name, provider_type, config_dict)
        elif request.provider_type_filter:
            # Provider-type mode: use specified provider type
            return f"{request.provider_type_filter}_templates.json"
        else:
            # Generic mode: use provider_type pattern
            return f"{provider_type}_templates.json"

    def _format_templates(self, examples: List[Dict[str, Any]], request: TemplateGenerationRequest) -> List[Dict[str, Any]]:
        """Format templates using scheduler strategy."""
        # Convert Template objects to dict format
        template_dicts = []
        for template in examples:
            template_dict = template.model_dump(exclude_none=True, mode='json')
            template_dicts.append(template_dict)
        
        # Apply scheduler formatting
        return self._scheduler_strategy.format_templates_for_generation(template_dicts)

    def _get_templates_file_path(self, filename: str) -> Path:
        """Get the full path for templates file."""
        from config.platform_dirs import get_config_location
        
        config_dir = get_config_location()
        return config_dir / filename

    def _get_config_dict(self) -> dict:
        """Get configuration dictionary for scheduler strategy."""
        try:
            from infrastructure.di.container import get_container
            from domain.base.ports.configuration_port import ConfigurationPort
            
            container = get_container()
            config_port = container.get(ConfigurationPort)
            
            # Get the configuration as dict
            # This provides access to template filename patterns and overrides
            return {
                "template": {
                    "filename_patterns": {
                        "provider_specific": "{provider_name}_templates.json",
                        "provider_type": "{provider_type}_templates.json"
                    }
                }
            }
        except Exception:
            # Fallback to empty config
            return {}

    def _write_templates_file(self, templates_file: Path, formatted_examples: List[Dict[str, Any]]) -> None:
        """Write templates to file (bulk operation)."""
        import json
        from datetime import datetime
        
        templates_data = {"templates": formatted_examples}
        
        class DateTimeEncoder(json.JSONEncoder):
            def default(self, obj):
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
            providers = provider_config.get_active_providers()
            
            return [{"name": p.name, "type": p.type} for p in providers]
        except Exception as e:
            self._logger.warning("Failed to get providers from config: %s", str(e))
            # Fallback to single default provider
            return [{"name": "aws_default_us-east-1", "type": "aws"}]

    def _get_provider_config(self, provider_name: str) -> Dict[str, str]:
        """Get configuration for specific provider."""
        try:
            provider_config = self._config_manager.get_provider_config()
            providers = provider_config.get_active_providers()
            
            # Find specific provider
            for provider in providers:
                if provider.name == provider_name:
                    return {"name": provider.name, "type": provider.type}
            
            # Provider not found, create from name
            return {
                "name": provider_name,
                "type": provider_name.split("_")[0] if "_" in provider_name else provider_name,
            }
        except Exception:
            # Fallback
            return {
                "name": provider_name,
                "type": provider_name.split("_")[0] if "_" in provider_name else provider_name,
            }

    def _merge_templates_with_deduplication(self, provider_templates: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Merge templates from multiple providers with intelligent deduplication."""
        template_groups = {}  # templateId -> list of templates
        
        # Group templates by templateId
        for provider_name, templates in provider_templates.items():
            for template in templates:
                template_id = template.get("templateId")
                if template_id:
                    if template_id not in template_groups:
                        template_groups[template_id] = []
                    template_groups[template_id].append((provider_name, template))
        
        merged_templates = []
        
        for template_id, template_list in template_groups.items():
            if len(template_list) == 1:
                # Single template - include as-is
                _, template = template_list[0]
                merged_templates.append(template)
            else:
                # Multiple templates with same ID - check if identical
                provider_name, first_template = template_list[0]
                
                # Compare templates (excluding timestamps and provider-specific fields)
                are_identical = True
                for other_provider, other_template in template_list[1:]:
                    if not self._templates_are_identical(first_template, other_template):
                        are_identical = False
                        break
                
                if are_identical:
                    # Identical templates - keep one copy
                    merged_templates.append(first_template)
                else:
                    # Different templates with same ID - create provider variants
                    for provider_name, template in template_list:
                        variant_template = template.copy()
                        region = self._extract_region_from_provider(provider_name)
                        variant_template["templateId"] = f"{template_id}-{region}"
                        merged_templates.append(variant_template)
        
        return merged_templates
    
    def _templates_are_identical(self, template1: Dict[str, Any], template2: Dict[str, Any]) -> bool:
        """Check if two templates are identical (ignoring timestamps and provider-specific fields)."""
        ignore_fields = {"createdAt", "updatedAt", "version", "name"}
        
        # Get keys from both templates
        keys1 = set(template1.keys()) - ignore_fields
        keys2 = set(template2.keys()) - ignore_fields
        
        if keys1 != keys2:
            return False
        
        # Compare values for common keys
        for key in keys1:
            if template1[key] != template2[key]:
                return False
        
        return True
    
    def _extract_region_from_provider(self, provider_name: str) -> str:
        """Extract region from provider name."""
        # Provider names like: aws_flamurg-testing-Admin_eu-west-2, aws-testing-us-east-1
        parts = provider_name.split("_")
        if len(parts) >= 3:
            return parts[-1]  # Last part is region
        elif "-" in provider_name:
            parts = provider_name.split("-")
            # Look for region pattern (us-east-1, eu-west-2, etc.)
            for part in reversed(parts):
                if "-" in part and len(part) > 5:  # Basic region pattern check
                    return part
        return "unknown"