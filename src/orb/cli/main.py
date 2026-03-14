"""
Main CLI entry point.

This module provides the main() entry point and re-exports parse_args
and execute_command for backward compatibility.

Argument parsing logic: cli.args
Command routing logic:   cli.router
"""

import asyncio
import logging
import sys

# Re-export for backward compatibility
from orb.cli.args import parse_args
from orb.cli.router import execute_command
from orb.infrastructure.logging.logger import get_logger

__all__ = ["main", "parse_args", "execute_command"]


async def _show_resource_help(resource):
    """Show help for a resource when no action is provided."""
    import subprocess  # nosec B404

    subprocess.run([sys.executable, "-m", "orb", resource, "--help"], check=False)  # nosec B603
    return {"success": True, "message": f"Showed help for {resource}"}


async def main() -> None:
    """Serve as main CLI entry point."""
    try:
        if len(sys.argv) == 1:
            sys.argv.append("--help")

        from io import StringIO

        old_stderr = sys.stderr
        sys.stderr = captured_stderr = StringIO()

        try:
            args, resource_parsers = parse_args()
            sys.stderr = old_stderr
        except SystemExit as e:
            sys.stderr = old_stderr
            error_output = captured_stderr.getvalue()

            if e.code == 2 and len(sys.argv) >= 2 and "required: action" in error_output:
                resource_name = sys.argv[1]
                if resource_name in [
                    "templates",
                    "machines",
                    "requests",
                    "system",
                    "config",
                    "providers",
                    "storage",
                    "scheduler",
                ]:
                    original_argv = sys.argv[:]
                    sys.argv = [sys.argv[0], resource_name, "--help"]
                    try:
                        parse_args()
                    except SystemExit as help_exit:
                        sys.argv = original_argv
                        if help_exit.code == 0:
                            sys.exit(0)
                    sys.argv = original_argv

            if error_output.strip():
                print(error_output.strip(), file=sys.stderr)
            raise

        # Setup environment after arg parse — skip for init (it calls get_config_location() directly)
        if args.resource != "init":
            from orb.run import setup_environment

            setup_environment()

        # Handle completion generation
        if args.completion:
            from orb.cli.completion import generate_bash_completion, generate_zsh_completion

            if args.completion == "bash":
                print(generate_bash_completion())
            elif args.completion == "zsh":
                print(generate_zsh_completion())
            return

        getattr(logging, args.log_level.upper())
        logger = get_logger(__name__)

        # Handle help display early - no need for app initialization
        if (
            hasattr(args, "action")
            and args.action is None
            and args.resource
            in [
                "templates",
                "template",
                "machines",
                "machine",
                "requests",
                "request",
                "providers",
                "provider",
                "infrastructure",
                "infra",
            ]
        ):
            resource_map = {
                "template": "templates",
                "machine": "machines",
                "request": "requests",
                "provider": "providers",
                "infra": "infrastructure",
            }
            help_resource = resource_map.get(args.resource, args.resource)
            if help_resource in resource_parsers:
                resource_parsers[help_resource].print_help()
                sys.exit(0)

        # Apply global overrides BEFORE any command execution
        scheduler_override_active = False
        if hasattr(args, "scheduler") and args.scheduler:
            try:
                from orb.domain.base.ports.configuration_port import ConfigurationPort
                from orb.infrastructure.di.container import get_container

                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_scheduler_strategy(args.scheduler)
                scheduler_override_active = True
            except Exception as e:
                logger.warning("Failed to override scheduler strategy: %s", e, exc_info=True)

        if hasattr(args, "provider") and args.provider:
            try:
                from orb.domain.base.ports.configuration_port import ConfigurationPort
                from orb.infrastructure.di.container import get_container

                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_provider_instance(args.provider)
            except Exception as e:
                logger.warning("Failed to override provider instance: %s", e, exc_info=True)

        if hasattr(args, "region") and args.region:
            try:
                from orb.domain.base.ports.configuration_port import ConfigurationPort
                from orb.infrastructure.di.container import get_container

                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_provider_region(args.region)
            except Exception as e:
                logger.warning("Failed to override region: %s", e, exc_info=True)

        if hasattr(args, "profile") and args.profile:
            try:
                from orb.domain.base.ports.configuration_port import ConfigurationPort
                from orb.infrastructure.di.container import get_container

                container = get_container()
                config = container.get(ConfigurationPort)
                config.override_provider_profile(args.profile)
            except Exception as e:
                logger.warning("Failed to override profile: %s", e, exc_info=True)

        # Skip application initialization for init command
        if args.resource == "init":
            from orb.interface.init_command_handler import handle_init

            result = await handle_init(args)
            sys.exit(result)

        if args.resource in ["templates", "template"] and args.action == "generate":
            from orb.interface.templates_generate_handler import handle_templates_generate

            try:
                result = await handle_templates_generate(args)
                if result.get("status") == "success":
                    sys.exit(0)
                else:
                    print(f"Error: {result.get('message')}", file=sys.stderr)
                    sys.exit(1)
            except Exception:
                import traceback

                traceback.print_exc()
                sys.exit(1)

        # All other commands need full Application initialization
        try:
            from orb.bootstrap import Application

            app = Application(args.config, skip_validation=False)
            dry_run = getattr(args, "dry_run", False)
            if not await app.initialize(dry_run=dry_run):
                raise RuntimeError("Failed to initialize application")
        except Exception as e:
            logger.error("Failed to initialize application: %s", e, exc_info=True)
            if args.verbose:
                import traceback

                traceback.print_exc()
            sys.exit(1)

        try:
            from orb.infrastructure.mocking.dry_run_context import dry_run_context

            if args.dry_run:
                logger.info("DRY-RUN mode activated - using mocked operations")
                with dry_run_context(True):
                    result = await execute_command(args, app, resource_parsers)
            else:
                result = await execute_command(args, app, resource_parsers)

            if isinstance(result, tuple) and len(result) == 2:
                formatted_output, exit_code = result
            else:
                formatted_output = result
                exit_code = 0

            if args.output:
                with open(args.output, "w") as f:
                    f.write(formatted_output)
                if not args.quiet:
                    print(f"Output written to {args.output}")
            else:
                print(formatted_output)

            if exit_code != 0:
                sys.exit(exit_code)

        except Exception as e:
            from orb.cli.response_formatter import create_cli_formatter
            from orb.domain.base.exceptions import DomainException

            if isinstance(e, DomainException):
                logger.exception("Domain error: %s", e, exc_info=True)
            else:
                logger.exception("Unexpected error: %s", e, exc_info=True)
                if args.verbose:
                    import traceback

                    traceback.print_exc()

            formatter = create_cli_formatter()
            output_format = getattr(args, "format", "json")
            error_output, exit_code = formatter.format_error(e, output_format)

            if not args.quiet:
                print(error_output)
            sys.exit(exit_code)
        finally:
            if scheduler_override_active:
                try:
                    from orb.domain.base.ports.configuration_port import ConfigurationPort
                    from orb.infrastructure.di.container import get_container

                    container = get_container()
                    config = container.get(ConfigurationPort)
                    config.restore_scheduler_strategy()
                except Exception as e:
                    logger.warning("Failed to restore scheduler strategy: %s", e, exc_info=True)

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(130)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
