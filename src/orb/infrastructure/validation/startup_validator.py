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
    ):
        self.config_path = config_path
        self.config_data: Optional[dict] = None
        self.app_config: Optional[AppConfig] = None
        self._credentials_checker = credentials_checker
        self._console = console or NullConsoleAdapter()

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

        # Discovery hierarchy
        candidates = [
            os.environ.get("ORB_CONFIG_FILE"),
            os.path.join(os.environ.get("ORB_CONFIG_DIR", ""), "config.json"),
            "./config/config.json",
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

        from orb.domain.base.ports.scheduler_port import SchedulerPort
        from orb.infrastructure.di.container import get_container

        container = get_container()
        scheduler = container.get(SchedulerPort)

        template_paths = cast(Any, scheduler).get_template_paths()
        return any(Path(path).exists() for path in template_paths)

    def _check_default_config(self) -> bool:
        """Check if default_config.json template exists."""
        from orb.config.services.path_resolution_service import PathResolutionService

        resolved_path = PathResolutionService().resolve_file_path("template", "default_config.json")

        return Path(resolved_path).exists()

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
        from orb.config.services.path_resolution_service import PathResolutionService

        svc = PathResolutionService()

        self._console.info("")
        self._console.info("Configuration not found in:")

        default_resolved = svc.resolve_file_path("template", "default_config.json")
        if default_resolved:
            self._console.info(f"  - {default_resolved}")

        config_resolved = svc.resolve_file_path("config", "config.json")
        if config_resolved:
            self._console.info(f"  - {config_resolved}")

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
