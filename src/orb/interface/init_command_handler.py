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
from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry

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
            if getattr(args, "scripts_dir", None):
                scripts_dir = Path(args.scripts_dir)
            else:
                scripts_dir = run_dir / "scripts"
        else:
            config_dir = get_config_location()
            work_dir = get_work_location()
            logs_dir = get_logs_location()
            if getattr(args, "scripts_dir", None):
                scripts_dir = Path(args.scripts_dir)
            else:
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

        # Determine which dirs differ from platform defaults (came from env vars or --config-dir)
        default_logs = get_logs_location()
        default_work = get_work_location()
        default_scripts = get_scripts_location()
        extra_paths: dict = {}
        if logs_dir != default_logs:
            extra_paths["logs_dir"] = logs_dir
        if work_dir != default_work:
            extra_paths["work_dir"] = work_dir
        if scripts_dir != default_scripts:
            extra_paths["scripts_dir"] = scripts_dir

        # Write config file
        _write_config_file(config_file, config, extra_paths)

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
            if selected_source is not None:
                source_entry = next(
                    (s for s in credential_sources if s["name"] == selected_source),
                    None,
                )
                if source_entry is not None:
                    provider_config.update(source_entry["config_delta"])
        else:
            console.error("[bold red]ERROR[/bold red] Authentication failed:")
            console.error(f"        {error_msg}")
            return {}

        # Step 4: operational params (provider-specific, e.g. region, project, namespace)
        strategy_class = _get_provider_strategy(provider_type)
        op_params = _prompt_operational_params(strategy_class)
        provider_config.update(op_params)

        console.info("")
        console.separator(char="-", color="cyan")

        # Infrastructure discovery
        console.info("")
        console.info("[4/4] Infrastructure Discovery")
        console.separator(char="-", color="cyan")
        console.info("  Discover infrastructure for template defaults?")
        console.info("  This will help create generic templates that work across provider setups.")
        console.info("")
        discover_choice = input("  Discover infrastructure? (Y/n): ").strip().lower()

        infrastructure_defaults = {}
        if discover_choice in ["", "y", "yes"]:
            registry = get_container().get(ProviderRegistryPort)
            infrastructure_defaults = _discover_infrastructure(
                provider_type, provider_config, registry
            )

        # Create first provider instance — provider_config is treated as opaque;
        # provider-specific layers unpack the individual keys they need.
        first_provider = {
            "type": provider_type,
            "config": {k: v for k, v in provider_config.items() if k != "type"},
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
                config_summary = ", ".join(
                    f"{k}={v}" for k, v in p.get("config", {}).items() if v is not None
                )
                console.info(f"  ({i}) {p['type']} - {config_summary}")
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
            if selected_source is not None:
                source_entry = next(
                    (s for s in credential_sources if s["name"] == selected_source),
                    None,
                )
                if source_entry is not None:
                    provider_config.update(source_entry["config_delta"])
        else:
            console.error(f"Authentication failed: {error_msg}")
            return None

        # Step 4: operational params (provider-specific, e.g. region, project, namespace)
        strategy_class = _get_provider_strategy(provider_type)
        op_params = _prompt_operational_params(strategy_class)
        provider_config.update(op_params)

        # Infrastructure discovery
        console.info("")
        console.info("Infrastructure Discovery")
        console.separator(char="-", color="cyan")
        discover_choice = input("  Discover infrastructure? (Y/n): ").strip().lower()

        infrastructure_defaults = {}
        if discover_choice in ["", "y", "yes"]:
            registry = get_container().get(ProviderRegistryPort)
            infrastructure_defaults = _discover_infrastructure(
                provider_type, provider_config, registry
            )

        return {
            "type": provider_type,
            "config": {k: v for k, v in provider_config.items() if k != "type"},
            "infrastructure_defaults": infrastructure_defaults,
        }

    except KeyboardInterrupt:
        console.error("\nProvider configuration cancelled")
        return None
    except Exception as e:
        console.error(f"Failed to configure provider: {e}")
        return None


def _get_provider_strategy(provider_type: str, registry: Any = None) -> Optional[type]:
    """Return the strategy CLASS for a provider type.

    The credential inquiry methods (``get_available_credential_sources``,
    ``test_credentials``, ``get_credential_requirements``,
    ``get_operational_requirements``, ``generate_provider_name``) are all
    classmethods and do not require an instance.  Returning the class directly
    avoids constructing a strategy before any provider configuration exists on
    disk.
    """
    try:
        if registry is None:
            from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort

            registry = get_container().get(ProviderRegistryPort)
        # Ensure the provider type is registered so its class is available.
        registry.ensure_provider_type_registered(provider_type)
        reg = registry._get_type_registration(provider_type)
        strategy_class = getattr(reg, "strategy_class", None)
        return strategy_class
    except Exception:
        return None


def _prompt_operational_params(strategy_class: Optional[type]) -> dict[str, Any]:
    """Interactively collect operational parameters from the operator.

    Calls ``get_operational_requirements()`` on the strategy class to discover
    what parameters are needed (e.g. region, project, namespace).  For each
    required parameter the strategy class may optionally supply a list of
    choices and a default via ``get_operational_param_choices(param)`` and
    ``get_operational_param_default(param)`` — if those classmethods are absent
    the field degrades to free-text input.

    Returns an opaque ``dict[str, Any]`` that the caller merges into the
    provider config without inspecting individual keys.
    """
    if strategy_class is None:
        return {}

    console = get_container().get(ConsolePort)
    op_requirements: dict[str, Any] = {}
    try:
        op_requirements = strategy_class.get_operational_requirements()
    except Exception as e:
        logger.debug("Could not get provider operational requirements from strategy: %s", e)

    result: dict[str, Any] = {}
    for param, info in op_requirements.items():
        if not info.get("required"):
            continue

        description = info.get("description", param)

        # Provider strategies may expose a list of (value, label) choices for
        # a given parameter.  If the classmethod is absent fall back to free text.
        choices: list[tuple[str, str]] = []
        default_value: str = ""
        try:
            if hasattr(strategy_class, "get_operational_param_choices"):
                choices = strategy_class.get_operational_param_choices(param) or []
        except Exception:
            # Strategy hook raised or returned a broken shape; free-text prompt
            # is the safe fallback so init keeps working with degraded UX.
            choices = []
        try:
            if hasattr(strategy_class, "get_operational_param_default"):
                default_value = strategy_class.get_operational_param_default(param) or ""
        except Exception:
            # Strategy hook raised or returned a broken shape; empty default
            # is safe fallback so the operator still gets prompted.
            default_value = ""

        console.info("")
        if not choices:
            raw = input(f"  {description}: ").strip()
            result[param] = raw if raw else default_value
        else:
            console.info(f"  Select {description}:")
            for i, (val, label) in enumerate(choices, 1):
                console.info(f"  ({i:2}) {val:<20} {label}")
            other_num = len(choices) + 1
            console.info(f"  ({other_num:2}) Other (enter custom value)")
            console.info("")
            choice = input("  Select (1): ").strip() or "1"
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(choices):
                    result[param] = choices[idx][0]
                elif idx == len(choices):
                    raw = input(f"  Enter custom {description}: ").strip()
                    result[param] = raw if raw else default_value
                else:
                    result[param] = default_value
            except ValueError:
                result[param] = default_value

    return result


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
    provider_type: str, provider_config: Dict[str, Any], registry: ProviderRegistryPort
) -> Dict[str, Any]:
    """Discover infrastructure interactively using the provider strategy.

    Discovery requires a live provider instance (cluster / account access).
    The instance is constructed via ``create_strategy_by_type`` using the
    credentials and region the operator already confirmed in the init flow.
    All provider config keys collected during the init flow are forwarded
    together as a single dict; providers that only use a subset of them are
    unaffected.

    Args:
        provider_type: The provider type identifier (e.g. ``"aws"``).
        provider_config: Dict of provider config key/value pairs already
            collected from the operator (e.g. ``{"region": ..., "profile": ...}``).
        registry: Live provider registry used to construct the strategy.
    """
    console = get_container().get(ConsolePort)
    try:
        strategy = registry.create_strategy_by_type(provider_type, provider_config)
        if strategy is None:
            console.error(f"Failed to construct strategy for provider type: {provider_type}")
            return {}

        if hasattr(strategy, "discover_infrastructure_interactive"):
            full_config = {"type": provider_type, "config": provider_config}
            return strategy.discover_infrastructure_interactive(full_config)
        console.info(f"Infrastructure discovery not supported for provider type: {provider_type}")
        return {}

    except Exception as e:
        console.error(f"Failed to discover infrastructure: {e}")
        console.info("Continuing without infrastructure discovery...")
        return {}


