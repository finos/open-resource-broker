"""Templates generate command handler."""

import json
from typing import Any, Dict

from cli.console import print_info, print_success
from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.di.container import get_container
from infrastructure.di.buses import QueryBus
from application.dto.queries import ListTemplatesQuery


async def handle_templates_generate(args) -> Dict[str, Any]:
    """Handle orb templates generate command with multi-provider support."""
    try:
        if args.provider:
            # Generate for specific provider
            providers = [_get_provider_config(args.provider)]
        elif getattr(args, "all_providers", False):
            # Generate for all active providers
            providers = _get_active_providers()
        else:
            # Default: generate for all active providers (NEW BEHAVIOR)
            providers = _get_active_providers()

        results = []
        for provider in providers:
            result = await _generate_templates_for_provider(provider, args)
            results.append(result)

        # Print summary
        total_templates = sum(r["templates_count"] for r in results)
        print_success(f"Generated templates for {len(results)} providers")
        print_info(f"Total templates: {total_templates}")
        print_info("")

        for result in results:
            print_info(f"Provider: {result['provider']}")
            print_info(f"  File: {result['filename']}")
            print_info(f"  Templates: {result['templates_count']}")

        return {
            "status": "success",
            "message": f"Generated templates for {len(results)} providers",
            "providers": results,
            "total_templates": total_templates,
        }

    except Exception as e:
        import traceback

        return {
            "status": "error",
            "message": f"Failed to generate templates: {e}",
            "traceback": traceback.format_exc(),
        }


async def _generate_templates_for_provider(provider: dict, args) -> dict:
    """Generate templates for a single provider."""
    provider_name = provider["name"]
    provider_type = provider["type"]
    provider_api = getattr(args, "provider_api", None)

    # Generate examples using provider-specific logic
    examples = await _generate_examples_from_factory(provider_type, provider_name, provider_api)

    # Get scheduler strategy for filename
    from config.platform_dirs import get_config_location
    from infrastructure.registry.scheduler_registry import get_scheduler_registry

    config_dir = get_config_location()
    config_file = config_dir / "config.json"

    # Load config to get scheduler type
    scheduler_type = "default"
    config_dict = None
    if config_file.exists():
        with open(config_file) as f:
            config_dict = json.load(f)
            scheduler_type = config_dict.get("scheduler", {}).get("type", "default")

    # Get strategy class from registry
    registry = get_scheduler_registry()
    strategy_class = registry.get_strategy_class(scheduler_type)

    # Determine filename based on generation mode
    if getattr(args, "generic", False):
        # Generic mode: use provider_type pattern
        filename = f"{provider_type}_templates.json"
    elif getattr(args, "provider_type", None):
        # Provider-type mode: use specified provider type
        filename = f"{args.provider_type}_templates.json"
    else:
        # Provider-specific mode: use provider name pattern
        filename = strategy_class.get_templates_filename(provider_name, provider_type, config_dict)

    # Write templates file
    config_dir.mkdir(parents=True, exist_ok=True)
    templates_file = config_dir / filename

    container = get_container()
    scheduler_strategy = container.get(SchedulerPort)
    
    # Format templates based on generation mode
    if getattr(args, "generic", False) or getattr(args, "provider_type", None):
        # Generic mode: don't apply provider-specific defaults
        formatted_examples = examples
    else:
        # Provider-specific mode: apply provider-specific defaults
        formatted_examples = scheduler_strategy.format_templates_for_generation(examples)

    templates_data = {"templates": formatted_examples}
    
    # Custom JSON encoder to handle datetime objects
    from datetime import datetime
    
    class DateTimeEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            return super().default(obj)
    
    with open(templates_file, "w") as f:
        json.dump(templates_data, f, indent=2, cls=DateTimeEncoder)

    return {
        "provider": provider_name,
        "filename": filename,
        "templates_count": len(examples),
        "path": str(templates_file),
    }


def _get_active_providers() -> list[dict]:
    """Get all active providers from configuration."""
    from config.platform_dirs import get_config_location

    config_dir = get_config_location()
    config_file = config_dir / "config.json"

    if not config_file.exists():
        # Get active provider from provider selection service
        try:
            from infrastructure.di.container import get_container
            provider_selection = get_container().get("ProviderSelectionService")
            selection_result = provider_selection.select_active_provider()
            return [{"name": selection_result.provider_name, "type": selection_result.provider_type}]
        except Exception:
            # Final fallback to default
            return [{"name": "default", "type": "aws"}]

    with open(config_file) as f:
        config_dict = json.load(f)

    provider_config = config_dict.get("provider", {})
    providers = provider_config.get("providers", [])

    # Return enabled providers
    active_providers = []
    for provider in providers:
        if provider.get("enabled", True):
            active_providers.append({"name": provider["name"], "type": provider["type"]})

    # Fallback if no providers configured
    if not active_providers:
        active_providers = [{"name": "default", "type": "aws"}]

    return active_providers


def _get_provider_config(provider_name: str) -> dict:
    """Get configuration for specific provider."""
    from config.platform_dirs import get_config_location

    config_dir = get_config_location()
    config_file = config_dir / "config.json"

    if not config_file.exists():
        # Fallback for specific provider
        return {
            "name": provider_name,
            "type": provider_name.split("-")[0] if "-" in provider_name else provider_name,
        }

    with open(config_file) as f:
        config_dict = json.load(f)

    provider_config = config_dict.get("provider", {})
    providers = provider_config.get("providers", [])

    # Find specific provider
    for provider in providers:
        if provider["name"] == provider_name:
            return {"name": provider["name"], "type": provider["type"]}

    # Provider not found, create from name
    return {
        "name": provider_name,
        "type": provider_name.split("-")[0] if "-" in provider_name else provider_name,
    }


async def _generate_examples_from_factory(
    provider_type: str, provider_name: str, provider_api: str = None
) -> list[Dict[str, Any]]:
    """Generate example templates using provider strategy's handler factory."""
    from infrastructure.di.container import get_container
    
    container = get_container()
    
    # For now, directly use AWS handler factory since that's what we have
    # TODO: Extend this when we have other provider types
    if provider_type == "aws":
        from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
        
        handler_factory = container.get(AWSHandlerFactory)
        if not handler_factory:
            raise ValueError(f"AWSHandlerFactory not available for provider: {provider_name}")
        
        # Generate example templates from the handler factory
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
        
        # Convert Template objects to dict format for generation
        examples = []
        for template in example_templates:
            template_dict = template.model_dump(exclude_none=True)
            examples.append(template_dict)
        
        return examples
    else:
        raise ValueError(f"Unsupported provider type: {provider_type}. Currently only 'aws' is supported.")
