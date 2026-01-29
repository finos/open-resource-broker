"""Init command handler for ORB configuration initialization."""

import json
import platform
import shutil
from pathlib import Path
from typing import Any, Dict

from cli.console import print_command, print_error, print_info, print_separator, print_success
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


def _interactive_setup() -> Dict[str, Any]:
    """Interactive configuration setup."""
    try:
        print_separator(width=60, char="=", color="cyan")
        print_info("  ORB Configuration Setup")
        print_separator(width=60, char="=", color="cyan")

        # Scheduler type
        print_info("")
        print_info("[1/3] Scheduler Type")
        print_separator(width=60, char="-", color="cyan")
        print_info("  1. default    - Standalone usage")
        print_info("  2. hostfactory - IBM Spectrum Symphony integration")
        print_info("")
        scheduler_choice = input("  Select scheduler [1]: ").strip() or "1"
        scheduler_type = "default" if scheduler_choice == "1" else "hostfactory"

        # Provider type
        print_info("")
        print_info("[2/4] Cloud Provider")
        print_separator(width=60, char="-", color="cyan")
        print_info("  1. aws - Amazon Web Services")
        print_info("")
        provider_choice = input("  Select provider [1]: ").strip() or "1"
        provider_type = "aws"  # Only AWS supported currently

        # Provider configuration
        print_info("")
        print_info("[3/4] Provider Configuration")
        print_separator(width=60, char="-", color="cyan")
        if provider_type == "aws":
            region = input("  Region [us-east-1]: ").strip() or "us-east-1"
            profile = input("  Profile [default]: ").strip() or "default"
        else:
            region = "us-east-1"
            profile = "default"
        
        # Infrastructure discovery
        print_info("")
        print_info("[4/4] Infrastructure Discovery")
        print_separator(width=60, char="-", color="cyan")
        print_info("  Discover AWS infrastructure for template defaults?")
        print_info("  This will help create generic templates that work across regions/accounts.")
        print_info("")
        discover_choice = input("  Discover infrastructure? [y/N]: ").strip().lower()
        
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


def _discover_infrastructure(provider_type: str, region: str, profile: str) -> Dict[str, Any]:
    """Discover infrastructure interactively using provider strategy."""
    try:
        from infrastructure.di.container import get_container
        from domain.base.ports.provider_port import ProviderPort
        
        container = get_container()
        provider_strategy = container.get(ProviderPort)
        
        # Create provider config for discovery
        provider_config = {
            "type": provider_type,
            "config": {"region": region, "profile": profile}
        }
        
        # Check if provider strategy supports infrastructure discovery
        if hasattr(provider_strategy, 'discover_infrastructure_interactive'):
            return provider_strategy.discover_infrastructure_interactive(provider_config)
        else:
            print_info(f"Infrastructure discovery not supported for provider type: {provider_type}")
            return {}
            
    except Exception as e:
        print_error(f"Failed to discover infrastructure: {e}")
        print_info("Continuing without infrastructure discovery...")
        return {}


def _get_default_config(args) -> Dict[str, Any]:
    """Get default configuration from args."""
    return {
        "scheduler_type": args.scheduler or "default",
        "provider_type": args.provider or "aws",
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

    # Use provider type from user config instead of hardcoded "aws"
    provider_type = user_config["provider_type"]
    provider_name = f"{provider_type}_{user_config['profile']}_{user_config['region']}"

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