def _get_default_config(args) -> Dict[str, Any]:
    """Get default configuration from args."""
    # Get first available provider as default
    providers = _get_available_providers()
    if not providers and not args.provider_type:
        raise ValueError("No providers registered. Install a provider plugin to continue.")
    default_provider = providers[0]["type"] if providers else args.provider_type

    provider_type = args.provider_type or default_provider
    strategy_class = _get_provider_strategy(provider_type)
    infrastructure_defaults = (
        strategy_class.get_cli_infrastructure_defaults(args) if strategy_class is not None else {}
    )

    # Provider-agnostic config extraction. The strategy classmethod owns the
    # shape of the provider config block; the CLI spec registry contributes any
    # additional provider-specific keys (fleet_role, subscription_id, project
    # etc.) that the classmethod does not itself surface.  Neither source reads
    # global --region / --profile flags — those are AWS-scoped args and no
    # longer exist at the global CLI level.
    provider_config = (
        strategy_class.get_cli_provider_config(args) if strategy_class is not None else {}
    )
    spec = CLISpecRegistry.get(provider_type)
    if spec is not None:
        for key, value in spec.extract_config(args).items():
            if provider_config.get(key) in (None, ""):
                provider_config[key] = value

    first_provider = {
        "type": provider_type,
        "config": provider_config,
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


def _fallback_provider_name(provider_type: str, provider_data: Dict[str, Any]) -> str:
    """Generate a provider instance name when the strategy is unavailable.

    Produces a short, stable, provider-agnostic identifier by hashing the
    non-empty config values.  This avoids encoding provider-specific field
    names (region, profile, project, …) into the base-layer fallback.
    """
    import hashlib

    config = provider_data.get("config", {})
    payload = json.dumps(config, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
    return f"{provider_type}_{digest}"


def _write_config_file(
    config_file: Path, user_config: Dict[str, Any], extra_paths: Optional[Dict[str, Any]] = None
):
    """Write configuration file with multiple provider support."""
    # Process all providers
    providers_list = []
    default_provider_name: str | None = None
    for provider_data in user_config.get("providers", []):
        # provider_data["config"] is the opaque provider config dict produced by
        # get_cli_provider_config (or the interactive flow).  Pass it through
        # without unpacking provider-specific keys.
        provider_config: dict[str, Any] = dict(provider_data.get("config", {}))
        provider_type = provider_data["type"]

        # Resolve the strategy CLASS (best-effort; None if unavailable).
        # Used for name generation and for routing infrastructure defaults.
        # The classmethod-based inquiry methods require no instance and no
        # provider config on disk, so no scaffolding is needed here.
        strategy_class = None
        try:
            registry = get_container().get(ProviderRegistryPort)
            strategy_class = _get_provider_strategy(provider_type, registry=registry)
        except Exception:
            pass  # best-effort; fall back to generic name and all-defaults-in-template

        # Generate provider name via the strategy class so each provider type
        # can apply its own naming convention.  Fall back to the generic
        # AWS-style shape when no strategy class is available.
        if strategy_class is not None:
            try:
                provider_name = strategy_class.generate_provider_name(provider_data)
            except Exception:
                provider_name = _fallback_provider_name(provider_type, provider_data)
        else:
            provider_name = _fallback_provider_name(provider_type, provider_data)

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
        infrastructure_defaults = provider_data.get("infrastructure_defaults", {})
        if infrastructure_defaults:
            config_only_keys = (
                strategy_class.get_cli_extra_config_keys() if strategy_class is not None else set()
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

    config: dict[str, Any] = {
        "scheduler": {"type": user_config["scheduler_type"]},
        "provider": provider_section,
    }

    from orb.infrastructure.scheduler.registry import get_scheduler_registry

    registry = get_scheduler_registry()
    extra = registry.get_extra_config_for_type(user_config["scheduler_type"])
    config["scheduler"].update(extra)

    # Persist non-default dir paths so the runtime picks them up without env vars
    if extra_paths:
        if "logs_dir" in extra_paths:
            config.setdefault("logging", {})["file_path"] = str(extra_paths["logs_dir"] / "orb.log")
        if "work_dir" in extra_paths:
            config.setdefault("storage", {}).setdefault("json_strategy", {})["base_path"] = str(
                extra_paths["work_dir"]
            )
        if "scripts_dir" in extra_paths:
            config["scripts_dir"] = str(extra_paths["scripts_dir"])

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
