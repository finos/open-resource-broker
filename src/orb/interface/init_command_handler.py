"""Init command handler for ORB configuration initialization."""

import json
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from orb.cli.console import (
    print_command,
    print_error,
    print_info,
    print_newline,
    print_separator,
    print_success,
)
from orb.config.platform_dirs import (
    get_config_location,
    get_logs_location,
    get_scripts_location,
    get_work_location,
)
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def handle_init(args) -> int:
    """Handle orb init command."""
    try:
        # Determine config directory
        if args.config_dir:
            config_dir = Path(args.config_dir)
            run_dir = config_dir.parent
            work_dir = run_dir / "work"
            logs_dir = run_dir / "logs"
            scripts_dir = run_dir / "scripts"
        else:
            config_dir = get_config_location()
            work_dir = get_work_location()
            logs_dir = get_logs_location()
            scripts_dir = get_scripts_location()

        # Check if already initialized
        config_file = config_dir / "config.json"
        if config_file.exists() and not args.force:
            print_error(f"Configuration already exists at {config_dir}")
            print_info("  Use --force to reinitialize")
            print_info("")
            print_info("To view current config:")
            print_command("  orb config show")
            return 1

        # Get configuration
        if args.non_interactive:
            config = _get_default_config(args)
        else:
            config = _interactive_setup()

        # Check if configuration was successful
        if not config:
            return 1

        # Create directories
        _create_directories(config_dir, work_dir, logs_dir)

        # Write config file
        _write_config_file(config_file, config)

        # Copy platform-specific scripts
        _copy_scripts(scripts_dir)

        # Success message with separator
        print_separator(char="━", color="green")
        print_success("  ORB initialized successfully")
        print_separator(char="━", color="green")
        print_info("")  # Empty line
        print_info("Created:")
        print_info(f"  Config:  {config_dir}")
        print_info(f"  Work:    {work_dir}")
        print_info(f"  Logs:    {logs_dir}")
        print_info(f"  Scripts: {scripts_dir}")
        print_info("")  # Empty line
        print_info("Next Steps:")
        print_command("  1. Generate templates: orb templates generate")
        print_command("  2. List templates:     orb templates list")
        print_command("  3. Show infrastructure: orb infrastructure show")
        print_command("  3. Show config:        orb config show")

        return 0

    except KeyboardInterrupt:
        print_error("\nInitialization cancelled by user")
        return 1
    except Exception as e:
        print_error("Failed to initialize ORB")
        print_error(f"  {e}")
        print_info("")
        print_info("To retry:")
        print_command("  orb init --force")
        logger.error("Failed to initialize ORB: %s", e, exc_info=True)
        return 1


def _get_available_schedulers() -> list[dict[str, str]]:
    """Get available schedulers from registry."""
    from orb.infrastructure.scheduler.registration import register_all_scheduler_types
    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    scheduler_types = registry.get_available_types_with_registration(register_all_scheduler_types)

    seen: set[str] = set()
    schedulers = []
    for scheduler_type in scheduler_types:
        meta = registry.get_display_metadata(scheduler_type)
        display_name = meta["display_name"]
        if display_name not in seen:
            seen.add(display_name)
            schedulers.append({"type": scheduler_type, **meta})

    return schedulers


def _get_available_providers() -> list[dict[str, str]]:
    """Get available providers from provider registry."""
    try:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registered_types = registry.get_registered_providers()

        providers = []
        for provider_type in sorted(registered_types):
            display_name = provider_type
            description = f"{provider_type.upper()} Provider"
            providers.append(
                {"type": provider_type, "display_name": display_name, "description": description}
            )

        return providers
    except Exception:
        return []


