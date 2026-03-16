"""Provider configuration command handlers."""

import json
from typing import Any, Dict

from orb.config.platform_dirs import get_config_location
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
from orb.infrastructure.di.container import get_container
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def handle_provider_add(args) -> int:
    """Handle orb providers add command."""
    try:
        console = get_container().get(ConsolePort)
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found. Run 'orb init' first.")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        provider_type = getattr(args, "provider_type", "aws")
        spec = CLISpecRegistry.get(provider_type)

        if spec is None:
            console.error(f"Unknown provider type: {provider_type}")
            return 1

        errors = spec.validate_add(args)
        for err in errors:
            console.error(err)
        if errors:
            return 1

        provider_config = spec.extract_config(args)

        # Test credentials
        console.info("Testing credentials...")
        success, error = _test_provider_credentials(provider_type, provider_config)
        if not success:
            console.error(f"Credential test failed: {error}")
            return 1

        console.success("Credentials verified successfully")

        # Discover infrastructure if requested
        infrastructure_defaults = {}
        if args.discover:
            console.info("Discovering infrastructure...")
            infrastructure_defaults = _discover_infrastructure(provider_type, provider_config)

        # Generate provider name
        provider_name = args.name or spec.generate_name(args)

        # Check if provider already exists
        existing_providers = config.get("provider", {}).get("providers", [])
        if any(p["name"] == provider_name for p in existing_providers):
            console.error(f"Provider '{provider_name}' already exists")
            return 1

        # Create provider instance
        provider_instance: dict[str, Any] = {
            "name": provider_name,
            "type": provider_type,
            "enabled": True,
            "config": provider_config,
        }

        if infrastructure_defaults:
            provider_instance["template_defaults"] = infrastructure_defaults

        config.setdefault("provider", {}).setdefault("providers", []).append(provider_instance)

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        console.success(f"Provider '{provider_name}' added successfully")
        return 0

    except Exception as e:
        get_container().get(ConsolePort).error(f"Failed to add provider: {e}")
        logger.error("Failed to add provider: %s", e, exc_info=True)
        return 1


async def handle_provider_remove(args) -> int:
    """Handle orb providers remove command."""
    try:
        console = get_container().get(ConsolePort)
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])
        original_count = len(providers)

        providers[:] = [p for p in providers if p["name"] != args.provider_name]

        if len(providers) == original_count:
            console.error(f"Provider '{args.provider_name}' not found")
            return 1

        if len(providers) == 0:
            console.error("Cannot remove last provider")
            return 1

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        console.success(f"Provider '{args.provider_name}' removed successfully")
        return 0

    except Exception as e:
        get_container().get(ConsolePort).error(f"Failed to remove provider: {e}")
        logger.error("Failed to remove provider: %s", e, exc_info=True)
        return 1


async def handle_provider_update(args) -> int:
    """Handle orb providers update command."""
    try:
        console = get_container().get(ConsolePort)
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])
        provider = None
        for p in providers:
            if p["name"] == args.provider_name:
                provider = p
                break

        if not provider:
            console.error(f"Provider '{args.provider_name}' not found")
            return 1

        # Infer provider type from stored record
        provider_type = provider.get("type", "aws")
        spec = CLISpecRegistry.get(provider_type)

        provider_config = provider.get("config", {})

        if spec is not None:
            partial = spec.extract_partial_config(args)
            if not partial:
                console.error("No updates specified.")
                return 1
            provider_config.update(partial)
        else:
            # Fallback: apply any non-None aws_* attrs directly
            updated = False
            if getattr(args, "aws_region", None):
                provider_config["region"] = args.aws_region
                updated = True
            if getattr(args, "aws_profile", None):
                provider_config["profile"] = args.aws_profile
                updated = True
            if not updated:
                console.error("No updates specified.")
                return 1

        # Test updated credentials
        console.info("Testing updated credentials...")
        success, error = _test_provider_credentials(provider_type, provider_config)
        if not success:
            console.error(f"Credential test failed: {error}")
            return 1

        console.success("Updated credentials verified successfully")

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        console.success(f"Provider '{args.provider_name}' updated successfully")
        return 0

    except Exception as e:
        get_container().get(ConsolePort).error(f"Failed to update provider: {e}")
        logger.error("Failed to update provider: %s", e, exc_info=True)
        return 1


