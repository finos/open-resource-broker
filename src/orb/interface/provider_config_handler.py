"""Provider configuration command handlers."""

import json
from typing import Any, Dict, Union

from orb.application.dto.interface_response import InterfaceResponse
from orb.application.services.response_formatting_service import ResponseFormattingService
from orb.config.platform_dirs import get_config_location
from orb.domain.base.ports.provider_cli_spec_port import CLISpecRegistry
from orb.infrastructure.di.container import get_container
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def handle_provider_add(args) -> dict[str, Any]:
    """Handle orb providers add command."""
    try:
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            return {"error": True, "message": "No configuration found. Run 'orb init' first.", "exit_code": 1}

        with open(config_file) as f:
            config = json.load(f)

        provider_type = getattr(args, "provider_type", "aws")
        spec = CLISpecRegistry.get(provider_type)

        if spec is None:
            return {"error": True, "message": f"Unknown provider type: {provider_type}", "exit_code": 1}

        errors = spec.validate_add(args)
        if errors:
            return {"error": True, "message": errors[0], "exit_code": 1}

        provider_config = spec.extract_config(args)

        # Test credentials
        success, error = _test_provider_credentials(provider_type, provider_config)
        if not success:
            return {"error": True, "message": f"Credential test failed: {error}", "exit_code": 1}

        # Discover infrastructure if requested
        infrastructure_defaults = {}
        if args.discover:
            infrastructure_defaults = _discover_infrastructure(provider_type, provider_config)

        # Generate provider name
        provider_name = args.name or spec.generate_name(args)

        # Check if provider already exists
        existing_providers = config.get("provider", {}).get("providers", [])
        if any(p["name"] == provider_name for p in existing_providers):
            return {"error": True, "message": f"Provider '{provider_name}' already exists", "exit_code": 1}

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

        return {"message": "Provider added", "provider": provider_name}

    except Exception as e:
        logger.error("Failed to add provider: %s", e, exc_info=True)
        return {"error": True, "message": f"Failed to add provider: {e}", "exit_code": 1}


async def handle_provider_remove(args) -> dict[str, Any]:
    """Handle orb providers remove command."""
    try:
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            return {"error": True, "message": "No configuration found", "exit_code": 1}

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])
        original_count = len(providers)

        providers[:] = [p for p in providers if p["name"] != args.provider_name]

        if len(providers) == original_count:
            return {"error": True, "message": f"Provider '{args.provider_name}' not found", "exit_code": 1}

        if len(providers) == 0:
            return {"error": True, "message": "Cannot remove last provider", "exit_code": 1}

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        return {"message": "Provider removed", "provider": args.provider_name}

    except Exception as e:
        logger.error("Failed to remove provider: %s", e, exc_info=True)
        return {"error": True, "message": f"Failed to remove provider: {e}", "exit_code": 1}


async def handle_provider_update(args) -> dict[str, Any]:
    """Handle orb providers update command."""
    try:
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            return {"error": True, "message": "No configuration found", "exit_code": 1}

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])
        provider = None
        for p in providers:
            if p["name"] == args.provider_name:
                provider = p
                break

        if not provider:
            return {"error": True, "message": f"Provider '{args.provider_name}' not found", "exit_code": 1}

        # Infer provider type from stored record
        provider_type = provider.get("type", "aws")
        spec = CLISpecRegistry.get(provider_type)

        provider_config = provider.get("config", {})

        if spec is not None:
            partial = spec.extract_partial_config(args)
            if not partial:
                return {"error": True, "message": "No updates specified.", "exit_code": 1}
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
                return {"error": True, "message": "No updates specified.", "exit_code": 1}

        # Test updated credentials
        success, error = _test_provider_credentials(provider_type, provider_config)
        if not success:
            return {"error": True, "message": f"Credential test failed: {error}", "exit_code": 1}

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        return {"message": "Provider updated", "provider": args.provider_name}

    except Exception as e:
        logger.error("Failed to update provider: %s", e, exc_info=True)
        return {"error": True, "message": f"Failed to update provider: {e}", "exit_code": 1}


