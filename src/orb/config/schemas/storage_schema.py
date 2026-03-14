"""Storage configuration schemas."""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class JsonStrategyConfig(BaseModel):
    """JSON storage strategy configuration."""

    storage_type: str = Field("single_file", description="Storage type (single_file, split_files)")
    base_path: str = Field("data", description="Base path for JSON files")
    filenames: dict[str, Any] = Field(
        default_factory=lambda: {
            "single_file": "request_database.json",
            "split_files": {
                "requests": "requests.json",
                "templates": "templates.json",
                "machines": "machines.json",
            },
        },
        description="Filenames for JSON storage",
    )
    backup_enabled: bool = Field(True, description="Enable automatic backups")
    backup_count: int = Field(5, description="Number of backup files to keep")
    pretty_print: bool = Field(True, description="Pretty print JSON files")

    @field_validator("storage_type")
    @classmethod
    def validate_storage_type(cls, v: str) -> str:
        """Validate storage type."""
        valid_types = ["single_file", "split_files"]
        if v not in valid_types:
            raise ValueError(f"Storage type must be one of {valid_types}")
        return v


class SqlStrategyConfig(BaseModel):
    """Generic SQL storage strategy configuration (sqlite, postgresql, mysql)."""

    type: str = Field("sqlite", description="SQL database type (sqlite, postgresql, mysql)")
    host: str = Field("", description="Database host")
    port: int = Field(0, description="Database port")
    name: str = Field("database.db", description="Database name")
    username: Optional[str] = Field(None, description="Database username")
    password: Optional[str] = Field(None, description="Database password")
    pool_size: int = Field(5, description="Connection pool size")
    max_overflow: int = Field(10, description="Maximum connection overflow")
    timeout: int = Field(30, description="Connection timeout in seconds")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate database type."""
        valid_types = ["sqlite", "postgresql", "mysql"]
        if v not in valid_types:
            raise ValueError(f"Database type must be one of {valid_types}")
        return v

    @model_validator(mode="after")
    def validate_connection_info(self) -> "SqlStrategyConfig":
        """Validate connection information."""
        db_type = self.type
        host = self.host
        port = self.port
        name = self.name

        if db_type == "sqlite":
            if not name:
                raise ValueError("Database name is required for SQLite")
        elif db_type in ["postgresql", "mysql"]:
            if not host:
                raise ValueError(f"Host is required for {db_type}")
            if not port:
                raise ValueError(f"Port is required for {db_type}")
            if not name:
                raise ValueError(f"Database name is required for {db_type}")

        return self


class BackoffConfig(BaseModel):
    """Backoff strategy configuration."""

    strategy_type: str = Field(
        "exponential",
        description="Backoff strategy type (constant, exponential, linear)",
    )
    max_retries: int = Field(3, description="Maximum number of retries")
    base_delay: float = Field(1.0, description="Base delay in seconds")
    max_delay: float = Field(60.0, description="Maximum delay in seconds")
    step: float = Field(1.0, description="Step size for linear backoff in seconds")
    jitter: float = Field(0.1, description="Jitter factor (0.0 to 1.0)")

    @field_validator("strategy_type")
    @classmethod
    def validate_strategy_type(cls, v: str) -> str:
        """Validate strategy type."""
        valid_types = ["constant", "exponential", "linear"]
        if v not in valid_types:
            raise ValueError(f"Strategy type must be one of {valid_types}")
        return v


class RetryConfig(BaseModel):
    """Simplified retry configuration."""

    # Basic retry settings
    max_attempts: int = Field(3, description="Maximum retry attempts")
    base_delay: float = Field(1.0, description="Base delay in seconds")
    max_delay: float = Field(60.0, description="Maximum delay in seconds")
    jitter: bool = Field(True, description="Add jitter to delays")

    @field_validator("max_attempts")
    @classmethod
    def validate_max_attempts(cls, v: int) -> int:
        """Validate max attempts."""
        if v < 0:
            raise ValueError("Max attempts must be non-negative")
        return v


class StorageConfig(BaseModel):
    """Storage configuration."""

    strategy: str = Field("json", description="Storage strategy (json, sql)")
    json_strategy: JsonStrategyConfig = Field(default_factory=lambda: JsonStrategyConfig())  # type: ignore[call-arg]
    sql_strategy: SqlStrategyConfig = Field(
        default_factory=lambda: SqlStrategyConfig()  # type: ignore[call-arg]
    )

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        """Validate storage strategy."""
        valid_strategies = ["json", "sql"]
        if v not in valid_strategies:
            raise ValueError(f"Storage strategy must be one of {valid_strategies}")
        return v

    @model_validator(mode="after")
    def validate_strategy_config(self) -> "StorageConfig":
        """Validate strategy configuration."""
        strategy = self.strategy

        if strategy == "json":
            json_strategy = self.json_strategy
            if not json_strategy.base_path:
                raise ValueError("JSON strategy base path is required")
        elif strategy == "sql":
            sql_strategy = self.sql_strategy
            if not sql_strategy.name:
                raise ValueError("SQL strategy database name is required")
        return self
