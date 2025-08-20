"""Base provider strategy implementing ProviderPort."""

from abc import ABC
from typing import Any, Dict

from domain.base.ports.provider_port import ProviderPort


class BaseProviderStrategy(ProviderPort, ABC):
    """Base class for all provider strategies implementing ProviderPort."""

    def __init__(self, config: Dict[str, Any], logger: Any) -> None:
        """Initialize base provider strategy.

        Args:
            config: Provider configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger

    def get_provider_info(self) -> Dict[str, Any]:
        """Get provider information."""
        return {"type": self.__class__.__name__, "config": self.config}
