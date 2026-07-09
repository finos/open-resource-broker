"""Startup validation for ORB application."""

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional, cast

from pydantic import ValidationError

from orb._package import DOCS_URL
from orb.config.schemas.app_schema import AppConfig
from orb.domain.base.ports.console_port import ConsolePort
from orb.infrastructure.adapters.null_console_adapter import NullConsoleAdapter


class StartupValidator:
    """Validates ORB startup requirements with fail-fast behavior."""

    def __init__(
        self,
        config_path: Optional[str] = None,
        credentials_checker: Optional[Callable[[list], bool]] = None,
        console: Optional[ConsolePort] = None,
        scheduler_port: Optional[Any] = None,
    ):
        self.config_path = config_path
        self.config_data: Optional[dict] = None
        self.app_config: Optional[AppConfig] = None
        self._credentials_checker = credentials_checker
        self._console = console or NullConsoleAdapter()
        self._scheduler_port = scheduler_port

    def validate_startup(self) -> None:
        """Validate startup requirements. Exit on critical failures."""
        try:
            self._validate_critical()
            self._validate_important()
        except SystemExit:
            raise
        except Exception as e:
            self._error(f"Unexpected validation error: {e}")
            sys.exit(1)

    def _validate_critical(self) -> None:
        """Critical validation - must pass to start."""
        # 1. Config file exists
        if not self._find_config_file():
            self._console.error("Configuration file not found")
            self._print_config_help()
            sys.exit(1)

        # 2. Config is valid JSON
        try:
            with open(self.config_path or "") as f:  # type: ignore[arg-type]
                self.config_data = json.load(f)
        except json.JSONDecodeError as e:
            self._console.error(f"Invalid JSON in config file: {self.config_path}")
            self._console.error(f"  {e}")
            self._console.info("")
            self._console.info("To fix:")
            self._console.info(f"  1. Check JSON syntax in: {self.config_path}")
            self._console.command("  2. Or reinitialize: orb init --force")
            sys.exit(1)
        except Exception as e:
            self._console.error(f"Cannot read config file: {self.config_path}")
            self._console.error(f"  {e}")
            self._console.info("")
            self._console.info("To fix:")
            self._console.info("  1. Check file permissions")
            self._console.command("  2. Or reinitialize: orb init --force")
            sys.exit(1)

        # 3. Config validates against Pydantic schema
        try:
            self.app_config = AppConfig(**(self.config_data or {}))
        except ValidationError as e:
            self._console.error(f"Invalid configuration in: {self.config_path}")
            for error in e.errors():
                field = " -> ".join(str(x) for x in error["loc"])
                self._console.error(f"  {field}: {error['msg']}")
            self._console.info("")
            self._console.info("To fix:")
            self._console.info(f"  1. Edit config file: {self.config_path}")
            self._console.command("  2. Or reinitialize: orb init --force")
            sys.exit(1)

    def _validate_important(self) -> None:
        """Important validation - warn but continue."""
        # 1. Default config template exists
        if not self._check_default_config():
            self._console.info("Default config template not found")
            self._console.command("  Run: orb init")

        # 2. Templates file exists
        if not self._check_templates_file():
            self._console.info("Templates file not found")
            self._console.command("  Run: orb templates generate")

        # 3. Provider credentials configured
        if not self._check_provider_credentials():
            self._console.warning("Provider credentials not configured")
            self._console.info("  Check your provider configuration and credentials")

    def _find_config_file(self) -> bool:
        """Find config file using discovery hierarchy."""
        if self.config_path and Path(self.config_path).exists():
            return True

        from pathlib import Path as _Path

        from orb.config.platform_dirs import get_config_location

        # Discovery hierarchy — must match ConfigurationManager's resolution order.
        # ORB_CONFIG_FILE and ORB_CONFIG_DIR/config.json are kept explicitly so the
        # validator can surface a meaningful error when they are set but wrong.
        # Then the platform-dirs path (respects venv / pyproject / uv-tool detection).
        # Finally the user-home path so operators running from inside a checkout
        # (which pins platform_dirs to the repo) still pick up ~/.orb/config/config.json
        # when the repo has no local config.
        candidates = [
            os.environ.get("ORB_CONFIG_FILE"),
            os.path.join(os.environ.get("ORB_CONFIG_DIR", ""), "config.json"),
            str(get_config_location() / "config.json"),
            str(_Path.home() / ".orb" / "config" / "config.json"),
        ]

        for candidate in candidates:
            if candidate and Path(candidate).exists():
                self.config_path = candidate
                return True

        return False

    def _check_templates_file(self) -> bool:
        """Check if any template files exist in the hierarchy."""
        if not self.app_config:
            return False

        scheduler = self._scheduler_port
        if scheduler is None:
            # Resolve from the DI container only when no scheduler was
            # supplied at construction time.  The container factory is
            # registered on `orb.bootstrap` import so this is safe, but
            # callers that already hold a container should prefer passing
            # it in to avoid the service-locator access here.
            try:
                from orb.application.ports.scheduler_port import SchedulerPort
                from orb.infrastructure.di.container import get_container

                scheduler = get_container().get(SchedulerPort)
            except Exception:
                return False

        template_paths = cast(Any, scheduler).get_template_paths()
        return any(Path(path).exists() for path in template_paths)

    def _check_default_config(self) -> bool:
        """Check if the packaged default_config.json template exists.

        Uses ``importlib.resources`` so the check succeeds from a wheel, an
        editable install, and a plain development checkout without requiring
        ``orb init`` to have been run first.  The previous path-resolution
        approach only worked after initialisation because it looked for a
        user-written config file rather than the package-shipped template.
        """
        try:
            from importlib.resources import files

            return files("orb.config").joinpath("default_config.json").is_file()
        except Exception:
            return False

    def _check_provider_credentials(self) -> bool:
        """Check if provider credentials are configured for all configured providers."""
        if not self.app_config:
            return False

        if self._credentials_checker is None:
            return True  # No checker provided — skip credential validation

        try:
            providers = self.app_config.provider.providers
            if not providers:
                return True  # No providers configured

            return self._credentials_checker(providers)

        except Exception:
            return True  # Don't fail on unexpected errors

    def _print_config_help(self) -> None:
        """Print helpful config location information."""
        from orb.config.platform_dirs import get_config_location

        self._console.info("")
        self._console.info("Configuration not found in:")

        # Show the explicit env-var paths when set, then the platform-dirs path.
        tried: list[str] = []
        env_file = os.environ.get("ORB_CONFIG_FILE")
        if env_file:
            tried.append(env_file)
        env_dir = os.environ.get("ORB_CONFIG_DIR")
        if env_dir:
            tried.append(os.path.join(env_dir, "config.json"))
        tried.append(str(get_config_location() / "config.json"))

        for path in tried:
            self._console.info(f"  - {path}")

        self._console.info("")
        self._console.info("To initialize:")
        self._console.command("  orb init")
        self._console.info("")
        self._console.info("Or specify config:")
        self._console.command("  orb --config /path/to/config.json templates list")
        self._console.info("")
        self._console.info(f"Documentation: {DOCS_URL}")

    def _error(self, message: str) -> None:
        """Print error message to stderr."""
        self._console.error(message)

    def _warn(self, message: str) -> None:
        """Print warning message to stderr."""
        self._console.warning(message)
