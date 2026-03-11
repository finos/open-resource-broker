"""Provider configuration command handlers."""

import json
import re
from typing import Any, Dict

from orb.config.platform_dirs import get_config_location
from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.di.container import get_container
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def handle_provider_add(args) -> int:
    """Handle orb providers add command."""
    try:
        console = get_container().get(ConsolePort)
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found. Run 'orb init' first.")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Validate required arguments
        if not args.aws_profile:
            console.error("--aws-profile is required")
            return 1
        if not args.aws_region:
            console.error("--aws-region is required")
            return 1

        # Test credentials
        console.info("Testing credentials...")
        success, error = _test_provider_credentials(
            "aws", {"profile": args.aws_profile, "region": args.aws_region}
        )
        if not success:
            console.error(f"Credential test failed: {error}")
            return 1

        console.success("Credentials verified successfully")

        # Discover infrastructure if requested
        infrastructure_defaults = {}
        if args.discover:
            console.info("Discovering infrastructure...")
            infrastructure_defaults = _discover_infrastructure(
                "aws", args.aws_region, args.aws_profile
            )

        # Generate provider name
        provider_name = args.name or _generate_provider_name(
            "aws", args.aws_profile, args.aws_region
        )

        # Check if provider already exists
        existing_providers = config.get("provider", {}).get("providers", [])
        if any(p["name"] == provider_name for p in existing_providers):
            console.error(f"Provider '{provider_name}' already exists")
            return 1

        # Create provider instance
        provider_instance = {
            "name": provider_name,
            "type": "aws",
            "enabled": True,
            "config": {"profile": args.aws_profile, "region": args.aws_region},
        }

        if infrastructure_defaults:
            provider_instance["template_defaults"] = infrastructure_defaults

        # Add to config
        config.setdefault("provider", {}).setdefault("providers", []).append(provider_instance)

        # Write updated config
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
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Find and remove provider
        providers = config.get("provider", {}).get("providers", [])
        original_count = len(providers)

        providers[:] = [p for p in providers if p["name"] != args.provider_name]

        if len(providers) == original_count:
            console.error(f"Provider '{args.provider_name}' not found")
            return 1

        if len(providers) == 0:
            console.error("Cannot remove last provider")
            return 1

        # Write updated config
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
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Find provider
        providers = config.get("provider", {}).get("providers", [])
        provider = None
        for p in providers:
            if p["name"] == args.provider_name:
                provider = p
                break

        if not provider:
            console.error(f"Provider '{args.provider_name}' not found")
            return 1

        # Update configuration
        provider_config = provider.get("config", {})
        updated = False

        if args.aws_region:
            provider_config["region"] = args.aws_region
            updated = True

        if args.aws_profile:
            provider_config["profile"] = args.aws_profile
            updated = True

        if not updated:
            console.error("No updates specified. Use --aws-region or --aws-profile")
            return 1

        # Test updated credentials
        console.info("Testing updated credentials...")
        success, error = _test_provider_credentials(provider["type"], provider_config)
        if not success:
            console.error(f"Credential test failed: {error}")
            return 1

        console.success("Updated credentials verified successfully")

        # Write updated config
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
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Check if provider exists
        providers = config.get("provider", {}).get("providers", [])
        if not any(p["name"] == args.provider_name for p in providers):
            console.error(f"Provider '{args.provider_name}' not found")
            return 1

        # Set default provider
        config.setdefault("provider", {})["default_provider"] = args.provider_name

        # Write updated config
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
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Get default provider
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
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            console.error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])

        if args.provider_name:
            # Show specific provider
            provider = None
            for p in providers:
                if p["name"] == args.provider_name:
                    provider = p
                    break

            if not provider:
                console.error(f"Provider '{args.provider_name}' not found")
                return 1

            console.info(f"Provider: {provider['name']}")
            console.info(f"Type: {provider['type']}")
            console.info(f"Region: {provider['config']['region']}")
            console.info(f"Profile: {provider['config']['profile']}")
            console.info(f"Enabled: {provider.get('enabled', True)}")

            if provider.get("template_defaults"):
                console.info("Template Defaults:")
                for key, value in provider["template_defaults"].items():
                    label = key.replace("_", " ").title()
                    if isinstance(value, list):
                        console.info(f"  {label}: {', '.join(value)}")
                    else:
                        console.info(f"  {label}: {value}")
        else:
            # Show default provider
            default_provider = config.get("provider", {}).get("default_provider")

            if default_provider:
                # Find and show default provider
                for p in providers:
                    if p["name"] == default_provider:
                        console.info(f"Default Provider: {p['name']}")
                        console.info(f"Type: {p['type']}")
                        console.info(f"Region: {p['config']['region']}")
                        console.info(f"Profile: {p['config']['profile']}")
                        break
            elif providers:
                first_provider = providers[0]
                console.info(f"No explicit default set. First provider: {first_provider['name']}")
                console.info(f"Type: {first_provider['type']}")
                console.info(f"Region: {first_provider['config']['region']}")
                console.info(f"Profile: {first_provider['config']['profile']}")
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


def _discover_infrastructure(provider_type: str, region: str, profile: str) -> Dict[str, Any]:
    """Discover infrastructure using provider strategy."""
    try:
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.infrastructure.di.container import get_container

        registry_service = get_container().get(ProviderRegistryService)

        if not registry_service.ensure_provider_registered(provider_type):
            logger.warning(f"Failed to register provider type: {provider_type}", exc_info=True)
            return {}

        provider_config = {"region": region, "profile": profile}
        strategy = registry_service.get_or_create_strategy(provider_type, provider_config)

        if hasattr(strategy, "discover_infrastructure_interactive"):
            full_config = {"type": provider_type, "config": provider_config}
            return strategy.discover_infrastructure_interactive(full_config)  # type: ignore[union-attr]
        else:
            logger.info(
                f"Infrastructure discovery not supported for provider type: {provider_type}"
            )
            return {}

    except Exception as e:
        logger.error(f"Failed to discover infrastructure: {e}", exc_info=True)
        return {}


def _generate_provider_name(provider_type: str, profile: str, region: str) -> str:
    """Generate provider name with proper sanitization."""
    try:
        from orb.application.services.provider_registry_service import ProviderRegistryService
        from orb.infrastructure.di.container import get_container

        registry_service = get_container().get(ProviderRegistryService)
        temp_config = {"type": provider_type, "profile": profile, "region": region}
        strategy = registry_service.get_or_create_strategy(provider_type, temp_config)
        if strategy is not None:
            return strategy.generate_provider_name({"profile": profile, "region": region})
    except Exception:
        pass
    # Fallback to simple name generation
    sanitized_profile = re.sub(r"[^a-zA-Z0-9\-_]", "-", profile)
    return f"{provider_type}_{sanitized_profile}_{region}"
