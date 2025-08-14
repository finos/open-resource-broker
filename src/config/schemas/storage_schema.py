"""Storage configuration schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class JsonStrategyConfig(BaseModel):
    """JSON storage strategy configuration."""

    storage_type: str = Field(
        "single_file", description="Storage type (single_file, split_files)")
    base_path: str = Field("data", description="Base path for JSON files")
    filenames: Dict[str, Any] = Field(
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
    """SQL storage strategy configuration."""

    type: str = Field(
        "sqlite", description="SQL database type (sqlite, postgresql, mysql, aurora)")
    host: str = Field("", description="Database host")
    port: int = Field(0, description="Database port")
    name: str = Field("database.db", description="Database name")
    username: Optional[str] = Field(None, description="Database username")
    password: Optional[str] = Field(None, description="Database password")
    pool_size: int = Field(5, description="Connection pool size")
    max_overflow: int = Field(10, description="Maximum connection overflow")
    timeout: int = Field(30, description="Connection timeout in seconds")
    ssl_ca: Optional[str] = Field(
        None, description="SSL CA certificate path (for Aurora)")
    ssl_verify: bool = Field(True, description="Verify SSL certificate (for Aurora)")
    cluster_endpoint: Optional[str] = Field(None, description="Aurora cluster endpoint")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate database type."""
        valid_types = ["sqlite", "postgresql", "mysql", "aurora"]
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
        elif db_type == "aurora":
            if not self.cluster_endpoint and not host:
                raise ValueError(
                    "Either cluster_endpoint or host is required for Aurora")
            if not port:
                raise ValueError("Port is required for Aurora")
            if not name:
                raise ValueError("Database name is required for Aurora")

        return self


class DynamodbStrategyConfig(BaseModel):
    """DynamoDB storage strategy configuration."""

    region: str = Field("us-east-1", description="AWS region")
    profile: str = Field("default", description="AWS profile")
    table_prefix: str = Field("hostfactory", description="Table prefix")


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

    # Service-specific settings
    service_configs: Dict[str, Dict[str, Any]] = Field(
        default_factory=lambda: {
            "ec2": {"max_attempts": 3, "base_delay": 1.0, "max_delay": 30.0},
            "dynamodb": {"max_attempts": 5, "base_delay": 0.5, "max_delay": 20.0},
            "s3": {"max_attempts": 4, "base_delay": 0.5, "max_delay": 15.0},
        },
        description="Service-specific retry configurations",
    )

    @field_validator("max_attempts")
    @classmethod
    def validate_max_attempts(cls, v: int) -> int:
        """Validate max attempts."""
        if v < 0:
            raise ValueError("Max attempts must be non-negative")
        return v


class StorageConfig(BaseModel):
    """Storage configuration."""

    strategy: str = Field("json", description="Storage strategy (json, sql, dynamodb)")
    default_storage_path: str = Field(
        "data", description="Default path for local storage strategies"
    )
    json_strategy: JsonStrategyConfig = Field(
        default_factory=lambda: JsonStrategyConfig())
    sql_strategy: SqlStrategyConfig = Field(default_factory=lambda: SqlStrategyConfig())
    dynamodb_strategy: DynamodbStrategyConfig = Field(
        default_factory=lambda: DynamodbStrategyConfig()
    )

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        """Validate storage strategy."""
        valid_strategies = ["json", "sql", "dynamodb"]
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
        elif strategy == "dynamodb":
            dynamodb_strategy = self.dynamodb_strategy
            if not dynamodb_strategy.region:
                raise ValueError("DynamoDB strategy region is required")

        return self
