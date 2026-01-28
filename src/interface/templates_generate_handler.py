"""Templates generate command handler."""

import json
from pathlib import Path
from typing import Any, Dict

from cli.console import print_success, print_info, print_command
from infrastructure.di.container import get_container
from domain.base.ports.scheduler_port import SchedulerPort


async def handle_templates_generate(args) -> Dict[str, Any]:
    """Handle orb templates generate command."""
    try:
        # Get provider name from args or use default
        provider_name = getattr(args, 'provider', None) or "aws-default"
        provider_type = provider_name.split('-')[0] if '-' in provider_name else provider_name
        
        # Generate examples from handler factory
        provider_api = getattr(args, 'provider_api', None)
        examples = _generate_examples_from_factory(provider_type, provider_api)
        
        # Get scheduler strategy class to determine filename (no if/else!)
        from config.platform_dirs import get_config_location
        from infrastructure.registry.scheduler_registry import get_scheduler_registry
        
        config_dir = get_config_location()
        config_file = config_dir / "config.json"
        
        # Load config to get scheduler type and pass to strategy
        scheduler_type = "default"
        config_dict = None
        if config_file.exists():
            with open(config_file) as f:
                config_dict = json.load(f)
                scheduler_type = config_dict.get("scheduler", {}).get("type", "default")
        
        # Get strategy class from registry (auto-discovers, no manual mapping)
        registry = get_scheduler_registry()
        strategy_class = registry.get_strategy_class(scheduler_type)
        
        # Call classmethod on strategy (polymorphism, no if/else)
        filename = strategy_class.get_templates_filename(provider_name, provider_type, config_dict)
        
        # Get config directory
        config_dir.mkdir(parents=True, exist_ok=True)
        
        # Format templates for scheduler's expected input format
        container = get_container()
        scheduler_strategy = container.get(SchedulerPort)
        formatted_examples = scheduler_strategy.format_templates_for_generation(examples)
        
        # Write templates file
        templates_data = {"templates": formatted_examples}
        templates_file = config_dir / filename
        
        with open(templates_file, 'w') as f:
            json.dump(templates_data, f, indent=2)
        
        # Print success message
        print_success(f"Generated {len(examples)} example templates")
        print_info(f"  File: {templates_file}")
        print_info("")  # Empty line
        print_info("Templates created:")
        for template in examples:
            print_info(f"  - {template.get('template_id', 'unknown')}")
        
        return {
            "status": "success",
            "message": f"Generated {len(examples)} example templates",
            "filename": filename,
            "path": str(templates_file),
            "templates": [t.get("template_id", "unknown") for t in examples]
        }
        
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "message": f"Failed to generate templates: {e}",
            "traceback": traceback.format_exc()
        }


def _generate_examples_from_factory(provider_type: str, provider_api: str = None) -> list[Dict[str, Any]]:
    """Generate example templates using handler factory."""
    if provider_type == "aws":
        from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
        from infrastructure.di.container import get_container
        
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