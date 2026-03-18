"""Configuration-level constants.

This module contains constants used for configuration management,
including environment variable names and configuration keys.
"""

# Environment variable prefixes
ENV_PREFIX_ORB = "ORB_"

# Storage backend types
STORAGE_BACKEND_JSON = "json"
STORAGE_BACKEND_DYNAMODB = "dynamodb"
STORAGE_BACKEND_SQL = "sql"
STORAGE_BACKEND_MEMORY = "memory"

# Scheduler types
SCHEDULER_TYPE_DEFAULT = "default"
SCHEDULER_TYPE_HOSTFACTORY = "hostfactory"

# Configuration file names
CONFIG_FILE_NAME = "config.yaml"
CONFIG_FILE_NAME_JSON = "config.json"
PROVIDERS_FILE_NAME = "providers.yaml"
TEMPLATES_FILE_NAME = "templates.yaml"

# Configuration directory names
CONFIG_DIR_NAME = ".orb"
CONFIG_DIR_NAME_LEGACY = ".hostfactory"  # Legacy for backward compatibility
CACHE_DIR_NAME = "cache"
LOGS_DIR_NAME = "logs"
DATA_DIR_NAME = "data"

# Default configuration values
DEFAULT_CONFIG_TIMEOUT_SECONDS = 30
DEFAULT_CONFIG_RETRY_ATTEMPTS = 3
DEFAULT_CONFIG_LOG_LEVEL = "INFO"
DEFAULT_CONFIG_CACHE_ENABLED = True
DEFAULT_CONFIG_CACHE_TTL_SECONDS = 300
