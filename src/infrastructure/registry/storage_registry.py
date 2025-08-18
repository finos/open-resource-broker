"""Storage Registry - Registry pattern for storage strategy factories."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from domain.base.exceptions import ConfigurationError

from .base_registry import BaseRegistration, BaseRegistry, RegistryMode


class UnsupportedStorageError(Exception):
    """Exception raised when an unsupported storage type is requested."""


class StorageFactoryInterface(ABC):
    """Interface for storage factory functions."""

    @abstractmethod
    def create_strategy(self, config: Any) -> Any:
        """Create a storage strategy."""

    @abstractmethod
    def create_config(self, data: Dict[str, Any]) -> Any:
        """Create a storage configuration."""


class StorageRegistration(BaseRegistration):
    """Storage-specific registration with unit_of_work_factory."""

    def __init__(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        unit_of_work_factory: Optional[Callable] = None,
    ):
        """Initialize the instance."""
        super().__init__(
            type_name,
            strategy_factory,
            config_factory,
            unit_of_work_factory=unit_of_work_factory,
        )
        self.unit_of_work_factory = unit_of_work_factory


class StorageRegistry(BaseRegistry):
    """
    Registry for storage strategy factories.

    Uses SINGLE_CHOICE mode - only one storage strategy at a time.
    Thread-safe singleton implementation using integrated BaseRegistry.
    """

    def __init__(self):
        # Storage is SINGLE_CHOICE - only one storage strategy at a time
        super().__init__(mode=RegistryMode.SINGLE_CHOICE)

    def register(
        self,
        storage_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        unit_of_work_factory: Optional[Callable] = None,
    ):
        """Register storage strategy factory - implements abstract method."""
        self.register_type(
            storage_type,
            strategy_factory,
            config_factory,
            unit_of_work_factory=unit_of_work_factory,
        )

    def register_storage(
        self,
        storage_type: str,
        strategy_factory: Callable,
        config_factory: Callable,
        unit_of_work_factory: Optional[Callable] = None,
    ) -> None:
        """
        Register a storage type with its factories - backward compatibility method.

        Args:
            storage_type: Type identifier for the storage (e.g., 'json', 'sql', 'dynamodb')
            strategy_factory: Factory function to create storage strategy
            config_factory: Factory function to create storage configuration
            unit_of_work_factory: Optional factory function to create unit of work

        Raises:
            ConfigurationError: If storage type is already registered
        """
        try:
            self.register(
                storage_type,
                strategy_factory,
                config_factory,
                unit_of_work_factory=unit_of_work_factory,
            )
        except ValueError as e:
            raise ConfigurationError(str(e)) from e

    def create_strategy(self, storage_type: str, config: Any) -> Any:
        """
        Create a storage strategy for the given type and configuration - implements abstract method.

        Args:
            storage_type: Type of storage to create
            config: Configuration for the storage strategy

        Returns:
            Storage strategy instance

        Raises:
            UnsupportedStorageError: If storage type is not registered
        """
        try:
            return self.create_strategy_by_type(storage_type, config)
        except ValueError as e:
            raise UnsupportedStorageError(str(e)) from e

    def create_config(self, storage_type: str, data: Dict[str, Any]) -> Any:
        """
        Create a storage configuration for the given type and data.

        Args:
            storage_type: Type of storage
            data: Configuration data dictionary

        Returns:
            Storage configuration instance

        Raises:
            UnsupportedStorageError: If storage type is not registered
        """
        try:
            registration = self._get_type_registration(storage_type)
            config = registration.config_factory(data)
            self.logger.debug("Created config for storage type: %s", storage_type)
            return config
        except ValueError as e:
            raise UnsupportedStorageError(str(e)) from e
        except Exception as e:
            error_msg = f"Failed to create config for storage type '{storage_type}': {str(e)}"
            self.logger.error(error_msg)
            raise ConfigurationError(error_msg) from e

    def create_unit_of_work(self, storage_type: str) -> Optional[Any]:
        """
        Create a unit of work for the given storage type.

        Args:
            storage_type: Type of storage

        Returns:
            Unit of work instance or None if not available
        """
        return self.create_additional_component(storage_type, "unit_of_work_factory")

    def get_registered_storage_types(self) -> List[str]:
        """Get list of registered storage types - backward compatibility method."""
        return self.get_registered_types()

    def is_storage_registered(self, storage_type: str) -> bool:
        """Check if storage type is registered - backward compatibility method."""
        return self.is_registered(storage_type)

    def ensure_type_registered(self, storage_type: str) -> None:
        """Ensure storage type is registered, register if not."""
        if not self.is_registered(storage_type):
            self._register_type_dynamically(storage_type)

    def _register_type_dynamically(self, storage_type: str) -> None:
        """Dynamically register storage type based on configuration."""
        try:
            if storage_type == "json":
                from infrastructure.persistence.json.registration import (
                    register_json_storage,
                )

                register_json_storage()
            elif storage_type == "sql":
                from infrastructure.persistence.sql.registration import (
                    register_sql_storage,
                )

                register_sql_storage()
            elif storage_type == "dynamodb":
                from providers.aws.persistence.dynamodb.registration import (
                    register_dynamodb_storage,
                )

                register_dynamodb_storage()
            else:
                raise ValueError(f"Unknown storage type: {storage_type}")
        except ImportError as e:
            from domain.base.exceptions import ConfigurationError

            raise ConfigurationError(f"Storage type '{storage_type}' not available: {e}") from e

    def _create_registration(
        self,
        type_name: str,
        strategy_factory: Callable,
        config_factory: Callable,
        **additional_factories,
    ) -> BaseRegistration:
        """Create storage-specific registration."""
        return StorageRegistration(
            type_name,
            strategy_factory,
            config_factory,
            additional_factories.get("unit_of_work_factory"),
        )


def get_storage_registry() -> StorageRegistry:
    """Get the singleton storage registry instance."""
    return StorageRegistry()


def reset_storage_registry() -> None:
    """Reset the storage registry for testing purposes."""
    # Since StorageRegistry inherits from BaseRegistry, we can reset it
    registry = get_storage_registry()
    registry._type_registrations.clear()
    registry._instance_registrations.clear()
