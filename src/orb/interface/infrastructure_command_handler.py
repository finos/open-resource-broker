"""Infrastructure command handlers for CLI."""

import json
from typing import Any, Dict, List

from orb.cli.console import print_error, print_info, print_separator
from orb.config.platform_dirs import get_config_location


async def handle_infrastructure_discover(args) -> Dict[str, Any]:
    """Handle orb infrastructure discover command."""
    try:
        if args.provider:
            providers = [_get_provider_config(args.provider)]
        elif getattr(args, "all_providers", False):
            providers = _get_active_providers_with_overrides()
        else:
            providers = _get_active_providers_with_overrides()

        results = []
        for provider in providers:
            result = await _discover_provider_infrastructure(provider, args)
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
            providers = _get_active_providers_with_overrides()
        else:
            providers = _get_active_providers_with_overrides()

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
            providers = _get_active_providers_with_overrides()

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


async def _discover_provider_infrastructure(provider: Dict[str, Any], args) -> Dict[str, Any]:
    """Discover infrastructure for a provider using strategy pattern."""
    try:
        from orb.domain.base.ports.provider_port import ProviderPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        provider_strategy = container.get(ProviderPort)

        # Pass CLI args to the provider strategy
        provider_with_args = {**provider, "cli_args": args}

        # Use the provider strategy to discover infrastructure
        return provider_strategy.discover_infrastructure(provider_with_args)

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
        for key, value in template_defaults.items():
            label = key.replace("_", " ").title()
            if isinstance(value, list):
                print_info(f"  {label} ({len(value)}):")
                for item in value:
                    print_info(f"    - {item}")
            else:
                print_info(f"  {label}: {value}")
    else:
        print_info("\nNo infrastructure defaults configured")
        print_info("To configure infrastructure defaults:")
        print_info("  1. Run: orb init --interactive")
        print_info("  2. Or run: orb infra discover (to see available infrastructure)")
        print_info("  3. Then manually add template_defaults to your provider config")

    print_separator(char="-")


async def _validate_provider_infrastructure(provider: Dict[str, Any]) -> Dict[str, Any]:
    """Validate infrastructure for a provider using strategy pattern."""
    try:
        from orb.domain.base.ports.provider_port import ProviderPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        provider_strategy = container.get(ProviderPort)

        # Check if provider strategy supports infrastructure validation
        if hasattr(provider_strategy, "validate_infrastructure"):
            return provider_strategy.validate_infrastructure(provider)
        else:
            print_info(f"Infrastructure validation not supported for provider: {provider['name']}")
            return {
                "provider": provider["name"],
                "error": "Infrastructure validation not supported",
            }

    except Exception as e:
        print_error(f"Failed to validate infrastructure for {provider['name']}: {e}")
        return {"provider": provider["name"], "error": str(e)}


def _get_active_providers() -> List[Dict[str, Any]]:
    """Get all active providers from configuration."""
    config_dir = get_config_location()
    config_file = config_dir / "config.json"

    if not config_file.exists():
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registered_types = registry.get_registered_providers()
        default_type = registered_types[0] if registered_types else "aws"
        return [
            {
                "name": "default",
                "type": default_type,
            }
        ]

    with open(config_file) as f:
        config_dict = json.load(f)

    provider_config = config_dict.get("provider", {})
    providers = provider_config.get("providers", [])

    # Return enabled providers
    active_providers = []
    for provider in providers:
        if provider.get("enabled", True):
            active_providers.append(provider)

    if not active_providers:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registered_types = registry.get_registered_providers()
        default_type = registered_types[0] if registered_types else "aws"
        active_providers = [
            {
                "name": "default",
                "type": default_type,
            }
        ]

    return active_providers


def _get_active_providers_with_overrides() -> List[Dict[str, Any]]:
    """Get active providers with global overrides applied."""
    providers = _get_active_providers()

    # Apply global overrides
    try:
        from orb.domain.base.ports.configuration_port import ConfigurationPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        config = container.get(ConfigurationPort)

        for provider in providers:
            provider_config = provider.get("config", {})

            # Apply region override if present in config
            region = provider_config.get("region")
            if region is not None:
                provider_config["region"] = config.get_effective_region(region)

            # Apply profile override if present in config
            profile = provider_config.get("profile")
            if profile is not None:
                provider_config["profile"] = config.get_effective_profile(profile)

            if provider_config:
                provider["config"] = provider_config
    except Exception as e:
        # Fallback to original providers if override fails
        from orb.infrastructure.logging.logger import get_logger

        logger = get_logger(__name__)
        logger.debug(f"Failed to override provider config: {e}")

    return providers


def _get_provider_config(provider_name: str) -> Dict[str, Any]:
    """Get configuration for specific provider."""
    active_providers = _get_active_providers()

    for provider in active_providers:
        if provider["name"] == provider_name:
            return provider

    raise ValueError(f"Provider '{provider_name}' not found in configuration")