async def handle_provider_set_default(args) -> dict[str, Any]:
    """Handle orb providers set-default command."""
    try:
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            return {"error": True, "message": "No configuration found", "exit_code": 1}

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])
        if not any(p["name"] == args.provider_name for p in providers):
            return {"error": True, "message": f"Provider '{args.provider_name}' not found", "exit_code": 1}

        config.setdefault("provider", {})["default_provider"] = args.provider_name

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        return {"message": "Default provider set", "provider": args.provider_name}

    except Exception as e:
        logger.error("Failed to set default provider: %s", e, exc_info=True)
        return {"error": True, "message": f"Failed to set default provider: {e}", "exit_code": 1}


async def handle_provider_get_default(args) -> dict[str, Any]:
    """Handle orb providers get-default command."""
    try:
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            return {"error": True, "message": "No configuration found", "exit_code": 1}

        with open(config_file) as f:
            config = json.load(f)

        default_provider = config.get("provider", {}).get("default_provider")

        if default_provider:
            return {"default_provider": default_provider, "explicit": True}
        else:
            providers = config.get("provider", {}).get("providers", [])
            if providers:
                return {"default_provider": providers[0]["name"], "explicit": False}
            else:
                return {"error": True, "message": "No providers configured", "exit_code": 1}

    except Exception as e:
        logger.error("Failed to get default provider: %s", e, exc_info=True)
        return {"error": True, "message": f"Failed to get default provider: {e}", "exit_code": 1}


async def handle_provider_get(args) -> dict[str, Any]:
    """Handle orb providers get command."""
    try:
        provider_name = getattr(args, "name", None) or getattr(args, "provider_name", None)
        if not provider_name:
            return {"error": True, "message": "Provider name is required", "exit_code": 1}

        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            return {"error": True, "message": "No configuration found", "exit_code": 1}

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])
        provider = next((p for p in providers if p["name"] == provider_name), None)
        if not provider:
            return {"error": True, "message": f"Provider '{provider_name}' not found", "exit_code": 1}

        return {"provider": provider}

    except Exception as e:
        logger.error("Failed to get provider: %s", e, exc_info=True)
        return {"error": True, "message": f"Failed to get provider: {e}", "exit_code": 1}


async def handle_provider_show(args) -> Union[dict[str, Any], InterfaceResponse]:
    """Handle orb providers show command."""
    try:
        container = get_container()
        formatter = container.get(ResponseFormattingService)
        config_file = get_config_location() / "config.json"
        if not config_file.exists():
            return formatter.format_error("No configuration found")

        with open(config_file) as f:
            config = json.load(f)

        providers = config.get("provider", {}).get("providers", [])

        def _build_provider_dict(p: dict) -> dict[str, Any]:
            spec = CLISpecRegistry.get(p.get("type", ""))
            display_config: dict[str, Any]
            if spec is not None:
                display_config = dict(spec.format_display(p.get("config", {})))
            else:
                display_config = p.get("config", {})
            result: dict[str, Any] = {
                "name": p["name"],
                "type": p["type"],
                "enabled": p.get("enabled", True),
                "config": display_config,
            }
            if p.get("template_defaults"):
                result["template_defaults"] = p["template_defaults"]
            return result

        if args.provider_name:
            provider = next((p for p in providers if p["name"] == args.provider_name), None)
            if not provider:
                return formatter.format_error(f"Provider '{args.provider_name}' not found")
            return formatter.format_provider_detail(_build_provider_dict(provider))
        else:
            default_provider = config.get("provider", {}).get("default_provider")
            if default_provider:
                for p in providers:
                    if p["name"] == default_provider:
                        return formatter.format_provider_detail(_build_provider_dict(p))
            if providers:
                return formatter.format_provider_detail(_build_provider_dict(providers[0]))
            return formatter.format_error("No providers configured")

    except Exception as e:
        logger.error("Failed to show provider: %s", e, exc_info=True)
        return formatter.format_error(f"Failed to show provider: {e}")


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