def _interactive_setup() -> Dict[str, Any]:
    """Interactive configuration setup."""
    try:
        print_separator(char="=", color="cyan")
        print_info("  ORB Configuration Setup")
        print_separator(char="=", color="cyan")

        # Scheduler type
        print_info("")
        print_info("[1/4] Scheduler Type")
        print_separator(char="-", color="cyan")

        schedulers = _get_available_schedulers()
        for i, scheduler in enumerate(schedulers, 1):
            print_info(f"  ({i}) {scheduler['display_name']} - {scheduler['description']}")

        print_info("")
        scheduler_choice = input("  Select scheduler (1): ").strip() or "1"
        try:
            scheduler_type = schedulers[int(scheduler_choice) - 1]["type"]
        except (ValueError, IndexError):
            scheduler_type = "default"

        print_newline()
        print_separator(char="-", color="cyan")

        # Provider type
        print_info("")
        print_info("[2/4] Cloud Provider")
        print_separator(char="-", color="cyan")

        providers = _get_available_providers()
        if not providers:
            raise ValueError("No providers registered. Install a provider plugin to continue.")
        for i, provider in enumerate(providers, 1):
            print_info(f"  ({i}) {provider['display_name']} - {provider['description']}")

        print_info("")
        provider_choice = input("  Select provider (1): ").strip() or "1"
        try:
            provider_type = providers[int(provider_choice) - 1]["type"]
        except (ValueError, IndexError):
            provider_type = providers[0]["type"]

        print_newline()
        print_separator(char="-", color="cyan")

        # Provider configuration
        print_info("")
        print_info("[3/4] Provider Configuration")
        print_separator(char="-", color="cyan")

        # Get credential requirements
        requirements = _get_credential_requirements(provider_type)

        # Collect required parameters first (e.g., region)
        strategy = _get_provider_strategy(provider_type)
        regions = strategy.get_available_regions() if strategy is not None else []
        default_region = strategy.get_default_region() if strategy is not None else ""
        provider_config = {"type": provider_type}
        for param, info in requirements.items():
            if info.get("required"):
                if param == "region":
                    provider_config[param] = _pick_region(regions, default_region)
                else:
                    prompt = f"  {info['description']}: "
                    provider_config[param] = input(prompt).strip()

        # Get available credential sources
        credential_sources = _get_available_credential_sources(provider_type)

        print_info("")
        print_info("Available credentials:")
        for i, source in enumerate(credential_sources, 1):
            print_info(f"  ({i}) {source['description']}")

        choice = input("  Select credentials (1): ").strip() or "1"
        try:
            selected_source = credential_sources[int(choice) - 1]["name"]
        except (ValueError, IndexError):
            selected_source = None

        # Test credentials
        print_info("")
        print_info("Testing credentials...")
        success, error_msg = _test_provider_credentials(
            provider_type, selected_source, **provider_config
        )
        if success:
            print_success("Credentials verified successfully")
            if selected_source:
                provider_config["profile"] = selected_source
        else:
            print_error("[bold red]ERROR[/bold red] Authentication failed:")
            print_error(f"        {error_msg}")
            return {}

        # Extract final values for backward compatibility
        region = provider_config.get("region") or default_region
        profile = provider_config.get("profile") or None

        print_newline()
        print_separator(char="-", color="cyan")

        # Infrastructure discovery
        print_info("")
        print_info("[4/4] Infrastructure Discovery")
        print_separator(char="-", color="cyan")
        print_info("  Discover infrastructure for template defaults?")
        print_info("  This will help create generic templates that work across regions/accounts.")
        print_info("")
        discover_choice = input("  Discover infrastructure? (y/N): ").strip().lower()

        infrastructure_defaults = {}
        if discover_choice in ["y", "yes"]:
            infrastructure_defaults = _discover_infrastructure(provider_type, region, profile)

        # Create first provider instance
        first_provider = {
            "type": provider_type,
            "profile": profile,
            "region": region,
            "infrastructure_defaults": infrastructure_defaults,
        }

        providers = [first_provider]

        # Multi-provider loop
        print_info("")
        print_separator(char="-", color="cyan")
        while True:
            print_info("")
            add_another = input("  Add another provider? (y/N): ").strip().lower()

            if add_another not in ["y", "yes"]:
                break

            additional_provider = _configure_additional_provider()
            if additional_provider:
                providers.append(additional_provider)

        print_info("")

        # Default provider selection (only when multiple providers configured)
        default_provider_index = 0
        if len(providers) > 1:
            print_separator(char="-", color="cyan")
            print_info("")
            print_info("Default Provider Selection")
            print_info("  Which provider should be used as the default?")
            print_info("")
            for i, p in enumerate(providers, 1):
                print_info(f"  ({i}) {p['type']} - {p['region']} ({p['profile']})")
            print_info("")
            default_choice = input("  Select default provider (1): ").strip() or "1"
            try:
                default_provider_index = int(default_choice) - 1
                if not (0 <= default_provider_index < len(providers)):
                    default_provider_index = 0
            except ValueError:
                default_provider_index = 0
            print_info("")

        # Mark the default provider
        for i, p in enumerate(providers):
            p["is_default"] = i == default_provider_index

        return {
            "scheduler_type": scheduler_type,
            "providers": providers,
        }
    except KeyboardInterrupt:
        print_error("\n\nSetup cancelled by user")
        raise
    except EOFError:
        print_error("\n\nUnexpected end of input")
        print_info("  Run with --non-interactive for automated setup")
        raise


