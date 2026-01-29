"""Infrastructure command handlers for CLI."""

import json
from typing import Any, Dict, List

from cli.console import print_error, print_info, print_success, print_separator
from config.platform_dirs import get_config_location


async def handle_infrastructure_discover(args) -> Dict[str, Any]:
    """Handle orb infrastructure discover command."""
    try:
        if args.provider:
            providers = [_get_provider_config(args.provider)]
        elif getattr(args, "all_providers", False):
            providers = _get_active_providers()
        else:
            providers = _get_active_providers()

        results = []
        for provider in providers:
            result = await _discover_provider_infrastructure(provider)
            results.append(result)

        return {
            "status": "success",
            "providers": results,
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Infrastructure discovery failed: {e}",
        }


async def handle_infrastructure_show(args) -> Dict[str, Any]:
    """Handle orb infrastructure show command."""
    try:
        if args.provider:
            providers = [_get_provider_config(args.provider)]
        elif getattr(args, "all_providers", False):
            providers = _get_active_providers()
        else:
            providers = _get_active_providers()

        for provider in providers:
            _show_provider_infrastructure(provider)

        return {"status": "success"}

    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to show infrastructure: {e}",
        }


async def handle_infrastructure_validate(args) -> Dict[str, Any]:
    """Handle orb infrastructure validate command."""
    try:
        if args.provider:
            providers = [_get_provider_config(args.provider)]
        else:
            providers = _get_active_providers()

        results = []
        for provider in providers:
            result = await _validate_provider_infrastructure(provider)
            results.append(result)

        return {
            "status": "success",
            "providers": results,
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Infrastructure validation failed: {e}",
        }


async def _discover_provider_infrastructure(provider: Dict[str, Any]) -> Dict[str, Any]:
    """Discover infrastructure for a provider using strategy pattern."""
    try:
        from infrastructure.di.container import get_container
        from domain.base.ports.provider_port import ProviderPort
        
        container = get_container()
        provider_strategy = container.get(ProviderPort)
        
        # Use the provider strategy to discover infrastructure
        return provider_strategy.discover_infrastructure(provider)
            
    except Exception as e:
        print_error(f"Failed to discover infrastructure for {provider['name']}: {e}")
        return {"provider": provider["name"], "error": str(e)}


def _show_provider_infrastructure(provider: Dict[str, Any]) -> None:
    """Show infrastructure configuration for a provider."""
    print_info(f"\nProvider: {provider['name']}")
    print_info(f"Type: {provider['type']}")
    
    config = provider.get("config", {})
    if config:
        print_info(f"Region: {config.get('region', 'N/A')}")
        print_info(f"Profile: {config.get('profile', 'N/A')}")

    template_defaults = provider.get("template_defaults", {})
    if template_defaults:
        print_info("\nInfrastructure Defaults:")
        if "subnet_ids" in template_defaults:
            subnets = template_defaults["subnet_ids"]
            print_info(f"  Subnets ({len(subnets)}):")
            for subnet in subnets:
                print_info(f"    - {subnet}")
        
        if "security_group_ids" in template_defaults:
            sgs = template_defaults["security_group_ids"]
            print_info(f"  Security Groups ({len(sgs)}):")
            for sg in sgs:
                print_info(f"    - {sg}")
    else:
        print_info("\nNo infrastructure defaults configured")

    print_separator(width=50, char="-")


async def _validate_provider_infrastructure(provider: Dict[str, Any]) -> Dict[str, Any]:
    """Validate infrastructure for a provider using strategy pattern."""
    try:
        from infrastructure.di.container import get_container
        from domain.base.ports.provider_port import ProviderPort
        
        container = get_container()
        provider_strategy = container.get(ProviderPort)
        
        # Check if provider strategy supports infrastructure validation
        if hasattr(provider_strategy, 'validate_infrastructure'):
            return provider_strategy.validate_infrastructure(provider)
        else:
            print_info(f"Infrastructure validation not supported for provider: {provider['name']}")
            return {"provider": provider["name"], "error": "Infrastructure validation not supported"}
            
    except Exception as e:
        print_error(f"Failed to validate infrastructure for {provider['name']}: {e}")
        return {"provider": provider["name"], "error": str(e)}


def _get_active_providers() -> List[Dict[str, Any]]:
    """Get all active providers from configuration."""
    config_dir = get_config_location()
    config_file = config_dir / "config.json"

    if not config_file.exists():
        return [{"name": "default", "type": "aws", "config": {"region": "us-east-1", "profile": "default"}}]

    with open(config_file) as f:
        config_dict = json.load(f)

    provider_config = config_dict.get("provider", {})
    providers = provider_config.get("providers", [])

    # Return enabled providers
    active_providers = []
    for provider in providers:
        if provider.get("enabled", True):
            active_providers.append(provider)

    return active_providers or [{"name": "default", "type": "aws", "config": {"region": "us-east-1", "profile": "default"}}]


def _get_provider_config(provider_name: str) -> Dict[str, Any]:
    """Get configuration for specific provider."""
    active_providers = _get_active_providers()
    
    for provider in active_providers:
        if provider["name"] == provider_name:
            return provider
    
    raise ValueError(f"Provider '{provider_name}' not found in configuration")
