"""AWS Handler Registry Service - Manages AWS handlers and their configurations."""

from typing import TYPE_CHECKING, Any, Optional

from domain.base.ports import LoggingPort

if TYPE_CHECKING:
    from providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
    from providers.aws.infrastructure.handlers.base_handler import AWSHandler


class AWSHandlerRegistry:
    """Service for managing AWS handlers and their configurations."""

    def __init__(
        self,
        handler_factory: "AWSHandlerFactory",
        provider_instance_config: Optional[Any],
        logger: LoggingPort,
    ):
        self._handler_factory = handler_factory
        self._provider_instance_config = provider_instance_config
        self._logger = logger
        self._handler_cache = {}

    def get_handler(self, handler_type: str) -> Optional["AWSHandler"]:
        """Get handler instance for the specified type."""
        if not self._handler_factory:
            self._logger.warning("No handler factory available")
            return None

        # Check if handler is enabled in configuration
        effective_configs = self.get_effective_handler_configs()
        if handler_type not in effective_configs:
            self._logger.warning("Handler %s not available in configuration", handler_type)
            return None

        # Use cached handler if available
        if handler_type in self._handler_cache:
            return self._handler_cache[handler_type]

        try:
            handler = self._handler_factory.create_handler(handler_type)
            self._handler_cache[handler_type] = handler
            return handler
        except Exception as e:
            self._logger.error("Failed to create handler %s: %s", handler_type, e)
            return None

    def get_available_handlers(self) -> dict[str, Any]:
        """Get all available handler instances."""
        effective_configs = self.get_effective_handler_configs()
        handlers = {}
        
        for handler_type in effective_configs:
            handler = self.get_handler(handler_type)
            if handler:
                handlers[handler_type] = handler
        
        return handlers

    def get_effective_handler_configs(self) -> dict[str, Any]:
        """Get effective handler configurations from provider instance config."""
        if self._provider_instance_config and hasattr(self._provider_instance_config, 'get_effective_handlers'):
            try:
                result = self._provider_instance_config.get_effective_handlers(None)
                if isinstance(result, dict):
                    return result
                else:
                    self._logger.warning("get_effective_handlers returned %s instead of dict", type(result))
            except Exception as e:
                self._logger.warning("Failed to get effective handlers from config: %s", e)
        
        # Fallback: all available handlers
        from providers.aws.domain.template.value_objects import ProviderApi
        return {api.value: {} for api in ProviderApi}

    def get_supported_apis(self) -> list[str]:
        """Get list of supported API types."""
        return list(self.get_effective_handler_configs().keys())

    def clear_cache(self) -> None:
        """Clear the handler cache."""
        self._handler_cache.clear()