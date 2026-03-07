"""Provider configuration command handlers."""

import json
import re
from typing import Any, Dict, Optional

from orb.cli.console import print_error, print_info, print_success
from orb.config.platform_dirs import get_config_location
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def handle_provider_add(args) -> int:
    """Handle orb providers add command."""
    try:
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            print_error("No configuration found. Run 'orb init' first.")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Validate required arguments
        if not args.aws_profile:
            print_error("--aws-profile is required")
            return 1
        if not args.aws_region:
            print_error("--aws-region is required")
            return 1

        # Test credentials
        print_info("Testing credentials...")
        success, error = _test_provider_credentials("aws", args.aws_profile, region=args.aws_region)
        if not success:
            print_error(f"Credential test failed: {error}")
            return 1

        print_success("Credentials verified successfully")

        # Discover infrastructure if requested
        infrastructure_defaults = {}
        if args.discover:
            print_info("Discovering infrastructure...")
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
            print_error(f"Provider '{provider_name}' already exists")
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

        print_success(f"Provider '{provider_name}' added successfully")
        return 0

    except Exception as e:
        print_error(f"Failed to add provider: {e}")
        logger.error("Failed to add provider: %s", e, exc_info=True)
        return 1


async def handle_provider_remove(args) -> int:
    """Handle orb providers remove command."""
    try:
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            print_error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Find and remove provider
        providers = config.get("provider", {}).get("providers", [])
        original_count = len(providers)

        providers[:] = [p for p in providers if p["name"] != args.provider_name]

        if len(providers) == original_count:
            print_error(f"Provider '{args.provider_name}' not found")
            return 1

        if len(providers) == 0:
            print_error("Cannot remove last provider")
            return 1

        # Write updated config
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        print_success(f"Provider '{args.provider_name}' removed successfully")
        return 0

    except Exception as e:
        print_error(f"Failed to remove provider: {e}")
        logger.error("Failed to remove provider: %s", e, exc_info=True)
        return 1


async def handle_provider_update(args) -> int:
    """Handle orb providers update command."""
    try:
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            print_error("No configuration found")
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
            print_error(f"Provider '{args.provider_name}' not found")
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
            print_error("No updates specified. Use --aws-region or --aws-profile")
            return 1

        # Test updated credentials
        print_info("Testing updated credentials...")
        success, error = _test_provider_credentials(
            provider["type"], provider_config.get("profile"), region=provider_config.get("region")
        )
        if not success:
            print_error(f"Credential test failed: {error}")
            return 1

        print_success("Updated credentials verified successfully")

        # Write updated config
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        print_success(f"Provider '{args.provider_name}' updated successfully")
        return 0

    except Exception as e:
        print_error(f"Failed to update provider: {e}")
        logger.error("Failed to update provider: %s", e, exc_info=True)
        return 1


async def handle_provider_set_default(args) -> int:
    """Handle orb providers set-default command."""
    try:
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            print_error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Check if provider exists
        providers = config.get("provider", {}).get("providers", [])
        if not any(p["name"] == args.provider_name for p in providers):
            print_error(f"Provider '{args.provider_name}' not found")
            return 1

        # Set default provider
        config.setdefault("provider", {})["default_provider"] = args.provider_name

        # Write updated config
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        print_success(f"Default provider set to '{args.provider_name}'")
        return 0

    except Exception as e:
        print_error(f"Failed to set default provider: {e}")
        logger.error("Failed to set default provider: %s", e, exc_info=True)
        return 1


async def handle_provider_get_default(args) -> int:
    """Handle orb providers get-default command."""
    try:
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            print_error("No configuration found")
            return 1

        with open(config_file) as f:
            config = json.load(f)

        # Get default provider
        default_provider = config.get("provider", {}).get("default_provider")

        if default_provider:
            print_success(f"Default provider: {default_provider}")
        else:
            providers = config.get("provider", {}).get("providers", [])
            if providers:
                first_provider = providers[0]["name"]
                print_info(f"No explicit default set. Using first provider: {first_provider}")
            else:
                print_error("No providers configured")
                return 1

        return 0

    except Exception as e:
        print_error(f"Failed to get default provider: {e}")
        logger.error("Failed to get default provider: %s", e, exc_info=True)
        return 1


