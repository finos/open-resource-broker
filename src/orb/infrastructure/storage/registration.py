"""Central Storage Registration Module.

This module provides centralized registration of all storage types,
ensuring all storage implementations are registered with the storage registry.

CLEAN ARCHITECTURE: Only registers storage strategies, no repository knowledge.
"""


def register_all_storage_types() -> None:
    """Register all available storage types."""
    from orb.infrastructure.storage.registry import get_storage_registry

    get_storage_registry()

    # Register all available storage types
    from orb.infrastructure.storage.json.registration import register_json_storage

    register_json_storage()

    from orb.infrastructure.storage.sql.registration import register_sql_storage

    register_sql_storage()


def get_available_storage_types() -> list:
    """
    Get list of available storage types by querying the storage registry.

    Returns:
        List of storage type names that are currently registered
    """
    from orb.infrastructure.storage.registry import get_storage_registry

    return get_storage_registry().get_registered_types()


def is_storage_type_available(storage_type: str) -> bool:
    """
    Check if a storage type is available for registration.

    Args:
        storage_type: Name of the storage type to check

    Returns:
        True if storage type is registered, False otherwise
    """
    from orb.infrastructure.storage.registry import get_storage_registry

    return get_storage_registry().is_registered(storage_type)
