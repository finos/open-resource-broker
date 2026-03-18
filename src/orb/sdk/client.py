"""
Open Resource Broker SDK main client implementation.

Provides a clean, async-first API that leverages the existing
application service and CQRS infrastructure with automatic
handler discovery for zero code duplication.
"""

import asyncio
from contextlib import suppress
from typing import Any, Callable, Dict, Optional

from orb.bootstrap import Application
from orb.config.managers.configuration_manager import ConfigurationManager
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.di.container import create_container

from .config import SDKConfig
from .discovery import MethodInfo, SDKMethodDiscovery
from .exceptions import (
    ConfigurationError,
    NotFoundError,
    ProviderError,
    RequestTimeoutError,
    SDKError,
)
from .middleware import SDKMiddleware, build_middleware_chain


class ORBClient:
    """
    Main SDK interface for Open Resource Broker operations.

    Provides automatic method discovery from existing CQRS handlers,
    ensuring zero code duplication while maintaining clean architecture
    principles and full integration with the existing system.

    Usage:
        async with ORBClient(provider="aws") as sdk:
            templates = await sdk.list_templates(active_only=True)
            request = await sdk.create_request(template_id="basic", count=5)
            status = await sdk.get_request_status(request_id=request.id)
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        config: Optional[dict[str, Any]] = None,
        config_path: Optional[str] = None,
        app_config: Optional[dict[str, Any]] = None,
        scheduler: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Initialize the Open Resource Broker SDK.

        Args:
            provider: Cloud provider type (aws, mock, etc.). Defaults to value from config/env.
            config: SDK configuration dictionary (timeout, log_level, etc.)
            config_path: Path to configuration file
            app_config: Application config dict — replaces config.json on disk.
                        Pass the same structure as config.json to run without filesystem.
            **kwargs: Additional configuration options
        """
        # Configuration setup
        if config:
            self._config = SDKConfig.from_dict(config)
        elif config_path:
            self._config = SDKConfig.from_file(config_path)
        else:
            self._config = SDKConfig.from_env()

        # Override with explicit parameters
        if provider is not None:
            self._config.provider = provider
        if scheduler is not None:
            self._config.scheduler = scheduler
        if config_path:
            self._config.config_path = config_path

        # Add any additional kwargs to custom config
        if kwargs:
            self._config.custom_config.update(kwargs)

        # Validate configuration
        self._config.validate()

        # Application-level config dict (replaces config.json)
        self._app_config = app_config

        # Internal components (lazy initialization)
        self._app: Optional[Application] = None
        self._container = None
        self._query_bus = None
        self._command_bus = None
        self._discovery: Optional[SDKMethodDiscovery] = None
        self._methods: dict[str, Callable] = {}
        self._middlewares: list[SDKMiddleware] = []
        self._initialized = False

    async def initialize(self) -> bool:
        """
        Initialize the SDK with the configured provider and settings.

        Returns:
            bool: True if initialization successful

        Raises:
            ConfigurationError: If configuration is invalid
            ProviderError: If provider initialization fails
            SDKError: If SDK initialization fails
        """
        if self._initialized:
            return True

        try:
            # skip_validation=True bypasses StartupValidator (sys.exit) in Application.__init__.
            # We still validate config_path existence here so callers get a clean
            # ConfigurationError instead of a cryptic downstream failure.
            import os

            if self._config.config_path and not os.path.exists(self._config.config_path):
                raise ConfigurationError(
                    f"Configuration file not found: {self._config.config_path}"
                )

            # Create an isolated container so multiple ORBClient instances in the
            # same process don't share state via the module-level singleton.
            self._container = create_container()

            # If a scheduler override is requested, pre-register a ConfigurationManager
            # with the override already applied. This mirrors bootstrap._ensure_container()
            # but must happen before Application is constructed so that when initialize()
            # calls _ensure_container() it finds this instance and does not create a fresh
            # one without the override. TemplateConfigurationManager is constructed lazily
            # on first use and calls container.get(SchedulerPort) → config.get_scheduler_strategy()
            # at that point — so the override must be in place then.
            if self._config.scheduler:
                cm = ConfigurationManager(
                    config_file=self._config.config_path, config_dict=self._app_config
                )
                cm.override_scheduler_strategy(self._config.scheduler)
                self._container.register_instance(ConfigurationManager, cm)

            # skip_validation=True bypasses StartupValidator (sys.exit) in Application.__init__
            self._app = Application(
                config_path=self._config.config_path,
                config_dict=self._app_config,
                skip_validation=True,
                container=self._container,
            )

            if not await self._app.initialize():
                raise ProviderError(
                    f"Failed to initialize {self._config.provider} provider",
                    provider=self._config.provider,
                )

            # Apply region/profile overrides from SDK config (mirrors CLI pattern).
            # Use self._container (the per-client isolated container), not the
            # module-level singleton returned by get_container(), so that overrides
            # from one ORBClient instance never bleed into another.
            if self._config.region or self._config.profile:
                config_port = self._container.get(ConfigurationPort)
                if self._config.region:
                    config_port.override_provider_region(self._config.region)
                if self._config.profile:
                    config_port.override_provider_profile(self._config.profile)

            # Get CQRS buses directly from the initialized application
            self._query_bus = self._app.get_query_bus()
            self._command_bus = self._app.get_command_bus()

            if not self._query_bus or not self._command_bus:
                raise ConfigurationError("CQRS buses not available")

            # Resolve scheduler formatting from DI container (graceful fallback if not registered)
            from orb.application.ports.scheduler_port import SchedulerPort

            scheduler_port = self._container.get_optional(SchedulerPort)

            # Initialize method discovery with scheduler formatting
            self._discovery = SDKMethodDiscovery(scheduler_port=scheduler_port)

            # Auto-discover all handler methods using CQRS buses
            self._methods = await self._discovery.discover_cqrs_methods(
                self._query_bus, self._command_bus
            )

            # Dynamically add methods to SDK instance.
            # Skip names that have explicit orchestrator-backed implementations
            # defined directly on this class — those take precedence.
            for method_name, method_func in self._methods.items():
                if method_name not in self._EXPLICIT_METHODS:
                    setattr(self, method_name, method_func)

            # Apply middleware if any were added before initialization
            if self._middlewares:
                self._apply_middleware_to_methods()

            self._initialized = True
            return True

        except SystemExit as e:
            # Defensive catch: if any downstream code calls sys.exit, convert it so
            # the SDK never kills the caller's process.
            raise ConfigurationError(f"Configuration validation failed (exit code {e.code})") from e
        except Exception as e:
            if isinstance(e, (SDKError, ConfigurationError, ProviderError)):
                raise
            raise SDKError(f"SDK initialization failed: {e!s}")

    async def cleanup(self) -> None:
        """Clean up resources and connections."""

        with suppress(Exception):
            if self._app and hasattr(self._app, "cleanup"):
                await self._app.cleanup()  # type: ignore[attr-defined]

        # Always clean up state
        self._initialized = False
        self._methods.clear()

        if self._container:
            self._container.clear()
            self._container = None

        # Remove dynamically added methods (skip explicit orchestrator-backed methods)
        if self._discovery:
            for method_name in self._discovery.list_available_methods():
                if method_name not in self._EXPLICIT_METHODS and hasattr(self, method_name):
                    delattr(self, method_name)

    # Context manager support
    async def __aenter__(self) -> "ORBClient":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit with cleanup."""
        await self.cleanup()

    # SDK introspection methods
    def list_available_methods(self) -> list[str]:
        """
        List all available SDK methods discovered from handlers.

        Returns:
            List of method names available on this SDK instance
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        return sorted(self._EXPLICIT_METHODS | set(self._methods.keys()))

    def get_method_info(self, method_name: str) -> Optional[MethodInfo]:
        """
        Get information about a specific SDK method.

        Args:
            method_name: Name of the method to get info for

        Returns:
            MethodInfo object with method details, or None if not found
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        if not self._discovery:
            return None

        return self._discovery.get_method_info(method_name)

    def get_method_parameters(self, method_name: str) -> Optional[Dict[str, str]]:
        """
        Get supported parameters for a specific SDK method, including CLI-style aliases.

        Args:
            method_name: Name of the method to get parameters for

        Returns:
            Dict mapping parameter names (including CLI aliases) to CQRS parameter names,
            or None if method not found
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        method_info = self.get_method_info(method_name)
        if not method_info:
            return None

        from .parameter_mapping import ParameterMapper

        return ParameterMapper.get_supported_parameters(method_info.original_class)

    def get_methods_by_type(self, handler_type: str) -> list[str]:
        """
        Get methods filtered by handler type.

        Args:
            handler_type: 'command' or 'query'

        Returns:
            List of method names for the specified handler type
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        if not self._discovery:
            return []

        methods = []
        for method_name in self._discovery.list_available_methods():
            method_info = self._discovery.get_method_info(method_name)
            if method_info and method_info.handler_type == handler_type:
                methods.append(method_name)

        return methods

    # Configuration and status methods
    @property
    def provider(self) -> str:
        """Get the configured provider type."""
        return self._config.provider

    @property
    def initialized(self) -> bool:
        """Check if SDK is initialized."""
        return self._initialized

    @property
    def config(self) -> SDKConfig:
        """Get the SDK configuration."""
        return self._config

    def get_stats(self) -> dict[str, Any]:
        """
        Get SDK statistics and information.

        Returns:
            Dictionary with SDK statistics
        """
        if not self._initialized:
            return {
                "initialized": False,
                "provider": self._config.provider,
                "methods_discovered": 0,
            }

        command_methods = self.get_methods_by_type("command")
        query_methods = self.get_methods_by_type("query")

        return {
            "initialized": True,
            "provider": self._config.provider,
            "methods_discovered": len(self._methods),
            "command_methods": len(command_methods),
            "query_methods": len(query_methods),
            "available_methods": list(self._methods.keys()),
        }

    async def batch(self, operations: list) -> list[Any]:
        """
        Execute multiple SDK operations concurrently and return results in order.

        Uses asyncio.gather under the hood. If any operation raises, the exception
        is captured and included in the results list rather than re-raised.

        Args:
            operations: List of awaitables returned by SDK methods

        Returns:
            List of results in the same order as the input operations.
            Failed operations have their exception instance at that index.

        Raises:
            SDKError: If the SDK is not initialized
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        if not operations:
            return []

        return list(await asyncio.gather(*operations, return_exceptions=True))

    def add_middleware(self, middleware: SDKMiddleware) -> None:
        """
        Add middleware to the SDK method execution pipeline.

        Middleware is applied in order — first added is outermost (called first).
        Can be called before or after initialization.

        Args:
            middleware: SDKMiddleware instance to add
        """
        self._middlewares.append(middleware)

        # Re-wrap already-discovered methods if initialized
        if self._initialized:
            self._apply_middleware_to_methods()

    def _apply_middleware_to_methods(self) -> None:
        """Re-wrap all discovered methods with the current middleware chain."""
        for method_name, raw_method in self._methods.items():
            wrapped = build_middleware_chain(self._middlewares, method_name, raw_method)
            setattr(self, method_name, wrapped)

    # Names of methods that have explicit orchestrator-backed implementations below.
    # The setattr loop in initialize() skips these so discovery does not overwrite them.
    _EXPLICIT_METHODS: frozenset[str] = frozenset(
        {
            "request_machines",
            "get_request_status",
            "list_requests",
            "return_machines",
            "cancel_request",
            "list_machines",
            "get_machine",
            "list_templates",
            "get_template",
            "create_template",
            "update_template",
            "delete_template",
            "validate_template",
            "refresh_templates",
        }
    )

    # --- Orchestrator-backed explicit methods ---
    # Each method resolves its orchestrator from the DI container and calls it
    # directly, bypassing the CQRS bus discovery layer for these core operations.

    async def request_machines(self, template_id: str, count: int, **kwargs) -> dict:
        """Request machines via AcquireMachinesOrchestrator.

        Equivalent to: orb machines request <template_id> <count>
        """
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.acquire_machines import (
            AcquireMachinesOrchestrator,
        )
        from orb.application.services.orchestration.dtos import AcquireMachinesInput

        orchestrator = self._container.get(AcquireMachinesOrchestrator)
        result = await orchestrator.execute(
            AcquireMachinesInput(
                template_id=template_id,
                requested_count=count,
                wait=kwargs.get("wait", False),
                timeout_seconds=kwargs.get("timeout_seconds", 300),
                additional_data=kwargs.get("additional_data", {}),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_request_response(result.raw)
        return result.raw

    async def get_request_status(self, request_ids: list, **kwargs) -> dict:
        """Get status for one or more requests via GetRequestStatusOrchestrator."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import GetRequestStatusInput
        from orb.application.services.orchestration.get_request_status import (
            GetRequestStatusOrchestrator,
        )

        orchestrator = self._container.get(GetRequestStatusOrchestrator)
        result = await orchestrator.execute(
            GetRequestStatusInput(
                request_ids=list(request_ids),
                all_requests=kwargs.get("all_requests", False),
                detailed=kwargs.get("detailed", False),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_request_status_response(result.requests)
        return {"requests": result.requests}

    async def list_requests(self, **kwargs) -> dict:
        """List requests via ListRequestsOrchestrator."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import ListRequestsInput
        from orb.application.services.orchestration.list_requests import (
            ListRequestsOrchestrator,
        )

        orchestrator = self._container.get(ListRequestsOrchestrator)
        result = await orchestrator.execute(
            ListRequestsInput(
                status=kwargs.get("status"),
                limit=kwargs.get("limit", 50),
                offset=kwargs.get("offset", 0),
                sync=kwargs.get("sync", False),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_request_status_response(result.requests)
        return {"requests": result.requests}

    async def return_machines(self, machine_ids: list, **kwargs) -> dict:
        """Return machines via ReturnMachinesOrchestrator."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import ReturnMachinesInput
        from orb.application.services.orchestration.return_machines import (
            ReturnMachinesOrchestrator,
        )

        orchestrator = self._container.get(ReturnMachinesOrchestrator)
        result = await orchestrator.execute(
            ReturnMachinesInput(
                machine_ids=list(machine_ids),
                all_machines=kwargs.get("all_machines", False),
                force=kwargs.get("force", False),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_request_response(result.raw)
        return result.raw

    async def cancel_request(self, request_id: str, **kwargs) -> dict:
        """Cancel a request via CancelRequestOrchestrator."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.cancel_request import (
            CancelRequestOrchestrator,
        )
        from orb.application.services.orchestration.dtos import CancelRequestInput

        orchestrator = self._container.get(CancelRequestOrchestrator)
        result = await orchestrator.execute(
            CancelRequestInput(
                request_id=request_id,
                reason=kwargs.get("reason", "Cancelled via API"),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_request_response(result.raw)
        return result.raw

    async def list_machines(self, **kwargs) -> dict:
        """List machines via ListMachinesOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import ListMachinesInput
        from orb.application.services.orchestration.list_machines import (
            ListMachinesOrchestrator,
        )

        orchestrator = self._container.get(ListMachinesOrchestrator)
        result = await orchestrator.execute(
            ListMachinesInput(
                status=kwargs.get("status"),
                provider_name=kwargs.get("provider_name"),
                request_id=kwargs.get("request_id"),
                limit=kwargs.get("limit", 100),
                offset=kwargs.get("offset", 0),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_machine_status_response(result.machines)
        return {"machines": [m.to_dict() for m in result.machines if hasattr(m, "to_dict")]}

    async def get_machine(self, machine_id: str, **kwargs) -> dict:
        """Get a single machine via GetMachineOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import GetMachineInput
        from orb.application.services.orchestration.get_machine import GetMachineOrchestrator

        orchestrator = self._container.get(GetMachineOrchestrator)
        result = await orchestrator.execute(GetMachineInput(machine_id=machine_id))
        if result.machine is None:
            raise NotFoundError("Machine", machine_id)
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None and hasattr(result.machine, "to_dict"):
            return scheduler.format_machine_details_response(result.machine.to_dict())
        if hasattr(result.machine, "to_dict"):
            return result.machine.to_dict()
        return {}

    async def list_templates(self, **kwargs) -> dict:
        """List templates via ListTemplatesOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import ListTemplatesInput
        from orb.application.services.orchestration.list_templates import (
            ListTemplatesOrchestrator,
        )

        orchestrator = self._container.get(ListTemplatesOrchestrator)
        result = await orchestrator.execute(
            ListTemplatesInput(
                active_only=kwargs.get("active_only", True),
                provider_name=kwargs.get("provider_name"),
                limit=kwargs.get("limit", 50),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_templates_response(result.templates)
        return {
            "templates": [
                t.model_dump()
                if hasattr(t, "model_dump")
                else t.to_dict()
                if hasattr(t, "to_dict")
                else t
                for t in result.templates
            ]
        }

    async def get_template(self, template_id: str, **kwargs) -> dict:
        """Get a single template via GetTemplateOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import GetTemplateInput
        from orb.application.services.orchestration.get_template import GetTemplateOrchestrator

        orchestrator = self._container.get(GetTemplateOrchestrator)
        result = await orchestrator.execute(GetTemplateInput(template_id=template_id))
        if result.template is None:
            raise NotFoundError("Template", template_id)
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_template_for_display(result.template)
        if hasattr(result.template, "to_dict"):
            return result.template.to_dict()
        return {}

    async def create_template(
        self, template_id: str, provider_api: str, image_id: str, **kwargs
    ) -> dict:
        """Create a template via CreateTemplateOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.create_template import (
            CreateTemplateOrchestrator,
        )
        from orb.application.services.orchestration.dtos import CreateTemplateInput

        # Named kwargs extracted explicitly; everything else goes into configuration
        _explicit = {"name", "description", "instance_type", "tags", "configuration"}
        extra = {k: v for k, v in kwargs.items() if k not in _explicit}
        configuration = {**extra, **kwargs.get("configuration", {})}

        orchestrator = self._container.get(CreateTemplateOrchestrator)
        result = await orchestrator.execute(
            CreateTemplateInput(
                template_id=template_id,
                provider_api=provider_api,
                image_id=image_id,
                name=kwargs.get("name"),
                description=kwargs.get("description"),
                instance_type=kwargs.get("instance_type"),
                tags=kwargs.get("tags", {}),
                configuration=configuration,
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_template_mutation_response(result.raw)
        return result.raw

    async def update_template(self, template_id: str, **kwargs) -> dict:
        """Update a template via UpdateTemplateOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import UpdateTemplateInput
        from orb.application.services.orchestration.update_template import (
            UpdateTemplateOrchestrator,
        )

        # Named kwargs extracted explicitly; everything else goes into configuration
        _explicit = {"name", "description", "instance_type", "image_id", "configuration"}
        extra = {k: v for k, v in kwargs.items() if k not in _explicit}
        configuration = {**extra, **kwargs.get("configuration", {})}

        orchestrator = self._container.get(UpdateTemplateOrchestrator)
        result = await orchestrator.execute(
            UpdateTemplateInput(
                template_id=template_id,
                name=kwargs.get("name"),
                description=kwargs.get("description"),
                instance_type=kwargs.get("instance_type"),
                image_id=kwargs.get("image_id"),
                configuration=configuration,
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_template_mutation_response(result.raw)
        return result.raw

    async def delete_template(self, template_id: str, **kwargs) -> dict:
        """Delete a template via DeleteTemplateOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.delete_template import (
            DeleteTemplateOrchestrator,
        )
        from orb.application.services.orchestration.dtos import DeleteTemplateInput
        from orb.domain.base.exceptions import EntityNotFoundError

        orchestrator = self._container.get(DeleteTemplateOrchestrator)
        try:
            result = await orchestrator.execute(DeleteTemplateInput(template_id=template_id))
        except EntityNotFoundError:
            raise NotFoundError("Template", template_id)
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_template_mutation_response(result.raw)
        return result.raw

    async def validate_template(self, **kwargs) -> dict:
        """Validate a template via ValidateTemplateOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import ValidateTemplateInput
        from orb.application.services.orchestration.validate_template import (
            ValidateTemplateOrchestrator,
        )

        orchestrator = self._container.get(ValidateTemplateOrchestrator)
        result = await orchestrator.execute(
            ValidateTemplateInput(
                template_id=kwargs.get("template_id"),
                config=kwargs.get("config"),
            )
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_template_mutation_response(result.raw)
        return result.raw

    async def refresh_templates(self, **kwargs) -> dict:
        """Refresh templates via RefreshTemplatesOrchestrator with scheduler formatting."""
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")
        assert self._container is not None

        from orb.application.ports.scheduler_port import SchedulerPort
        from orb.application.services.orchestration.dtos import RefreshTemplatesInput
        from orb.application.services.orchestration.refresh_templates import (
            RefreshTemplatesOrchestrator,
        )

        orchestrator = self._container.get(RefreshTemplatesOrchestrator)
        result = await orchestrator.execute(
            RefreshTemplatesInput(provider_name=kwargs.get("provider_name"))
        )
        scheduler = self._container.get_optional(SchedulerPort)
        if scheduler is not None:
            return scheduler.format_templates_response(result.templates)
        return {"templates": result.templates}

    async def show_template(self, template_id: str) -> Any:
        """Show template details (CLI-style convenience method).

        Equivalent to: orb templates show <template_id>
        Maps to: get_template()
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        return await self.get_template(template_id=template_id)  # type: ignore[attr-defined]

    async def get_template_or_none(self, *, template_id: str, **kwargs: Any) -> Any:
        """Return template details, or None if the template does not exist."""
        try:
            return await self.get_template(template_id=template_id, **kwargs)  # type: ignore[attr-defined]
        except NotFoundError:
            return None

    async def health_check(self) -> Any:
        """Check provider health (CLI-style convenience method).

        Equivalent to: orb providers health
        Maps to: get_provider_health()
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        return await self.get_provider_health()  # type: ignore[attr-defined]

    # --- CQRS method stubs (overridden at runtime by SDKMethodDiscovery) ---
    # These stubs exist solely for IDE autocompletion and static type checking.
    # At runtime each is replaced by setattr() in initialize() with the real
    # async callable wired to the CQRS bus.

    async def get_configuration(self, **kwargs: Any) -> Any:
        pass

    # Request operations
    async def get_request(self, *, request_id: str, **kwargs: Any) -> Any:
        pass

    async def list_return_requests(self, **kwargs: Any) -> Any:
        pass

    async def list_active_requests(self, **kwargs: Any) -> Any:
        pass

    async def get_request_summary(self, *, request_id: str, **kwargs: Any) -> Any:
        pass

    async def create_request(self, *, template_id: str, count: int = 1, **kwargs: Any) -> Any:
        pass

    async def create_return_request(self, *, machine_ids: list[str], **kwargs: Any) -> Any:
        pass

    async def update_request_status(self, *, request_id: str, **kwargs: Any) -> Any:
        pass

    async def complete_request(self, *, request_id: str, **kwargs: Any) -> Any:
        pass

    async def sync_request(self, *, request_id: str, **kwargs: Any) -> Any:
        pass

    async def populate_machine_ids(self, *, request_id: str, **kwargs: Any) -> Any:
        pass

    # Machine operations
    async def get_active_machine_count(self, **kwargs: Any) -> Any:
        pass

    async def get_machine_health(self, **kwargs: Any) -> Any:
        pass

    async def update_machine_status(self, *, machine_id: str, **kwargs: Any) -> Any:
        pass

    async def convert_machine_status(self, **kwargs: Any) -> Any:
        pass

    async def convert_batch_machine_status(self, **kwargs: Any) -> Any:
        pass

    async def cleanup_machine_resources(self, **kwargs: Any) -> Any:
        pass

    async def register_machine(self, **kwargs: Any) -> Any:
        pass

    async def deregister_machine(self, *, machine_id: str, **kwargs: Any) -> Any:
        pass

    # Provider operations
    async def get_provider_health(self, **kwargs: Any) -> Any:
        pass

    async def list_available_providers(self, **kwargs: Any) -> Any:
        pass

    async def get_provider_capabilities(self, **kwargs: Any) -> Any:
        pass

    async def get_provider_metrics(self, **kwargs: Any) -> Any:
        pass

    async def get_provider_strategy_config(self, **kwargs: Any) -> Any:
        pass

    async def execute_provider_operation(self, **kwargs: Any) -> Any:
        pass

    async def register_provider_strategy(self, **kwargs: Any) -> Any:
        pass

    async def update_provider_health(self, **kwargs: Any) -> Any:
        pass

    # Bulk operations
    async def get_multiple_requests(self, *, request_ids: list[str], **kwargs: Any) -> Any:
        pass

    async def get_multiple_templates(self, *, template_ids: list[str], **kwargs: Any) -> Any:
        pass

    async def get_multiple_machines(self, *, machine_ids: list[str], **kwargs: Any) -> Any:
        pass

    # Cleanup operations
    async def list_cleanable_requests(self, **kwargs: Any) -> Any:
        pass

    async def list_cleanable_resources(self, **kwargs: Any) -> Any:
        pass

    async def cleanup_old_requests(self, **kwargs: Any) -> Any:
        pass

    async def cleanup_all_resources(self, **kwargs: Any) -> Any:
        pass

    # Storage operations
    async def list_storage_strategies(self, **kwargs: Any) -> Any:
        pass

    async def get_storage_health(self, **kwargs: Any) -> Any:
        pass

    async def get_storage_metrics(self, **kwargs: Any) -> Any:
        pass

    # Scheduler operations
    async def list_scheduler_strategies(self, **kwargs: Any) -> Any:
        pass

    async def get_scheduler_configuration(self, **kwargs: Any) -> Any:
        pass

    async def validate_scheduler_configuration(self, **kwargs: Any) -> Any:
        pass

    # System / config operations
    async def get_configuration_section(self, *, section: str, **kwargs: Any) -> Any:
        pass

    async def get_provider_config(self, **kwargs: Any) -> Any:
        pass

    async def validate_provider_config(self, **kwargs: Any) -> Any:
        pass

    async def get_system_status(self, **kwargs: Any) -> Any:
        pass

    async def validate_storage(self, **kwargs: Any) -> Any:
        pass

    async def validate_mcp(self, **kwargs: Any) -> Any:
        pass

    async def validate_provider_state(self, **kwargs: Any) -> Any:
        pass

    async def reload_provider_config(self, **kwargs: Any) -> Any:
        pass

    async def set_configuration(self, **kwargs: Any) -> Any:
        pass

    async def wait_for_request(
        self,
        request_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 10.0,
    ) -> dict[str, Any]:
        """Poll until the request reaches a terminal status or timeout expires.

        Raises:
            SDKError: If the SDK is not initialized.
            TimeoutError: If timeout expires before terminal status.
        """
        if not self._initialized:
            raise SDKError("SDK not initialized. Use as async context manager.")

        from orb.domain.request.request_types import RequestStatus

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            status_result = await self.get_request_status(request_ids=[request_id], detailed=False)
            requests = status_result.get("requests", []) if isinstance(status_result, dict) else []
            result = requests[0] if requests else {}
            status_str = (
                result.get("status", "")
                if isinstance(result, dict)
                else getattr(result, "status", "")
            )
            try:
                if RequestStatus(status_str).is_terminal():
                    return result  # type: ignore[return-value]
            except ValueError:
                # Invalid or unknown status string; treat as non-terminal and continue polling.
                pass

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RequestTimeoutError(request_id=request_id, timeout=timeout)
            await asyncio.sleep(min(poll_interval, remaining))

    async def wait_for_return(
        self,
        return_request_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 10.0,
    ) -> dict[str, Any]:
        """Poll until the return request reaches a terminal status or timeout expires."""
        return await self.wait_for_request(
            return_request_id,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    def __repr__(self) -> str:
        """Return string representation of SDK instance."""
        status = "initialized" if self._initialized else "not initialized"
        method_count = len(self._methods) if self._initialized else 0
        return f"ORBClient(provider='{self._config.provider}', status='{status}', methods={method_count})"


# Backward-compatible aliases
OpenResourceBroker = ORBClient
ORB = ORBClient
