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

    from orb.providers.aws.storage.registration import (
        register_aurora_storage,
        register_dynamodb_storage,
    )

    register_dynamodb_storage()
    register_aurora_storage()


def get_available_storage_types() -> list:
    """
    Get list of available storage types.

    Returns:
        List of storage type names that are available for registration
    """
    from importlib.util import find_spec

    available_types = []

    # Each backend is available when its registration module can be imported.
    # find_spec probes importability without binding an unused name.
    if find_spec("orb.infrastructure.storage.json.registration") is not None:
        available_types.append("json")
    if find_spec("orb.infrastructure.storage.sql.registration") is not None:
        available_types.append("sql")
    if find_spec("orb.providers.aws.storage.registration") is not None:
        available_types.append("dynamodb")
        available_types.append("aurora")

    return available_types


def is_storage_type_available(storage_type: str) -> bool:
    """
    Check if a storage type is available for registration.

    Args:
        storage_type: Name of the storage type to check

    Returns:
        True if storage type is available, False otherwise
    """
    return storage_type in get_available_storage_types()
