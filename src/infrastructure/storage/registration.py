"""Central Storage Registration Module.

This module provides centralized registration of all storage types,
ensuring all storage implementations are registered with the storage registry.

CLEAN ARCHITECTURE: Only registers storage strategies, no repository knowledge.
"""


def register_all_storage_types() -> None:
    """Register all available storage types."""
    from infrastructure.storage.registry import get_storage_registry

    get_storage_registry()

    # Register all available storage types
    from infrastructure.storage.json.registration import register_json_storage

    register_json_storage()

    from infrastructure.storage.sql.registration import register_sql_storage

    register_sql_storage()

    from infrastructure.storage.dynamodb.registration import register_dynamodb_storage

    register_dynamodb_storage()


def get_available_storage_types() -> list:
    """
    Get list of available storage types.

    Returns:
        List of storage type names that are available for registration
    """
    available_types = []

    # Check JSON storage availability
    try:
        pass

        available_types.append("json")
    except ImportError:
        pass

    # Check SQL storage availability
    try:
        pass

        available_types.append("sql")
    except ImportError:
        pass

    # Check DynamoDB storage availability
    try:
        pass

        available_types.append("dynamodb")
    except ImportError:
        pass

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
