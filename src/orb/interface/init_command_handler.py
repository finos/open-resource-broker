"""Init command handler for ORB configuration initialization."""

import json
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from orb.config.platform_dirs import (
    get_config_location,
    get_logs_location,
    get_scripts_location,
    get_work_location,
)
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.infrastructure.di.container import get_container
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def handle_init(args) -> int:
    """Handle orb init command."""
    console = get_container().get(ConsolePort)
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
            console.error(f"Configuration already exists at {config_dir}")
            console.info("  Use --force to reinitialize")
            console.info("")
            console.info("To view current config:")
            console.command("  orb config show")
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
        console.separator(char="━", color="green")
        console.success("  ORB initialized successfully")
        console.separator(char="━", color="green")
        console.info("")  # Empty line
        console.info("Created:")
        console.info(f"  Config:  {config_dir}")
        console.info(f"  Work:    {work_dir}")
        console.info(f"  Logs:    {logs_dir}")
        console.info(f"  Scripts: {scripts_dir}")
        console.info("")  # Empty line
        console.info("Next Steps:")
        console.command("  1. Generate templates: orb templates generate")
        console.command("  2. List templates:     orb templates list")
        console.command("  3. Show infrastructure: orb infrastructure show")
        console.command("  3. Show config:        orb config show")

        return 0

    except (KeyboardInterrupt, EOFError):
        console.error("\nInitialization cancelled.")
        return 1
    except Exception as e:
        console.error("Failed to initialize ORB")
        console.error(f"  {e}")
        console.info("")
        console.info("To retry:")
        console.command("  orb init --force")
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


