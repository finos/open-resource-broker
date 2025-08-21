"""Configuration port for domain layer."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class ConfigurationPort(ABC):
    """Port for configuration operations in domain layer."""

    @abstractmethod
    def get_naming_config(self) -> Dict[str, Any]:
        """Get naming configuration."""

    @abstractmethod
    def get_request_config(self) -> Dict[str, Any]:
        """Get request configuration."""

    @abstractmethod
    def get_template_config(self) -> Dict[str, Any]:
        """Get template configuration."""

    @abstractmethod
    def get_provider_config(self) -> Dict[str, Any]:
        """Get provider configuration."""

    @abstractmethod
    def get_storage_config(self) -> Dict[str, Any]:
        """Get storage configuration."""

    @abstractmethod
    def get_package_info(self) -> Dict[str, Any]:
        """Get package metadata information."""

    @abstractmethod
    def get_events_config(self) -> Dict[str, Any]:
        """Get events configuration."""

    @abstractmethod
    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration."""

    @abstractmethod
    def get_native_spec_config(self) -> Dict[str, Any]:
        """Get native spec configuration."""
