"""Metrics configuration schema."""

from pydantic import BaseModel, ConfigDict, Field


# NOTE: ProviderMetricsConfig is AWS-specific and ideally belongs in
# providers/aws/configuration/. Moving it there is blocked by eager imports in
# orb/providers/aws/__init__.py (AWSProviderConfig, AWSProviderStrategy, registration)
# which pull in orb.config.schemas before it is fully initialized, causing a circular
# import. Known debt: resolve once orb/providers/aws/__init__.py is made lazy.
class ProviderMetricsConfig(BaseModel):
    """Provider-specific metrics configuration."""

    aws_metrics_enabled: bool = Field(False, description="Enable provider metrics collection")
    sample_rate: float = Field(1.0, description="Sampling rate for metrics (0.0-1.0)")
    monitored_services: list[str] = Field(
        default_factory=list, description="List of services to monitor"
    )
    monitored_operations: list[str] = Field(
        default_factory=list, description="List of operations to monitor"
    )
    track_payload_sizes: bool = Field(False, description="Track request/response payload sizes")


class MetricsConfig(BaseModel):
    """Metrics configuration."""

    model_config = ConfigDict(populate_by_name=True)

    metrics_enabled: bool = Field(False, description="Enable metrics collection")
    metrics_dir: str = Field("./metrics", description="Directory for metrics files")
    metrics_interval: int = Field(60, description="Metrics collection interval in seconds")
    trace_enabled: bool = Field(False, description="Enable request tracing")
    trace_buffer_size: int = Field(1000, description="Trace buffer size")
    trace_file_max_size_mb: int = Field(10, description="Maximum trace file size in MB")
    provider_metrics: ProviderMetricsConfig = Field(
        default_factory=ProviderMetricsConfig,  # type: ignore[arg-type]
        alias="aws_metrics",
        description="Provider-specific metrics configuration",
    )
