"""Core application settings with automatic environment variable loading."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreAppSettings(BaseSettings):
    """Core application settings with automatic env var loading."""
    model_config = SettingsConfigDict(
        env_prefix='ORB_',
        case_sensitive=False,
        env_nested_delimiter='__'
    )
    
    # Core fields - automatically map to ORB_LOG_LEVEL, ORB_DEBUG, etc.
    log_level: str = "INFO"
    debug: bool = False
    environment: str = "development"
    request_timeout: int = 300
    max_machines_per_request: int = 100