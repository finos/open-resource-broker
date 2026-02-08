"""Template Generation Service - Application Layer."""

from typing import Any, Dict, List, Optional

from domain.base.ports import ConfigurationPort, LoggingPort, SchedulerPort
from domain.base.ports.template_adapter_port import TemplateAdapterPort
from domain.base.contracts.template_contract import TemplateContract
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
        template_adapter: TemplateAdapterPort,
        logger: LoggingPort,
    ):
        self._config_manager = config_manager
        self._scheduler_strategy = scheduler_strategy
        self._template_adapter = template_adapter
        self._logger = logger

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
            
            # Generate templates for each provider
            results = []
            for provider in providers:
                result = await self._generate_templates_for_provider(provider, request)
                results.append(result)
            
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
            
            # Check if templates should be overwritten
            if not request.force_overwrite:
                # Use template adapter to check existing templates (Clean Architecture)
                try:
                    existing_templates = await self._template_adapter.get_templates()
                    if existing_templates:
                        return ProviderTemplateResult(
                            provider=provider_name,
                            filename=filename,
                            templates_count=0,
                            path="",
                            status="skipped",
                            reason="file_exists"
                        )
                except Exception:
                    # If we can't check, proceed with generation
                    pass
            
            # Format templates using scheduler strategy
            formatted_examples = self._format_templates(examples, request)
            
            # Save templates using template adapter (Clean Architecture)
            for template_data in formatted_examples:
                template_contract = TemplateContract(
                    template_id=template_data.get("template_id", ""),
                    name=template_data.get("name", template_data.get("template_id", "")),
                    provider_api=template_data.get("provider_api", "RunInstances"),
                    configuration=template_data
                )
                await self._template_adapter.save_template(template_contract)
            
            return ProviderTemplateResult(
                provider=provider_name,
                filename=filename,
                templates_count=len(examples),
                path="",
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
        from providers.registry import get_provider_registry
        from infrastructure.di.container import get_container
        
        registry = get_provider_registry()
        container = get_container()
        
        # Ensure provider type is registered
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