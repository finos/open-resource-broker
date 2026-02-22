"""Infrastructure-level constants.

This module contains constants used across the infrastructure layer to avoid
magic numbers and hardcoded strings throughout the codebase.
"""

# Retry and timeout constants
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_BACKOFF_MULTIPLIER = 2
MAX_RETRY_ATTEMPTS = 10

# Cache constants
DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes
MAX_CACHE_SIZE_ITEMS = 1000
CACHE_CLEANUP_INTERVAL_SECONDS = 60

# Batch processing constants
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 1000
MIN_BATCH_SIZE = 1

# Connection pool constants
DEFAULT_POOL_SIZE = 10
MAX_POOL_SIZE = 100
MIN_POOL_SIZE = 1
POOL_TIMEOUT_SECONDS = 30

# HTTP constants
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
MAX_REQUEST_TIMEOUT_SECONDS = 300
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

# Token and authentication constants
DEFAULT_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour
TOKEN_REFRESH_BUFFER_SECONDS = 300  # 5 minutes before expiry
MAX_TOKEN_LENGTH = 4096

# Pagination constants
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 1000
MIN_PAGE_SIZE = 1

# File and storage constants
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BUFFER_SIZE_BYTES = 8192  # 8 KB
MAX_FILENAME_LENGTH = 255

# Logging constants
DEFAULT_LOG_LEVEL = "INFO"
MAX_LOG_MESSAGE_LENGTH = 10000

# Health check constants
HEALTH_CHECK_TIMEOUT_SECONDS = 5
HEALTH_CHECK_INTERVAL_SECONDS = 30

# Rate limiting constants
DEFAULT_RATE_LIMIT_PER_MINUTE = 60
DEFAULT_RATE_LIMIT_BURST = 10
