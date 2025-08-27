"""Logging configuration schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field("INFO", description="Logging level")
    format: str = Field(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Logging format",
    )
    file_path: Optional[str] = Field(None, description="Log file path")
    max_size: int = Field(10 * 1024 * 1024, description="Maximum log file size in bytes")
    backup_count: int = Field(5, description="Number of backup log files")
    console_enabled: bool = Field(True, description="Whether console logging is enabled")
    accept_propagated_setting: bool = Field(
        False, description="Whether to use HostFactory service log settings"
    )
