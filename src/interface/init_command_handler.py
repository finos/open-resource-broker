"""Init command handler for ORB configuration initialization."""

import json
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Optional

from cli.console import print_command, print_error, print_info, print_separator, print_success, print_newline
from config.platform_dirs import (
    get_config_location,
    get_logs_location,
    get_scripts_location,
    get_work_location,
)
from infrastructure.logging.logger import get_logger

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
        print_separator(width=60, char="━", color="green")
        print_success("  ORB initialized successfully")
        print_separator(width=60, char="━", color="green")
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
        logger.error("Failed to initialize ORB: %s", e)
        return 1


def _get_available_schedulers() -> list[dict[str, str]]:
    """Get available schedulers from registry."""
    scheduler_types = ["default", "hostfactory"]
    
    schedulers = []
    for scheduler_type in scheduler_types:
        if scheduler_type == "default":
            schedulers.append({
                "type": "default",
                "display_name": "default",
                "description": "Standalone usage"
            })
        elif scheduler_type == "hostfactory":
            schedulers.append({
                "type": "hostfactory", 
                "display_name": "hostfactory",
                "description": "IBM Spectrum Symphony integration"
            })
    
    return schedulers


def _get_available_providers() -> list[dict[str, str]]:
    """Get available providers from provider registry."""
    try:
        from providers.registry import get_provider_registry
        
        registry = get_provider_registry()
        registered_types = registry.get_registered_providers()
        
        providers = []
        for provider_type in sorted(registered_types):
            # Get display name and description based on provider type
            display_name = provider_type
            if provider_type == "aws":
                description = "Amazon Web Services"
            else:
                description = f"{provider_type.upper()} Provider"
            
            providers.append({
                "type": provider_type,
                "display_name": display_name,
                "description": description
            })
        
        # Fallback to AWS if no providers registered (for backward compatibility)
        return providers if providers else [{"type": "aws", "display_name": "aws", "description": "Amazon Web Services"}]
    except Exception:
        # Fallback to AWS if registry unavailable
        return [{"type": "aws", "display_name": "aws", "description": "Amazon Web Services"}]


def _interactive_setup() -> Dict[str, Any]:
    """Interactive configuration setup."""
    try:
        print_separator(width=60, char="=", color="cyan")
        print_info("  ORB Configuration Setup")
        print_separator(width=60, char="=", color="cyan")

        # Scheduler type
        print_info("")
        print_info("[1/4] Scheduler Type")
        print_separator(width=60, char="-", color="cyan")
        
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
        print_separator(width=60, char="-", color="cyan")
        
        # Provider type
        print_info("")
        print_info("[2/4] Cloud Provider")
        print_separator(width=60, char="-", color="cyan")
        
        providers = _get_available_providers()
        for i, provider in enumerate(providers, 1):
            print_info(f"  ({i}) {provider['display_name']} - {provider['description']}")
        
        print_info("")
        provider_choice = input("  Select provider (1): ").strip() or "1"
        try:
            provider_type = providers[int(provider_choice) - 1]["type"]
        except (ValueError, IndexError):
            # Use first available provider as default
            providers = _get_available_providers()
            provider_type = providers[0]["type"] if providers else "aws"

        print_newline()
        print_separator(width=60, char="-", color="cyan")

        # Provider configuration
        print_info("")
        print_info("[3/4] Provider Configuration")
        print_separator(width=60, char="-", color="cyan")
        
        # Get credential requirements
        requirements = _get_credential_requirements(provider_type)
        
        # Collect required parameters first (e.g., region for AWS)
        provider_config = {"type": provider_type}
        for param, info in requirements.items():
            if info.get("required"):
                default_value = "us-east-1" if param == "region" else ""
                prompt = f"  {info['description']} ({default_value}): " if default_value else f"  {info['description']}: "
                value = input(prompt).strip() or default_value
                provider_config[param] = value
        
        # Fallback for AWS if no requirements defined
        if provider_type == "aws" and not requirements:
            region = input("  Region (us-east-1): ").strip() or "us-east-1"
            provider_config["region"] = region
        
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
        success, error_msg = _test_provider_credentials(provider_type, selected_source, **provider_config)
        if success:
            print_success("Credentials verified successfully")
            if selected_source:
                provider_config["profile"] = selected_source
        else:
            print_error("[bold red]ERROR[/bold red] Authentication failed:")
            print_error(f"        {error_msg}")
            return {}
        
        # Extract final values for backward compatibility
        region = provider_config.get("region", "us-east-1")
        profile = provider_config.get("profile", "default")
        
        print_newline()
        print_separator(width=60, char="-", color="cyan")
        
        # Infrastructure discovery
        print_info("")
        print_info("[4/4] Infrastructure Discovery")
        print_separator(width=60, char="-", color="cyan")
        print_info("  Discover AWS infrastructure for template defaults?")
        print_info("  This will help create generic templates that work across regions/accounts.")
        print_info("")
        discover_choice = input("  Discover infrastructure? (y/N): ").strip().lower()
        
        infrastructure_defaults = {}
        if discover_choice in ['y', 'yes']:
            infrastructure_defaults = _discover_infrastructure(provider_type, region, profile)
        
        print_info("")

        return {
            "scheduler_type": scheduler_type,
            "provider_type": provider_type,
            "region": region,
            "profile": profile,
            "infrastructure_defaults": infrastructure_defaults,
        }
    except KeyboardInterrupt:
        print_error("\n\nSetup cancelled by user")
        raise
    except EOFError:
        print_error("\n\nUnexpected end of input")
        print_info("  Run with --non-interactive for automated setup")
        raise


