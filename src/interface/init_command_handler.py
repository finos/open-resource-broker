"""Init command handler for ORB configuration initialization."""

import json
import sys
from pathlib import Path
from typing import Any, Dict

from cli.console import print_separator, print_success, print_info, print_command
from config.platform_dirs import get_config_location, get_work_location, get_logs_location
from infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def handle_init(args) -> int:
    """Handle orb init command."""
    try:
        # Determine config directory
        if args.config_dir:
            config_dir = Path(args.config_dir)
            work_dir = config_dir.parent / "work"
            logs_dir = config_dir.parent / "logs"
        else:
            config_dir = get_config_location()
            work_dir = get_work_location()
            logs_dir = get_logs_location()

        # Check if already initialized
        config_file = config_dir / "config.json"
        if config_file.exists() and not args.force:
            print_error(f"Configuration already exists at {config_dir}")
            print_info(f"  Use --force to reinitialize")
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

        # Success message with separator
        print_separator(width=60, char="━", color="green")
        print_success("  ORB initialized successfully")
        print_separator(width=60, char="━", color="green")
        print_info("")  # Empty line
        print_info("Created:")
        print_info(f"  Config: {config_dir}")
        print_info(f"  Work:   {work_dir}")
        print_info(f"  Logs:   {logs_dir}")
        print_info("")  # Empty line
        print_info("Next Steps:")
        print_command("  1. Generate templates: orb templates generate")
        print_command("  2. List templates:     orb templates list")
        print_command("  3. Show config:        orb config show")
        
        return 0

    except KeyboardInterrupt:
        print_error("\nInitialization cancelled by user")
        return 1
    except Exception as e:
        print_error(f"Failed to initialize ORB")
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

        # Provider type (only AWS for now)
        print_info("")
        print_info("[2/3] Cloud Provider")
        print_separator(width=60, char="-", color="cyan")
        print_info("  Provider: aws (only option currently)")
        provider_type = "aws"

        # AWS configuration
        print_info("")
        print_info("[3/3] AWS Configuration")
        print_separator(width=60, char="-", color="cyan")
        region = input("  Region [us-east-1]: ").strip() or "us-east-1"
        profile = input("  Profile [default]: ").strip() or "default"
        print_info("")

        return {
            "scheduler_type": scheduler_type,
            "provider_type": provider_type,
            "region": region,
            "profile": profile,
        }
    except KeyboardInterrupt:
        print_error("\n\nSetup cancelled by user")
        raise
    except EOFError:
        print_error("\n\nUnexpected end of input")
        print_info("  Run with --non-interactive for automated setup")
        raise


def _get_default_config(args) -> Dict[str, Any]:
    """Get default configuration from args."""
    return {
        "scheduler_type": args.scheduler or "default",
        "provider_type": args.provider or "aws",
        "region": args.region or "us-east-1",
        "profile": args.profile or "default",
    }


def _create_directories(config_dir: Path, work_dir: Path, logs_dir: Path):
    """Create directory structure."""
    dirs = [
        config_dir,
        work_dir,
        work_dir / "cache",
        logs_dir,
    ]

    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.info("Created directory: %s", dir_path)


def _write_config_file(config_file: Path, user_config: Dict[str, Any]):
    """Write configuration file."""
    try:
        # Load default config template
        try:
            import config
            template_path = Path(config.__file__).parent / "default_config.json"
            with open(template_path) as f:
                full_config = json.load(f)
        except (ImportError, FileNotFoundError):
            # Fallback minimal config
            full_config = {
                "version": "2.0.0",
                "scheduler": {"type": "default"},
                "provider": {
                    "default_provider_type": "aws",
                    "providers": []
                }
            }

        # Update with user values
        full_config["scheduler"]["type"] = user_config["scheduler_type"]
        full_config["provider"]["default_provider_type"] = user_config["provider_type"]
        full_config["provider"]["providers"] = [
            {
                "name": f"{user_config['provider_type']}-default",
                "type": user_config["provider_type"],
                "enabled": True,
                "config": {
                    "region": user_config["region"],
                    "profile": user_config["profile"]
                }
            }
        ]

        # Write config file
        with open(config_file, 'w') as f:
            json.dump(full_config, f, indent=2)

        logger.info("Created configuration file: %s", config_file)
        
    except Exception as e:
        print_error(f"Failed to write config file: {config_file}")
        print_error(f"  {e}")
        print_info("")
        print_info("To retry:")
        print_info(f"  Check directory permissions: {config_file.parent}")
        raise