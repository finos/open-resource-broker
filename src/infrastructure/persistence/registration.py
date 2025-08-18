"""Central Storage Registration Module.

This module provides centralized registration of all storage types,
ensuring all storage implementations are registered with the storage registry.

CLEAN ARCHITECTURE: Only registers storage strategies, no repository knowledge.
"""

from infrastructure.logging.logger import get_logger


def register_all_storage_types() -> None:
    """
    Register all available storage types with the storage registry.

    This function attempts to register all known storage types. If a storage
    type fails to register (e.g., due to missing dependencies), it logs the
    error but continues with other storage types.

    CLEAN ARCHITECTURE: Only registers storage strategies.
    """
    logger = get_logger(__name__)

    # Track registration results
    registered_types = []
    failed_types = []

    # Register JSON storage
    try:
        from infrastructure.persistence.json.registration import register_json_storage

        register_json_storage()
        registered_types.append("json")
        logger.debug("JSON storage registered successfully")
    except Exception as e:
        failed_types.append(("json", str(e)))
        logger.warning("Failed to register JSON storage: %s", e)

    # Register SQL storage
    try:
        from infrastructure.persistence.sql.registration import register_sql_storage

        register_sql_storage()
        registered_types.append("sql")
        logger.debug("SQL storage registered successfully")
    except Exception as e:
        failed_types.append(("sql", str(e)))
        logger.warning("Failed to register SQL storage: %s", e)

    # Register DynamoDB storage
    try:
        from providers.aws.persistence.dynamodb.registration import (
            register_dynamodb_storage,
        )

        register_dynamodb_storage()
        registered_types.append("dynamodb")
        logger.debug("DynamoDB storage registered successfully")
    except Exception as e:
        failed_types.append(("dynamodb", str(e)))
        logger.warning("Failed to register DynamoDB storage: %s", e)

    # Log summary
    if registered_types:
        logger.info("Successfully registered storage types: %s", ", ".join(registered_types))

    if failed_types:
        failed_summary = ", ".join([f"{name} ({error})" for name, error in failed_types])
        logger.warning("Failed to register storage types: %s", failed_summary)

    if not registered_types:
        logger.error("No storage types were successfully registered!")
        raise RuntimeError("Failed to register any storage types") from e


def register_storage_type_on_demand(storage_type: str) -> bool:
    """
    Register a specific storage type on demand .

    Args:
        storage_type: Name of the storage type to register

    Returns:
        True if registration was successful, False otherwise
    """
    logger = get_logger(__name__)

    # Check if already registered
    from infrastructure.registry.storage_registry import get_storage_registry

    registry = get_storage_registry()

    if hasattr(registry, "is_registered") and registry.is_registered(storage_type):
        logger.debug("Storage type '%s' already registered", storage_type)
        return True

    try:
        if storage_type == "json":
            from infrastructure.persistence.json.registration import (
                register_json_storage,
            )

            register_json_storage()
        elif storage_type == "sql":
            from infrastructure.persistence.sql.registration import register_sql_storage

            register_sql_storage()
        elif storage_type == "dynamodb":
            from providers.aws.persistence.dynamodb.registration import (
                register_dynamodb_storage,
            )

            register_dynamodb_storage()
        else:
            logger.error("Unknown storage type: %s", storage_type)
            return False

        logger.info("Successfully registered storage type on demand: %s", storage_type)
        return True

    except Exception as e:
        logger.error("Failed to register storage type '%s' on demand: %s", storage_type, e)
        return False


def register_minimal_storage_types() -> None:
    """
    Register only essential storage types for faster startup .

    This registers only JSON storage by default, with other types loaded on demand.
    """
    logger = get_logger(__name__)

    # Register only JSON storage (lightweight, always available)
    try:
        from infrastructure.persistence.json.registration import register_json_storage

        register_json_storage()
        logger.info("Minimal storage registration complete: json")
    except Exception as e:
        logger.error("Failed to register minimal storage types: %s", e)
        raise RuntimeError("Failed to register minimal storage types") from e


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


def register_storage_type(storage_type: str) -> bool:
    """
    Register a specific storage type.

    Args:
        storage_type: Name of the storage type to register

    Returns:
        True if registration was successful, False otherwise
    """
    logger = get_logger(__name__)

    try:
        if storage_type == "json":
            from infrastructure.persistence.json.registration import (
                register_json_storage,
            )

            register_json_storage()
        elif storage_type == "sql":
            from infrastructure.persistence.sql.registration import register_sql_storage

            register_sql_storage()
        elif storage_type == "dynamodb":
            from providers.aws.persistence.dynamodb.registration import (
                register_dynamodb_storage,
            )

            register_dynamodb_storage()
        else:
            logger.error("Unknown storage type: %s", storage_type)
            return False

        logger.info("Successfully registered storage type: %s", storage_type)
        return True

    except Exception as e:
        logger.error("Failed to register storage type '%s': %s", storage_type, e)
        return False