def _get_available_providers(registry: Any = None) -> list[dict[str, str]]:
    """Get available providers from provider registry."""
    try:
        if registry is None:
            from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort

            registry = get_container().get(ProviderRegistryPort)
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
    console = get_container().get(ConsolePort)
    try:
        console.separator(char="=", color="cyan")
        console.info("  ORB Configuration Setup")
        console.separator(char="=", color="cyan")

        # Scheduler type
        console.info("")
        console.info("[1/4] Scheduler Type")
        console.separator(char="-", color="cyan")

        schedulers = _get_available_schedulers()
        for i, scheduler in enumerate(schedulers, 1):
            console.info(f"  ({i}) {scheduler['display_name']} - {scheduler['description']}")

        console.info("")
        scheduler_choice = input("  Select scheduler (1): ").strip() or "1"
        try:
            scheduler_type = schedulers[int(scheduler_choice) - 1]["type"]
        except (ValueError, IndexError):
            scheduler_type = "default"

        console.info("")
        console.separator(char="-", color="cyan")

        # Provider type
        console.info("")
        console.info("[2/4] Cloud Provider")
        console.separator(char="-", color="cyan")

        providers = _get_available_providers()
        if not providers:
            raise ValueError("No providers registered. Install a provider plugin to continue.")
        for i, provider in enumerate(providers, 1):
            console.info(f"  ({i}) {provider['display_name']} - {provider['description']}")

        console.info("")
        provider_choice = input("  Select provider (1): ").strip() or "1"
        try:
            provider_type = providers[int(provider_choice) - 1]["type"]
        except (ValueError, IndexError):
            provider_type = providers[0]["type"]

        console.info("")
        console.separator(char="-", color="cyan")

        # Provider configuration
        console.info("")
        console.info("[3/4] Provider Configuration")
        console.separator(char="-", color="cyan")

        # Step 1: pre-auth params (e.g. Azure tenant_id)
        auth_requirements = _get_credential_requirements(provider_type)
        provider_config: Dict[str, Any] = {"type": provider_type}
        for param, info in auth_requirements.items():
            if info.get("required"):
                prompt = f"  {info['description']}: "
                provider_config[param] = input(prompt).strip()

        # Step 2: select credentials
        console.info("  Discovering credential sources...")
        credential_sources = _get_available_credential_sources(provider_type)

        console.info("")
        console.info("Available credentials:")
        for i, source in enumerate(credential_sources, 1):
            console.info(f"  ({i}) {source['description']}")

        choice = input("  Select credentials (1): ").strip() or "1"
        try:
            selected_source = credential_sources[int(choice) - 1]["name"]
        except (ValueError, IndexError):
            selected_source = None

        # Step 3: test credentials (no region yet)
        console.info("")
        console.info("Testing credentials...")
        success, error_msg = _test_provider_credentials(
            provider_type, selected_source, **provider_config
        )
        if success:
            console.success("Credentials verified successfully")
            if selected_source:
                provider_config["profile"] = selected_source
        else:
            console.error("[bold red]ERROR[/bold red] Authentication failed:")
            console.error(f"        {error_msg}")
            return {}

        # Step 4: operational params (region for AWS)
        op_requirements = _get_operational_requirements(provider_type)
        strategy = _get_provider_strategy(provider_type)
        regions = strategy.get_available_regions() if strategy is not None else []
        default_region = strategy.get_default_region() if strategy is not None else ""
        for param, info in op_requirements.items():
            if info.get("required"):
                if param == "region":
                    provider_config[param] = _pick_region(regions, default_region)
                else:
                    prompt = f"  {info['description']}: "
                    provider_config[param] = input(prompt).strip()

        # Step 5: extract final values
        region = provider_config.get("region") or default_region
        profile = provider_config.get("profile") or None

        console.info("")
        console.separator(char="-", color="cyan")

        # Infrastructure discovery
        console.info("")
        console.info("[4/4] Infrastructure Discovery")
        console.separator(char="-", color="cyan")
        console.info("  Discover infrastructure for template defaults?")
        console.info("  This will help create generic templates that work across regions/accounts.")
        console.info("")
        discover_choice = input("  Discover infrastructure? (y/N): ").strip().lower()

        infrastructure_defaults = {}
        if discover_choice in ["y", "yes"]:
            registry = get_container().get(ProviderRegistryPort)
            infrastructure_defaults = _discover_infrastructure(
                provider_type, region, profile, registry
            )

        # Create first provider instance
        first_provider = {
            "type": provider_type,
            "profile": profile,
            "region": region,
            "infrastructure_defaults": infrastructure_defaults,
        }

        providers = [first_provider]

        # Multi-provider loop
        console.info("")
        console.separator(char="-", color="cyan")
        while True:
            console.info("")
            add_another = input("  Add another provider? (y/N): ").strip().lower()

            if add_another not in ["y", "yes"]:
                break

            additional_provider = _configure_additional_provider()
            if additional_provider:
                providers.append(additional_provider)

        console.info("")

        # Default provider selection (only when multiple providers configured)
        default_provider_index = 0
        if len(providers) > 1:
            console.separator(char="-", color="cyan")
            console.info("")
            console.info("Default Provider Selection")
            console.info("  Which provider should be used as the default?")
            console.info("")
            for i, p in enumerate(providers, 1):
                console.info(f"  ({i}) {p['type']} - {p['region']} ({p['profile']})")
            console.info("")
            default_choice = input("  Select default provider (1): ").strip() or "1"
            try:
                default_provider_index = int(default_choice) - 1
                if not (0 <= default_provider_index < len(providers)):
                    default_provider_index = 0
            except ValueError:
                default_provider_index = 0
            console.info("")

        # Mark the default provider
        for i, p in enumerate(providers):
            p["is_default"] = i == default_provider_index

        return {
            "scheduler_type": scheduler_type,
            "providers": providers,
        }
    except KeyboardInterrupt:
        console.error("\n\nSetup cancelled by user")
        raise
    except EOFError:
        console.error("\n\nUnexpected end of input")
        console.info("  Run with --non-interactive for automated setup")
        raise