async def handle_provider_set_default(args) -> int:
    """Handle orb providers set-default command."""
    try:
        console = get_container().get(ConsolePort)
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])
        if not any(p["name"] == args.provider_name for p in providers):
            console.error(f"Provider '{args.provider_name}' not found")
            return 1

        config.setdefault("provider", {})["default_provider"] = args.provider_name

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        console.success(f"Default provider set to '{args.provider_name}'")
        return 0

    except Exception as e:
        get_container().get(ConsolePort).error(f"Failed to set default provider: {e}")
        logger.error("Failed to set default provider: %s", e, exc_info=True)
        return 1


async def handle_provider_get_default(args) -> int:
    """Handle orb providers get-default command."""
    try:
        console = get_container().get(ConsolePort)
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        default_provider = config.get("provider", {}).get("default_provider")

        if default_provider:
            console.success(f"Default provider: {default_provider}")
        else:
            providers = config.get("provider", {}).get("providers", [])
            if providers:
                first_provider = providers[0]["name"]
                console.info(f"No explicit default set. Using first provider: {first_provider}")
            else:
                console.error("No providers configured")
                return 1

        return 0

    except Exception as e:
        get_container().get(ConsolePort).error(f"Failed to get default provider: {e}")
        logger.error("Failed to get default provider: %s", e, exc_info=True)
        return 1


async def handle_provider_show(args) -> int:
    """Handle orb providers show command."""
    try:
        console = get_container().get(ConsolePort)
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])

        def _display_provider(p: dict) -> None:
            console.info(f"Provider: {p['name']}")
            console.info(f"Type: {p['type']}")
            spec = CLISpecRegistry.get(p.get("type", ""))
            if spec is not None:
                for label, value in spec.format_display(p.get("config", {})):
                    console.info(f"{label}: {value}")
            else:
                for key, value in p.get("config", {}).items():
                    console.info(f"{key}: {value}")
            console.info(f"Enabled: {p.get('enabled', True)}")
            if p.get("template_defaults"):
                console.info("Template Defaults:")
                for key, value in p["template_defaults"].items():
                    label = key.replace("_", " ").title()
                    if isinstance(value, list):
                        console.info(f"  {label}: {', '.join(value)}")
                    else:
                        console.info(f"  {label}: {value}")

        if args.provider_name:
            provider = next((p for p in providers if p["name"] == args.provider_name), None)
            if not provider:
                console.error(f"Provider '{args.provider_name}' not found")
                return 1
            _display_provider(provider)
        else:
            default_provider = config.get("provider", {}).get("default_provider")
            if default_provider:
                for p in providers:
                    if p["name"] == default_provider:
                        console.info(f"Default Provider: {p['name']}")
                        _display_provider(p)
                        break
            elif providers:
                first_provider = providers[0]
                console.info(f"No explicit default set. First provider: {first_provider['name']}")
                _display_provider(first_provider)
            else:
                console.error("No providers configured")
                return 1

        return 0

    except Exception as e:
        get_container().get(ConsolePort).error(f"Failed to show provider: {e}")
        logger.error("Failed to show provider: %s", e, exc_info=True)
        return 1


def _test_provider_credentials(provider_type: str, credential_config: dict) -> tuple[bool, str]:
    """Test provider credentials via the provider strategy."""
    try:
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.infrastructure.di.container import get_container

        registry_service = get_container().get(ProviderRegistryService)

        if not registry_service.ensure_provider_registered(provider_type):
            return False, f"Provider type not supported: {provider_type}"

        strategy = registry_service.get_or_create_strategy(provider_type, credential_config)

        if strategy is None:
            return False, f"Failed to create strategy for provider type: {provider_type}"

        result = strategy.test_credentials()
        if result.get("success", False):
            return True, ""
        return False, result.get("error", "Unknown error")
    except Exception as e:
        return False, str(e)


def _discover_infrastructure(provider_type: str, provider_config: Dict[str, Any]) -> Dict[str, Any]:
    """Discover infrastructure using provider strategy."""
    try:
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.infrastructure.di.container import get_container

        registry_service = get_container().get(ProviderRegistryService)

        if not registry_service.ensure_provider_registered(provider_type):
            logger.warning("Failed to register provider type: %s", provider_type)
            return {}

        strategy = registry_service.get_or_create_strategy(provider_type, provider_config)

        if hasattr(strategy, "discover_infrastructure_interactive"):
            full_config = {"type": provider_type, "config": provider_config}
            return strategy.discover_infrastructure_interactive(full_config)  # type: ignore[union-attr]
        else:
            logger.info(
                "Infrastructure discovery not supported for provider type: %s", provider_type
            )
            return {}

    except Exception as e:
        logger.error("Failed to discover infrastructure: %s", e, exc_info=True)
        return {}
