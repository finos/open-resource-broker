"""
OpenHFPlugin SDK main client implementation.

Provides a clean, async-first API that leverages the existing
application service and CQRS infrastructure with automatic
handler discovery for zero code duplication.
"""

from contextlib import suppress
from typing import Any, Callable, Dict, List, Optional

from bootstrap import Application

from .config import SDKConfig
from .discovery import MethodInfo, SDKMethodDiscovery
from .exceptions import ConfigurationError, ProviderError, SDKError


class OpenHFPluginSDK:
    """
    Main SDK interface for Host Factory operations.

    Provides automatic method discovery from existing CQRS handlers,
    ensuring zero code duplication while maintaining clean architecture
    principles and full integration with the existing system.

    Usage:
        async with OpenHFPluginSDK(provider="aws") as sdk:
            templates = await sdk.list_templates(active_only=True)
            request = await sdk.create_request(template_id="basic", machine_count=5)
            status = await sdk.get_request_status(request_id=request.id)
    """

    def __init__(
        self,
        provider: str = "aws",
        config: Optional[Dict[str, Any]] = None,
        config_path: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the Host Factory SDK.

        Args:
            provider: Cloud provider type (aws, mock, etc.)
            config: Configuration dictionary
            config_path: Path to configuration file
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
        if provider != "aws":  # Only override if explicitly set
            self._config.provider = provider
        if config_path:
            self._config.config_path = config_path

        # Add any additional kwargs to custom config
        if kwargs:
            self._config.custom_config.update(kwargs)

        # Validate configuration
        self._config.validate()

        # Internal components (lazy initialization)
        self._app: Optional[Application] = None
        self._query_bus = None
        self._command_bus = None
        self._discovery: Optional[SDKMethodDiscovery] = None
        self._methods: Dict[str, Callable] = {}
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
            # Initialize application with configuration
            self._app = Application(config_path=self._config.config_path)

            if not await self._app.initialize():
                raise ProviderError(
                    f"Failed to initialize {self._config.provider} provider",
                    provider=self._config.provider,
                )

            # Get CQRS buses directly from the initialized application
            self._query_bus = self._app.get_query_bus()
            self._command_bus = self._app.get_command_bus()

            if not self._query_bus or not self._command_bus:
                raise ConfigurationError("CQRS buses not available") from e

            # Initialize method discovery
            self._discovery = SDKMethodDiscovery()

            # Auto-discover all handler methods using CQRS buses
            self._methods = await self._discovery.discover_cqrs_methods(
                self._query_bus, self._command_bus
            )

            # Dynamically add methods to SDK instance
            for method_name, method_func in self._methods.items():
                setattr(self, method_name, method_func)

            self._initialized = True
            return True

        except Exception as e:
            if isinstance(e, (SDKError, ConfigurationError, ProviderError)):
                raise
            raise SDKError(f"SDK initialization failed: {str(e)}") from e

    async def cleanup(self) -> None:
        """Clean up resources and connections."""

        with suppress(Exception):
            if self._app and hasattr(self._app, "cleanup"):
                await self._app.cleanup()

        # Always clean up state
        self._initialized = False
        self._methods.clear()

        # Remove dynamically added methods
        if self._discovery:
            for method_name in self._discovery.list_available_methods():
                if hasattr(self, method_name):
                    delattr(self, method_name)

    # Context manager support
    async def __aenter__(self) -> "OpenHFPluginSDK":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit with cleanup."""
        await self.cleanup()

    # SDK introspection methods
    def list_available_methods(self) -> List[str]:
        """
        List all available SDK methods discovered from handlers.

        Returns:
            List of method names available on this SDK instance
        """
        if not self._initialized:
            raise SDKError(
                "SDK not initialized. Call initialize() or use as async context manager."
            )

        return list(self._methods.keys())

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

    def get_methods_by_type(self, handler_type: str) -> List[str]:
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

    def get_stats(self) -> Dict[str, Any]:
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

    def __repr__(self) -> str:
        """Return string representation of SDK instance."""
        status = "initialized" if self._initialized else "not initialized"
        method_count = len(self._methods) if self._initialized else 0
        return f"OpenHFPluginSDK(provider='{self._config.provider}', status='{status}', methods={method_count})"
