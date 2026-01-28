"""Templates generate command handler."""

import json
from typing import Any, Dict

from cli.console import print_info, print_success
from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.di.container import get_container


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
    examples = _generate_examples_from_factory(provider_type, provider_name, provider_api)

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

    # Generate filename using provider name
    filename = strategy_class.get_templates_filename(provider_name, provider_type, config_dict)

    # Write templates file
    config_dir.mkdir(parents=True, exist_ok=True)
    templates_file = config_dir / filename

    container = get_container()
    scheduler_strategy = container.get(SchedulerPort)
    formatted_examples = scheduler_strategy.format_templates_for_generation(examples)

    templates_data = {"templates": formatted_examples}
    with open(templates_file, "w") as f:
        json.dump(templates_data, f, indent=2)

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
        # Fallback to default provider
        return [{"name": "aws-default", "type": "aws"}]

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
        active_providers = [{"name": "aws-default", "type": "aws"}]

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


def _generate_examples_from_factory(
    provider_type: str, provider_name: str, provider_api: str = None
) -> list[Dict[str, Any]]:
    """Generate example templates using handler factory."""
    if provider_type == "aws":
        from infrastructure.di.container import get_container
        from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory

        container = get_container()
        factory = container.get(AWSHandlerFactory)

        # Get examples from handlers as Template domain objects
        template_objects = factory.generate_example_templates()

        # Convert to dict format for processing
        examples = []
        for template in template_objects:
            # Filter by provider_api if specified
            if provider_api and template.provider_api != provider_api:
                continue
            examples.append(template.model_dump(exclude_none=True))

        return examples
    else:
        return []