def _configure_additional_provider() -> Optional[Dict[str, Any]]:
    """Configure an additional provider instance."""
    console = get_container().get(ConsolePort)
    try:
        console.info("")
        console.info("Additional Provider Configuration")
        console.separator(char="-", color="cyan")

        # Provider type
        providers = _get_available_providers()
        if not providers:
            raise ValueError("No providers registered. Install a provider plugin to continue.")
        for i, provider in enumerate(providers, 1):
            console.info(f"  ({i}) {provider['display_name']} - {provider['description']}")

        console.info("")
        provider_choice = input("  Select provider (1): ").strip() or "1"
        try:
            provider_type = providers[int(provider_choice) - 1]["type"]
        except (ValueError, IndexError):
            provider_type = providers[0]["type"]

        # Provider configuration
        console.info("")
        console.info("Provider Configuration")
        console.separator(char="-", color="cyan")

        # Step 1: pre-auth params (e.g. Azure tenant_id)
        auth_requirements = _get_credential_requirements(provider_type)
        provider_config: Dict[str, Any] = {"type": provider_type}
        for param, info in auth_requirements.items():
            if info.get("required"):
                prompt = f"  {info['description']}: "
                provider_config[param] = input(prompt).strip()

        # Step 2: select credentials
        console.info("  Discovering credential sources...")
        credential_sources = _get_available_credential_sources(provider_type)

        console.info("")
        console.info("Available credentials:")
        for i, source in enumerate(credential_sources, 1):
            console.info(f"  ({i}) {source['description']}")

        choice = input("  Select credentials (1): ").strip() or "1"
        try:
            selected_source = credential_sources[int(choice) - 1]["name"]
        except (ValueError, IndexError):
            selected_source = None

        # Step 3: test credentials (no region yet)
        console.info("")
        console.info("Testing credentials...")
        success, error_msg = _test_provider_credentials(
            provider_type, selected_source, **provider_config
        )
        if success:
            console.success("Credentials verified successfully")
            if selected_source:
                provider_config["profile"] = selected_source
        else:
            console.error(f"Authentication failed: {error_msg}")
            return None

        # Step 4: operational params (region for AWS)
        op_requirements = _get_operational_requirements(provider_type)
        strategy = _get_provider_strategy(provider_type)
        regions = strategy.get_available_regions() if strategy is not None else []
        default_region = strategy.get_default_region() if strategy is not None else ""
        for param, info in op_requirements.items():
            if info.get("required"):
                if param == "region":
                    provider_config[param] = _pick_region(regions, default_region)
                else:
                    prompt = f"  {info['description']}: "
                    provider_config[param] = input(prompt).strip()

        # Step 5: extract final values
        region = provider_config.get("region") or default_region
        profile = provider_config.get("profile") or None

        # Infrastructure discovery
        console.info("")
        console.info("Infrastructure Discovery")
        console.separator(char="-", color="cyan")
        discover_choice = input("  Discover infrastructure? (y/N): ").strip().lower()

        infrastructure_defaults = {}
        if discover_choice in ["y", "yes"]:
            registry = get_container().get(ProviderRegistryPort)
            infrastructure_defaults = _discover_infrastructure(
                provider_type, region, profile, registry
            )

        return {
            "type": provider_type,
            "profile": provider_config.get("profile") or None,
            "region": provider_config.get("region") or default_region,
            "infrastructure_defaults": infrastructure_defaults,
        }

    except KeyboardInterrupt:
        console.error("\nProvider configuration cancelled")
        return None
    except Exception as e:
        console.error(f"Failed to configure provider: {e}")
        return None


def _get_provider_strategy(provider_type: str, registry: Any = None) -> Optional[Any]:
    """Get a lightweight provider strategy instance for credential/region queries."""
    try:
        if registry is None:
            from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort

            registry = get_container().get(ProviderRegistryPort)
        registry.ensure_provider_type_registered(provider_type)
        return registry.get_or_create_strategy(provider_type)
    except Exception:
        return None