async def handle_provider_show(args) -> int:
    """Handle orb providers show command."""
    try:
        # Load existing config
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            print_error("No configuration found")
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
                print_error(f"Provider '{args.provider_name}' not found")
                return 1

            print_info(f"Provider: {provider['name']}")
            print_info(f"Type: {provider['type']}")
            print_info(f"Region: {provider['config']['region']}")
            print_info(f"Profile: {provider['config']['profile']}")
            print_info(f"Enabled: {provider.get('enabled', True)}")

            if provider.get("template_defaults"):
                print_info("Template Defaults:")
                for key, value in provider["template_defaults"].items():
                    label = key.replace("_", " ").title()
                    if isinstance(value, list):
                        print_info(f"  {label}: {', '.join(value)}")
                    else:
                        print_info(f"  {label}: {value}")
        else:
            # Show default provider
            default_provider = config.get("provider", {}).get("default_provider")

            if default_provider:
                # Find and show default provider
                for p in providers:
                    if p["name"] == default_provider:
                        print_info(f"Default Provider: {p['name']}")
                        print_info(f"Type: {p['type']}")
                        print_info(f"Region: {p['config']['region']}")
                        print_info(f"Profile: {p['config']['profile']}")
                        break
            elif providers:
                first_provider = providers[0]
                print_info(f"No explicit default set. First provider: {first_provider['name']}")
                print_info(f"Type: {first_provider['type']}")
                print_info(f"Region: {first_provider['config']['region']}")
                print_info(f"Profile: {first_provider['config']['profile']}")
            else:
                print_error("No providers configured")
                return 1

        return 0

    except Exception as e:
        print_error(f"Failed to show provider: {e}")
        logger.error("Failed to show provider: %s", e, exc_info=True)
        return 1


def _test_provider_credentials(
    provider_type: str, profile: Optional[str], **kwargs
) -> tuple[bool, str]:
    """Test provider credentials."""
    if provider_type == "aws":
        try:
            from orb.providers.aws.session_factory import AWSSessionFactory

            region = kwargs.get("region")
            result = AWSSessionFactory.discover_credentials(profile, region)
            if result.get("success", False):
                return True, ""
            else:
                return False, result.get("error", "Unknown error")
        except Exception as e:
            return False, str(e)
    else:
        return False, "Provider type not supported"


def _discover_infrastructure(provider_type: str, region: str, profile: str) -> Dict[str, Any]:
    """Discover infrastructure using provider strategy."""
    try:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        # Ensure provider type is registered
        if not registry.ensure_provider_type_registered(provider_type):
            logger.warning(f"Failed to register provider type: {provider_type}", exc_info=True)
            return {}

        # Create provider config for discovery
        provider_config = {"region": region, "profile": profile}

        # Get strategy from registry
        strategy = registry.get_or_create_strategy(provider_type, provider_config)

        # Check if provider strategy supports infrastructure discovery
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
        from orb.infrastructure.di.container import get_container
        from orb.providers.factory import ProviderStrategyFactory

        container = get_container()
        factory = container.get(ProviderStrategyFactory)

        temp_config = {"type": provider_type, "profile": profile, "region": region}
        strategy = factory.create_strategy(provider_type, temp_config)  # type: ignore[attr-defined]
        return strategy.generate_provider_name({"profile": profile, "region": region})
    except Exception:
        # Fallback to simple name generation
        sanitized_profile = re.sub(r"[^a-zA-Z0-9\-_]", "-", profile)
        return f"{provider_type}_{sanitized_profile}_{region}"