def _get_available_credential_sources(provider_type: str) -> list[dict]:
    """Get available credential sources for provider."""
    if provider_type == "aws":
        try:
            from providers.aws.profile_discovery import get_available_profiles
            return get_available_profiles()
        except Exception:
            return [{"name": None, "description": "Default credentials"}]
    else:
        return [{"name": None, "description": "Default credentials"}]


def _test_provider_credentials(provider_type: str, credential_source: Optional[str], **kwargs) -> tuple[bool, str]:
    """Test provider credentials."""
    if provider_type == "aws":
        try:
            from providers.aws.session_factory import AWSSessionFactory
            region = kwargs.get("region")
            result = AWSSessionFactory.discover_credentials(credential_source, region)
            if result.get("success", False):
                return True, ""
            else:
                return False, result.get("error", "Unknown error")
        except Exception as e:
            return False, str(e)
    else:
        return False, "Provider type not supported"


def _get_credential_requirements(provider_type: str) -> dict:
    """Get credential requirements for provider."""
    if provider_type == "aws":
        return {"region": {"required": True, "description": "AWS region"}}
    else:
        return {}


def _discover_infrastructure(provider_type: str, region: str, profile: str) -> Dict[str, Any]:
    """Discover infrastructure interactively using provider strategy."""
    try:
        from providers.registry import get_provider_registry
        
        registry = get_provider_registry()
        
        # Ensure provider type is registered
        if not registry.ensure_provider_type_registered(provider_type):
            print_error(f"Failed to register provider type: {provider_type}")
            return {}
        
        # Create provider config for discovery
        provider_config = {
            "region": region,
            "profile": profile
        }
        
        # Get strategy from registry
        strategy = registry.get_or_create_strategy(provider_type, provider_config)
        
        # Check if provider strategy supports infrastructure discovery
        if hasattr(strategy, 'discover_infrastructure_interactive'):
            full_config = {
                "type": provider_type,
                "config": provider_config
            }
            return strategy.discover_infrastructure_interactive(full_config)
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
    default_provider = providers[0]["type"] if providers else "aws"
    
    return {
        "scheduler_type": args.scheduler or "default",
        "provider_type": args.provider or default_provider,
        "region": args.region or "us-east-1",
        "profile": args.profile or "default",
        "infrastructure_defaults": {},
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
    """Write configuration file with new provider naming."""
    from config.installation_detector import get_template_location

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

    # Generate provider name using provider-aware naming convention
    provider_config = {"profile": user_config["profile"], "region": user_config["region"]}

    # Use provider strategy to generate name with proper sanitization
    provider_type = user_config["provider_type"]
    
    # Get provider strategy to generate proper name
    try:
        from infrastructure.di.container import get_container
        from providers.factory import ProviderStrategyFactory
        
        container = get_container()
        factory = container.get(ProviderStrategyFactory)
        
        temp_config = {"type": provider_type, **provider_config}
        strategy = factory.create_strategy(provider_type, temp_config)
        provider_name = strategy.generate_provider_name(provider_config)
    except Exception:
        # Fallback to simple name generation
        import re
        sanitized_profile = re.sub(r'[^a-zA-Z0-9\-_]', '-', user_config['profile'])
        provider_name = f"{provider_type}_{sanitized_profile}_{user_config['region']}"

    # Create config.json with user overrides only
    provider_instance = {
        "name": provider_name,  # NEW NAMING
        "type": user_config["provider_type"],
        "enabled": True,
        "config": provider_config,
    }
    
    # Add template_defaults if infrastructure was discovered
    infrastructure_defaults = user_config.get("infrastructure_defaults", {})
    if infrastructure_defaults:
        provider_instance["template_defaults"] = infrastructure_defaults
    
    config = {
        "scheduler": {"type": user_config["scheduler_type"]},
        "provider": {
            "providers": [provider_instance]
        },
    }

    if user_config["scheduler_type"] == "hostfactory":
        config["scheduler"]["config_root"] = "$ORB_CONFIG_DIR"

    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)


def _copy_scripts(scripts_dir: Path):
    """Copy platform-specific scripts to scripts directory."""
    from config.installation_detector import get_scripts_location

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
        logger.warning(f"Failed to copy scripts: {e}")


def _get_installed_scripts_path():
    """Get scripts path for installed package using proper scheme detection."""