def _pick_region(regions: list[tuple[str, str]], default_region: str = "") -> str:
    """Prompt user to select a region.

    If regions is non-empty, show a numbered list with an 'Other' option.
    If regions is empty, prompt for free-text input.
    """
    console = get_container().get(ConsolePort)
    console.info("")
    if not regions:
        custom = input("  Enter region: ").strip()
        return custom if custom else default_region

    console.info("  Select region:")
    for i, (region_id, region_name) in enumerate(regions, 1):
        console.info(f"  ({i:2}) {region_id:<20} {region_name}")
    other_num = len(regions) + 1
    console.info(f"  ({other_num:2}) Other (type custom)")
    console.info("")

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
        except Exception as e:
            logger.debug("Could not get provider auth config sources from strategy: %s", e)
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
        except Exception as e:
            logger.debug("Could not get provider auth config requirements from strategy: %s", e)
    return {}


def _get_operational_requirements(provider_type: str) -> dict:
    """Get operational requirements for provider via strategy."""
    strategy = _get_provider_strategy(provider_type)
    if strategy is not None:
        try:
            return strategy.get_operational_requirements()
        except Exception as e:
            logger.debug("Could not get provider operational requirements from strategy: %s", e)
    return {}


def _discover_infrastructure(
    provider_type: str, region: str, profile: str | None, registry: ProviderRegistryPort
) -> Dict[str, Any]:
    """Discover infrastructure interactively using provider strategy."""
    console = get_container().get(ConsolePort)
    try:
        # Ensure provider type is registered
        if not registry.ensure_provider_type_registered(provider_type):
            console.error(f"Failed to register provider type: {provider_type}")
            return {}

        # Create provider config for discovery
        provider_config = {"region": region, "profile": profile}

        # Get strategy from registry — bypass cache so discovery uses the correct region/profile
        strategy = registry.create_strategy_by_type(provider_type, provider_config)

        # Check if provider strategy supports infrastructure discovery
        if hasattr(strategy, "discover_infrastructure_interactive"):
            full_config = {"type": provider_type, "config": provider_config}
            return strategy.discover_infrastructure_interactive(full_config)  # type: ignore[union-attr]
        else:
            console.info(
                f"Infrastructure discovery not supported for provider type: {provider_type}"
            )
            return {}

    except Exception as e:
        console.error(f"Failed to discover infrastructure: {e}")
        console.info("Continuing without infrastructure discovery...")
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
    from importlib.resources import files

    try:
        full_config = json.loads(files("orb.config").joinpath("default_config.json").read_text())
    except Exception as e:
        raise FileNotFoundError(f"Could not find default_config.json template: {e}")

    # Copy default_config.json to runtime config directory
    default_config_file = config_file.parent / "default_config.json"
    with open(default_config_file, "w") as f:
        json.dump(full_config, f, indent=2)

    # Process all providers
    providers_list = []
    default_provider_name: str | None = None
    for provider_data in user_config.get("providers", []):
        provider_config = {"profile": provider_data["profile"], "region": provider_data["region"]}
        provider_type = provider_data["type"]

        # Generate provider name
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

        # Track default provider name — ProviderInstanceConfig has no "default" field
        if provider_data.get("is_default", False):
            default_provider_name = provider_name

        # Add template_defaults if infrastructure was discovered.
        # Promote all infrastructure_defaults to template_defaults except for keys
        # that belong in provider config — determined by the provider strategy.
        strategy = None
        try:
            registry = get_container().get(ProviderRegistryPort)
            strategy = registry.create_strategy_by_type(provider_type, provider_config)
        except Exception:
            pass  # best-effort; fall back to putting all defaults in template_defaults
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

    # Default to first provider if none explicitly marked
    if not default_provider_name and providers_list:
        default_provider_name = providers_list[0]["name"]

    provider_section: dict[str, Any] = {"providers": providers_list}
    if default_provider_name:
        provider_section["default_provider_instance"] = default_provider_name

    config = {
        "scheduler": {"type": user_config["scheduler_type"]},
        "provider": provider_section,
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
