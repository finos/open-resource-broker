"""Domain-level constants.

This module contains constants used in the domain layer, focusing on
business rules and domain-specific values.
"""

# Request status constants
REQUEST_STATUS_PENDING = "pending"
REQUEST_STATUS_PROVISIONING = "provisioning"
REQUEST_STATUS_ACTIVE = "active"
REQUEST_STATUS_FAILED = "failed"
REQUEST_STATUS_CANCELLED = "cancelled"
REQUEST_STATUS_COMPLETED = "completed"

# Machine status constants
MACHINE_STATUS_PENDING = "pending"
MACHINE_STATUS_RUNNING = "running"
MACHINE_STATUS_STOPPED = "stopped"
MACHINE_STATUS_TERMINATED = "terminated"
MACHINE_STATUS_FAILED = "failed"

# Price type constants
PRICE_TYPE_ON_DEMAND = "ondemand"
PRICE_TYPE_SPOT = "spot"
PRICE_TYPE_RESERVED = "reserved"

# Provider type constants
PROVIDER_TYPE_AWS = "aws"
PROVIDER_TYPE_PROVIDER1 = "provider1"
PROVIDER_TYPE_PROVIDER2 = "provider2"

# Template validation constants
MIN_TEMPLATE_NAME_LENGTH = 1
MAX_TEMPLATE_NAME_LENGTH = 255
MIN_INSTANCE_COUNT = 1
MAX_INSTANCE_COUNT = 1000

# Resource naming constants
MAX_RESOURCE_NAME_LENGTH = 255
MIN_RESOURCE_NAME_LENGTH = 1
RESOURCE_NAME_ALLOWED_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"

# Timeout constants (in seconds)
MAX_REQUEST_TIMEOUT_SECONDS = 86400  # 1 day maximum
DEFAULT_REQUEST_TIMEOUT_SECONDS = 3600  # 1 hour default
MIN_REQUEST_TIMEOUT_SECONDS = 1  # Minimum 1 second
FALLBACK_REQUEST_TIMEOUT_SECONDS = 300  # 5 minutes fallback

# Default values
DEFAULT_INSTANCE_COUNT = 1
DEFAULT_PRICE_TYPE = PRICE_TYPE_ON_DEMAND
