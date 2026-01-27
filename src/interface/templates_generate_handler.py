"""Templates generate command handler."""

import json
from pathlib import Path
from typing import Any, Dict

from cli.console import print_success, print_info, print_command


async def handle_templates_generate(args) -> Dict[str, Any]:
    """Handle orb templates generate command."""
    try:
        # Get provider name from args or use default
        provider_name = getattr(args, 'provider', None) or "aws-default"
        provider_type = provider_name.split('-')[0] if '-' in provider_name else provider_name
        
        # Generate examples
        provider_api = getattr(args, 'provider_api', None)
        examples = _generate_examples(provider_type, provider_api)
        
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
        
        # Write templates file
        templates_data = {"templates": examples}
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


def _generate_examples(provider_type: str, provider_api: str = None) -> list[Dict[str, Any]]:
    """Generate example templates."""
    if provider_type == "aws":
        return _generate_aws_examples(provider_api)
    else:
        return []


def _generate_aws_examples(provider_api: str = None) -> list[Dict[str, Any]]:
    """Generate AWS example templates."""
    examples = []
    
    # EC2Fleet example
    if not provider_api or provider_api == "EC2Fleet":
        examples.append({
            "template_id": "EC2FleetInstant",
            "name": "EC2 Fleet Instant",
            "description": "EC2 Fleet with instant fulfillment",
            "provider_type": "aws",
            "provider_api": "EC2Fleet",
            "instance_type": "t3.medium",
            "max_instances": 10,
            "price_type": "ondemand",
            "subnet_ids": ["subnet-xxxxx"],
            "security_group_ids": ["sg-xxxxx"],
            "tags": {"Environment": "dev", "ManagedBy": "ORB"}
        })
    
    # SpotFleet example
    if not provider_api or provider_api == "SpotFleet":
        examples.append({
            "template_id": "SpotFleet",
            "name": "Spot Fleet",
            "description": "Spot Fleet for cost-effective compute",
            "provider_type": "aws",
            "provider_api": "SpotFleet",
            "instance_types": {"t3.medium": 1, "t3.large": 2},
            "max_instances": 20,
            "price_type": "spot",
            "max_price": 0.05,
            "allocation_strategy": "lowest_price",
            "subnet_ids": ["subnet-xxxxx"],
            "security_group_ids": ["sg-xxxxx"],
            "tags": {"Environment": "dev", "ManagedBy": "ORB"}
        })
    
    # AutoScalingGroup example
    if not provider_api or provider_api == "ASG":
        examples.append({
            "template_id": "AutoScalingGroup",
            "name": "Auto Scaling Group",
            "description": "Auto Scaling Group for dynamic scaling",
            "provider_type": "aws",
            "provider_api": "AutoScalingGroup",
            "instance_type": "t3.medium",
            "max_instances": 15,
            "price_type": "ondemand",
            "subnet_ids": ["subnet-xxxxx"],
            "security_group_ids": ["sg-xxxxx"],
            "tags": {"Environment": "dev", "ManagedBy": "ORB"}
        })
    
    # RunInstances example
    if not provider_api or provider_api == "RunInstances":
        examples.append({
            "template_id": "RunInstances",
            "name": "Run Instances",
            "description": "Simple EC2 instance launch",
            "provider_type": "aws",
            "provider_api": "RunInstances",
            "instance_type": "t3.medium",
            "max_instances": 5,
            "price_type": "ondemand",
            "subnet_ids": ["subnet-xxxxx"],
            "security_group_ids": ["sg-xxxxx"],
            "tags": {"Environment": "dev", "ManagedBy": "ORB"}
        })
    
    return examples