def _configure_additional_provider() -> Optional[Dict[str, Any]]:
    """Configure an additional provider instance."""
    try:
        print_info("")
        print_info("Additional Provider Configuration")
        print_separator(char="-", color="cyan")

        # Provider type
        providers = _get_available_providers()
        if not providers:
            raise ValueError("No providers registered. Install a provider plugin to continue.")
        for i, provider in enumerate(providers, 1):
            print_info(f"  ({i}) {provider['display_name']} - {provider['description']}")

        print_info("")
        provider_choice = input("  Select provider (1): ").strip() or "1"
        try:
            provider_type = providers[int(provider_choice) - 1]["type"]
        except (ValueError, IndexError):
            provider_type = providers[0]["type"]

        # Provider configuration
        print_info("")
        print_info("Provider Configuration")
        print_separator(char="-", color="cyan")

        # Get credential requirements
        requirements = _get_credential_requirements(provider_type)

        strategy = _get_provider_strategy(provider_type)
        regions = strategy.get_available_regions() if strategy is not None else []
        default_region = strategy.get_default_region() if strategy is not None else ""
        provider_config = {"type": provider_type}
        for param, info in requirements.items():
            if info.get("required"):
                if param == "region":
                    provider_config[param] = _pick_region(regions, default_region)
                else:
                    prompt = f"  {info['description']}: "
                    provider_config[param] = input(prompt).strip()

        # Get available credential sources
        credential_sources = _get_available_credential_sources(provider_type)

        print_info("")
        print_info("Available credentials:")
        for i, source in enumerate(credential_sources, 1):
            print_info(f"  ({i}) {source['description']}")

        choice = input("  Select credentials (1): ").strip() or "1"
        try:
            selected_source = credential_sources[int(choice) - 1]["name"]
        except (ValueError, IndexError):
            selected_source = None

        # Test credentials
        print_info("")
        print_info("Testing credentials...")
        success, error_msg = _test_provider_credentials(
            provider_type, selected_source, **provider_config
        )
        if success:
            print_success("Credentials verified successfully")
            if selected_source:
                provider_config["profile"] = selected_source
        else:
            print_error(f"Authentication failed: {error_msg}")
            return None

        # Infrastructure discovery
        print_info("")
        print_info("Infrastructure Discovery")
        print_separator(char="-", color="cyan")
        discover_choice = input("  Discover infrastructure? (y/N): ").strip().lower()

        infrastructure_defaults = {}
        if discover_choice in ["y", "yes"]:
            region = provider_config.get("region") or default_region
            profile = provider_config.get("profile") or None
            infrastructure_defaults = _discover_infrastructure(provider_type, region, profile)

        return {
            "type": provider_type,
            "profile": provider_config.get("profile") or None,
            "region": provider_config.get("region") or default_region,
            "infrastructure_defaults": infrastructure_defaults,
        }

    except KeyboardInterrupt:
        print_error("\nProvider configuration cancelled")
        return None
    except Exception as e:
        print_error(f"Failed to configure provider: {e}")
        return None


