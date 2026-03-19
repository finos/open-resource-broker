"""Infrastructure command handlers for CLI."""

import json
from typing import Any, Dict, List

from orb.config.platform_dirs import get_config_location
from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.di.container import get_container


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
            "error": f"Infrastructure discovery failed: {e}",
            "status": "error",
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

        provider_data = []
        for provider in providers:
            _show_provider_infrastructure(provider)
            provider_data.append(provider)

        return {"status": "success", "providers": provider_data}

    except Exception as e:
        return {
            "error": f"Failed to show infrastructure: {e}",
            "status": "error",
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
            "error": f"Infrastructure validation failed: {e}",
            "status": "error",
        }


async def _discover_provider_infrastructure(provider: Dict[str, Any], args) -> Dict[str, Any]:
    """Discover infrastructure for a provider using strategy pattern."""
    try:
        from orb.domain.base.ports.provider_discovery_port import ProviderDiscoveryPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        provider_strategy = container.get(ProviderDiscoveryPort)

        # Pass CLI args to the provider strategy
        provider_with_args = {**provider, "cli_args": args}

        # Use the provider strategy to discover infrastructure
        return provider_strategy.discover_infrastructure(provider_with_args)

    except Exception as e:
        get_container().get(ConsolePort).error(
            f"Failed to discover infrastructure for {provider['name']}: {e}"
        )
        return {"provider": provider["name"], "error": str(e)}


def _show_provider_infrastructure(provider: Dict[str, Any]) -> None:
    """Show infrastructure configuration for a provider."""
    console = get_container().get(ConsolePort)
    console.info(f"\nProvider: {provider['name']}")
    console.info(f"Type: {provider['type']}")

    config = provider.get("config", {})
    if config:
        console.info(f"Region: {config.get('region', 'N/A')}")
        console.info(f"Profile: {config.get('profile', 'N/A')}")

    template_defaults = provider.get("template_defaults", {})
    if template_defaults:
        console.info("\nInfrastructure Defaults:")
        for key, value in template_defaults.items():
            label = key.replace("_", " ").title()
            if isinstance(value, list):
                console.info(f"  {label} ({len(value)}):")
                for item in value:
                    console.info(f"    - {item}")
            else:
                console.info(f"  {label}: {value}")
    else:
        console.info("\nNo infrastructure defaults configured")
        console.info("To configure infrastructure defaults:")
        console.info("  1. Run: orb init --interactive")
        console.info("  2. Or run: orb infra discover (to see available infrastructure)")
        console.info("  3. Then manually add template_defaults to your provider config")

    console.separator(char="-")


async def _validate_provider_infrastructure(provider: Dict[str, Any]) -> Dict[str, Any]:
    """Validate infrastructure for a provider using strategy pattern."""
    try:
        from orb.domain.base.ports.provider_discovery_port import ProviderDiscoveryPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        provider_strategy = container.get(ProviderDiscoveryPort)

        # Check if provider strategy supports infrastructure validation
        if hasattr(provider_strategy, "validate_infrastructure"):
            return provider_strategy.validate_infrastructure(provider)
        else:
            get_container().get(ConsolePort).info(
                f"Infrastructure validation not supported for provider: {provider['name']}"
            )
            return {
                "provider": provider["name"],
                "error": "Infrastructure validation not supported",
            }

    except Exception as e:
        get_container().get(ConsolePort).error(
            f"Failed to validate infrastructure for {provider['name']}: {e}"
        )
        return {"provider": provider["name"], "error": str(e)}


def _get_active_providers() -> List[Dict[str, Any]]:
    """Get all active providers from configuration."""
    config_dir = get_config_location()
    config_file = config_dir / "config.json"

    if not config_file.exists():
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.infrastructure.di.container import get_container

        registry_service = get_container().get(ProviderRegistryService)
        registered_types = registry_service.get_registered_provider_types()
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
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.infrastructure.di.container import get_container

        registry_service = get_container().get(ProviderRegistryService)
        registered_types = registry_service.get_registered_provider_types()
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
