"""AWS storage provider configuration schemas."""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from orb.config.schemas.storage_schema import SqlStrategyConfig


class AuroraSqlStrategyConfig(SqlStrategyConfig):
    """Aurora-specific SQL storage strategy configuration.

    Extends SqlStrategyConfig with Aurora-specific fields:
    cluster_endpoint, ssl_ca, ssl_verify.
    """

    cluster_endpoint: Optional[str] = Field(None, description="Aurora cluster endpoint")
    ssl_ca: Optional[str] = Field(None, description="SSL CA certificate path")
    ssl_verify: bool = Field(True, description="Verify SSL certificate")

    @model_validator(mode="after")
    def validate_aurora_connection_info(self) -> "AuroraSqlStrategyConfig":
        """Validate Aurora-specific connection information."""
        if not self.cluster_endpoint and not self.host:
            raise ValueError("Either cluster_endpoint or host is required for Aurora")
        if not self.port:
            raise ValueError("Port is required for Aurora")
        if not self.name:
            raise ValueError("Database name is required for Aurora")
        return self


class DynamodbStrategyConfig(BaseModel):
    """DynamoDB storage strategy configuration."""

    region: Optional[str] = Field(None, description="AWS region")
    profile: str = Field("default", description="AWS profile")
    table_prefix: str = Field("hostfactory", description="Table prefix")


class AWSStorageConfig(BaseModel):
    """AWS-specific storage backend configuration.

    Holds optional sub-configs for each AWS storage backend.
    Only the backend matching the top-level storage.strategy is used at runtime.
    """

    dynamodb: Optional[DynamodbStrategyConfig] = Field(
        None, description="DynamoDB storage backend configuration"
    )
    aurora: Optional[AuroraSqlStrategyConfig] = Field(
        None, description="Aurora SQL storage backend configuration"
    )