def _get_provider_strategy(provider_type: str) -> Optional[Any]:
    """Get a lightweight provider strategy instance for credential/region queries."""
    try:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        registry.ensure_provider_type_registered(provider_type)
        return registry.get_or_create_strategy(provider_type)
    except Exception:
        return None


def _pick_region(regions: list[tuple[str, str]], default_region: str = "") -> str:
    """Prompt user to select a region.

    If regions is non-empty, show a numbered list with an 'Other' option.
    If regions is empty, prompt for free-text input.
    """
    print_info("")
    if not regions:
        custom = input("  Enter region: ").strip()
        return custom if custom else default_region

    print_info("  Select region:")
    for i, (region_id, region_name) in enumerate(regions, 1):
        print_info(f"  ({i:2}) {region_id:<20} {region_name}")
    other_num = len(regions) + 1
    print_info(f"  ({other_num:2}) Other (type custom)")
    print_info("")

    choice = input("  Select region (1): ").strip() or "1"
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(regions):
            return regions[idx][0]
        elif idx == len(regions):
            custom = input("  Enter custom region: ").strip()
            return custom if custom else default_region
        else:
            return default_region
    except ValueError:
        return default_region


def _get_available_credential_sources(provider_type: str) -> list[dict]:
    """Get available credential sources for provider via strategy."""
    strategy = _get_provider_strategy(provider_type)
    if strategy is not None:
        try:
            sources = strategy.get_available_credential_sources()
            if sources:
                return sources
        except Exception:
            pass
    return [{"name": None, "description": "Default credentials"}]


def _test_provider_credentials(
    provider_type: str, credential_source: Optional[str], **kwargs
) -> tuple[bool, str]:
    """Test provider credentials via strategy."""
    strategy = _get_provider_strategy(provider_type)
    if strategy is None:
        return False, "Provider type not supported"
    try:
        result = strategy.test_credentials(credential_source, **kwargs)
        if result.get("success", False):
            return True, ""
        return False, result.get("error", "Unknown error")
    except Exception as e:
        return False, str(e)


def _get_credential_requirements(provider_type: str) -> dict:
    """Get credential requirements for provider via strategy."""
    strategy = _get_provider_strategy(provider_type)
    if strategy is not None:
        try:
            return strategy.get_credential_requirements()
        except Exception:
            pass
    return {}


def _discover_infrastructure(
    provider_type: str, region: str, profile: str | None
) -> Dict[str, Any]:
    """Discover infrastructure interactively using provider strategy."""
    try:
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()

        # Ensure provider type is registered
        if not registry.ensure_provider_type_registered(provider_type):
            print_error(f"Failed to register provider type: {provider_type}")
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
            print_info(f"Infrastructure discovery not supported for provider type: {provider_type}")
            return {}

    except Exception as e:
        print_error(f"Failed to discover infrastructure: {e}")
        print_info("Continuing without infrastructure discovery...")
        return {}


def _get_default_config(args) -> Dict[str, Any]:
    """Get default configuration from args."""
    # Get first available provider as default
    providers = _get_available_providers()
    if not providers and not args.provider:
        raise ValueError("No providers registered. Install a provider plugin to continue.")
    default_provider = providers[0]["type"] if providers else args.provider

    provider_type = args.provider or default_provider
    strategy = _get_provider_strategy(provider_type)
    default_region = strategy.get_default_region() if strategy is not None else ""
    infrastructure_defaults = (
        strategy.get_cli_infrastructure_defaults(args) if strategy is not None else {}
    )

    first_provider = {
        "type": provider_type,
        "profile": args.profile or None,
        "region": args.region or default_region,
        "infrastructure_defaults": infrastructure_defaults,
    }

    return {
        "scheduler_type": args.scheduler or "default",
        "providers": [first_provider],
    }


def _create_directories(config_dir: Path, work_dir: Path, logs_dir: Path):
    """Create directory structure."""
    dirs = [
        config_dir,
        work_dir,
        work_dir / ".cache",
        logs_dir,
    ]

    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.info("Created directory: %s", dir_path)


