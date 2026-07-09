"""Infrastructure command handlers for CLI."""

import json
from typing import Any, Dict, List

from orb.config.platform_dirs import get_config_location
from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.error.decorators import handle_interface_exceptions
from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry


@handle_interface_exceptions(context="infrastructure_discover", interface_type="cli")
async def handle_infrastructure_discover(args) -> Dict[str, Any]:
    """Handle orb infrastructure discover command."""
    container = args._container
    try:
        if args.provider_name:
            providers = [_get_provider_config(args.provider_name, container)]
        else:
            providers = _get_active_providers_with_overrides(container)

        results = []
        for provider in providers:
            result = await _discover_provider_infrastructure(provider, args, container)
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


@handle_interface_exceptions(context="infrastructure_show", interface_type="cli")
async def handle_infrastructure_show(args) -> Dict[str, Any]:
    """Handle orb infrastructure show command."""
    container = args._container
    try:
        if args.provider_name:
            providers = [_get_provider_config(args.provider_name, container)]
        else:
            providers = _get_active_providers_with_overrides(container)

        provider_data = []
        for provider in providers:
            _show_provider_infrastructure(provider, container)
            provider_data.append(provider)

        return {"status": "success", "providers": provider_data}

    except Exception as e:
        return {
            "error": f"Failed to show infrastructure: {e}",
            "status": "error",
        }


@handle_interface_exceptions(context="infrastructure_validate", interface_type="cli")
async def handle_infrastructure_validate(args) -> Dict[str, Any]:
    """Handle orb infrastructure validate command."""
    container = args._container
    try:
        if args.provider_name:
            providers = [_get_provider_config(args.provider_name, container)]
        else:
            providers = _get_active_providers_with_overrides(container)

        results = []
        for provider in providers:
            result = await _validate_provider_infrastructure(provider, container)
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


async def _discover_provider_infrastructure(
    provider: Dict[str, Any], args, container
) -> Dict[str, Any]:
    """Discover infrastructure for a provider using strategy pattern."""
    try:
        from orb.domain.base.ports.provider_discovery_port import ProviderDiscoveryPort

        provider_strategy = container.get(ProviderDiscoveryPort)

        # Pass CLI args to the provider strategy
        provider_with_args = {**provider, "cli_args": args}

        # Use the provider strategy to discover infrastructure
        return provider_strategy.discover_infrastructure(provider_with_args)

    except Exception as e:
        container.get(ConsolePort).error(
            f"Failed to discover infrastructure for {provider['name']}: {e}"
        )
        return {"provider": provider["name"], "error": str(e)}


def _show_provider_infrastructure(provider: Dict[str, Any], container) -> None:
    """Show infrastructure configuration for a provider."""
    console = container.get(ConsolePort)
    console.info(f"\nProvider: {provider['name']}")
    console.info(f"Type: {provider['type']}")

    config = provider.get("config", {})
    if config:
        provider_type = provider.get("type", "")
        spec = CLISpecRegistry.get_or_none(provider_type)
        if spec is not None:
            for label, value in spec.format_display(config):
                console.info(f"{label}: {value}")
        else:
            for key, value in config.items():
                label = key.replace("_", " ").title()
                console.info(f"{label}: {value}")

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


async def _validate_provider_infrastructure(provider: Dict[str, Any], container) -> Dict[str, Any]:
    """Validate infrastructure for a provider using strategy pattern."""
    try:
        from orb.domain.base.ports.provider_discovery_port import ProviderDiscoveryPort

        provider_strategy = container.get(ProviderDiscoveryPort)

        # Check if provider strategy supports infrastructure validation
        if hasattr(provider_strategy, "validate_infrastructure"):
            return provider_strategy.validate_infrastructure(provider)
        else:
            container.get(ConsolePort).info(
                f"Infrastructure validation not supported for provider: {provider['name']}"
            )
            return {
                "provider": provider["name"],
                "error": "Infrastructure validation not supported",
            }

    except Exception as e:
        container.get(ConsolePort).error(
            f"Failed to validate infrastructure for {provider['name']}: {e}"
        )
        return {"provider": provider["name"], "error": str(e)}


def _get_active_providers(container) -> List[Dict[str, Any]]:
    """Get all active providers from configuration."""
    config_dir = get_config_location()
    config_file = config_dir / "config.json"

    if not config_file.exists():
        from orb.application.services.provider_registry_service import ProviderRegistryService

        registry_service = container.get(ProviderRegistryService)
        registered_types = registry_service.get_registered_provider_types()
        if not registered_types:
            raise RuntimeError(
                "No providers are registered. Run 'orb init' to configure a provider."
            )
        default_type = registered_types[0]
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

        registry_service = container.get(ProviderRegistryService)
        registered_types = registry_service.get_registered_provider_types()
        if not registered_types:
            raise RuntimeError(
                "No providers are registered. Run 'orb init' to configure a provider."
            )
        default_type = registered_types[0]
        active_providers = [
            {
                "name": "default",
                "type": default_type,
            }
        ]

    return active_providers


def _get_active_providers_with_overrides(container) -> List[Dict[str, Any]]:
    """Get active providers with provider-name and provider-type overrides applied.

    Provider-name and provider-type filtering is a per-operation concern handled
    by each orchestrator's Input DTO.  Provider-specific config key overrides
    (e.g. AWS region, AWS profile) are not applied here because they are
    provider-scoped and do not belong in the provider-agnostic interface layer.
    """
    return _get_active_providers(container)


def _get_provider_config(provider_name: str, container) -> Dict[str, Any]:
    """Get configuration for specific provider."""
    active_providers = _get_active_providers(container)

    for provider in active_providers:
        if provider["name"] == provider_name:
            return provider

    raise ValueError(f"Provider '{provider_name}' not found in configuration")
