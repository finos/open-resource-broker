"""Metrics configuration schema."""

from pydantic import BaseModel, Field


class AwsMetricsConfig(BaseModel):
    """AWS-specific metrics configuration."""

    aws_metrics_enabled: bool = Field(False, description="Enable AWS metrics collection")
    sample_rate: float = Field(1.0, description="Sampling rate for metrics (0.0-1.0)")
    monitored_services: list[str] = Field(
        default_factory=list, description="List of AWS services to monitor"
    )
    monitored_operations: list[str] = Field(
        default_factory=list, description="List of AWS operations to monitor"
    )
    track_payload_sizes: bool = Field(False, description="Track request/response payload sizes")


class MetricsConfig(BaseModel):
    """Metrics configuration."""

    metrics_enabled: bool = Field(False, description="Enable metrics collection")
    metrics_dir: str = Field("./metrics", description="Directory for metrics files")
    metrics_interval: int = Field(60, description="Metrics collection interval in seconds")
    trace_enabled: bool = Field(False, description="Enable request tracing")
    trace_buffer_size: int = Field(1000, description="Trace buffer size")
    trace_file_max_size_mb: int = Field(10, description="Maximum trace file size in MB")
    aws_metrics: AwsMetricsConfig = Field(
        default_factory=AwsMetricsConfig, description="AWS-specific metrics configuration"
    )