def _write_config_file(config_file: Path, user_config: Dict[str, Any]):
    """Write configuration file with multiple provider support."""
    from orb.config.installation_detector import get_template_location

    try:
        template_path = get_template_location()

        if template_path.exists():
            with open(template_path) as f:
                full_config = json.load(f)
        else:
            raise FileNotFoundError(f"Template not found: {template_path}")

    except Exception as e:
        raise FileNotFoundError(f"Could not find default_config.json template: {e}")

    # Copy default_config.json to runtime config directory
    default_config_file = config_file.parent / "default_config.json"
    with open(default_config_file, "w") as f:
        json.dump(full_config, f, indent=2)

    # Process all providers
    providers_list = []
    for provider_data in user_config.get("providers", []):
        provider_config = {"profile": provider_data["profile"], "region": provider_data["region"]}
        provider_type = provider_data["type"]

        # Generate provider name
        strategy = None
        try:
            from orb.infrastructure.di.container import get_container
            from orb.providers.factory import ProviderStrategyFactory

            container = get_container()
            factory = container.get(ProviderStrategyFactory)

            temp_config = {"type": provider_type, **provider_config}
            strategy = factory.create_strategy(provider_type, temp_config)  # type: ignore[attr-defined]
            provider_name = strategy.generate_provider_name(provider_config)
        except Exception:
            # Fallback to simple name generation
            import re

            profile_for_name = provider_data["profile"] or "instance-profile"
            sanitized_profile = re.sub(r"[^a-zA-Z0-9\-_]", "-", profile_for_name)
            provider_name = f"{provider_type}_{sanitized_profile}_{provider_data['region']}"

        # Create provider instance
        provider_instance = {
            "name": provider_name,
            "type": provider_type,
            "enabled": True,
            "config": provider_config,
        }

        # Mark as default if flagged
        if provider_data.get("is_default", False):
            provider_instance["default"] = True

        # Add template_defaults if infrastructure was discovered.
        # Promote all infrastructure_defaults to template_defaults except for keys
        # that belong in provider config — determined by the provider strategy.
        infrastructure_defaults = provider_data.get("infrastructure_defaults", {})
        if infrastructure_defaults:
            config_only_keys = (
                strategy.get_cli_extra_config_keys() if strategy is not None else set()
            )
            template_level = {
                k: v for k, v in infrastructure_defaults.items() if k not in config_only_keys
            }
            if template_level:
                provider_instance["template_defaults"] = template_level
            for key in config_only_keys:
                if key in infrastructure_defaults:
                    provider_instance.setdefault("config", {})[key] = infrastructure_defaults[key]

        providers_list.append(provider_instance)

    config = {
        "scheduler": {"type": user_config["scheduler_type"]},
        "provider": {"providers": providers_list},
    }

    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    extra = registry.get_extra_config_for_type(user_config["scheduler_type"])
    config["scheduler"].update(extra)

    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)


def _copy_scripts(scripts_dir: Path):
    """Copy platform-specific scripts to scripts directory."""
    from orb.config.installation_detector import get_scripts_location

    try:
        scripts_src = get_scripts_location()

        if not scripts_src.exists():
            logger.warning(f"Scripts directory not found: {scripts_src}")
            return

        scripts_dir.mkdir(parents=True, exist_ok=True)

        is_windows = platform.system() == "Windows"
        extension = ".bat" if is_windows else ".sh"

        copied = 0
        for script in scripts_src.glob(f"*{extension}"):
            # Skip if source and destination are the same (development mode)
            dest_script = scripts_dir / script.name
            if script.resolve() == dest_script.resolve():
                continue
            shutil.copy2(script, dest_script)
            copied += 1

        if copied > 0:
            logger.info(f"Copied {copied} scripts to {scripts_dir}")

    except Exception as e:
        logger.warning(f"Failed to copy scripts: {e}", exc_info=True)


def _get_installed_scripts_path():
    """Get scripts path for installed package using proper scheme detection."""
