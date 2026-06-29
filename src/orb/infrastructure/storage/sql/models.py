"""
AUTHORITATIVE schema for SQL strategy. Use Alembic for migrations.

These ORM models are the single source of truth for the SQL table structure.
The column-dict helpers in unit_of_work.py have been removed in favour of
these models.  Run ``alembic upgrade head`` to apply schema changes.
"""

from sqlalchemy import BIGINT, Boolean, Integer, String, Text, text as sa_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class RequestModel(Base):
    """ORM model for the `requests` table."""

    __tablename__ = "requests"

    # Identity
    request_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Counts
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    desired_capacity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    successful_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Timing (stored as ISO-8601 TEXT for dialect portability)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_status_check: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_status_check: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status / error
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded
    success_rate: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provider
    provider_api: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_type: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded TEXT

    # Resources
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded list
    machine_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded list

    # Launch template
    launch_template_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    launch_template_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Misc
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # NOTE: 'metadata' is reserved by SQLAlchemy DeclarativeBase; the column is
    # named 'metadata' in the DB but accessed as `extra_metadata` on the model.
    extra_metadata: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)

    # Legacy / backward-compat aliases written by older serializer versions
    machine_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class MachineModel(Base):
    """ORM model for the `machines` table."""

    __tablename__ = "machines"

    # Identity
    machine_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    instance_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Network
    private_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    public_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    private_dns_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_dns_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timing (BIGINT unix epoch for launch_time; ISO-8601 TEXT for others)
    launch_time: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    termination_time: Mapped[str | None] = mapped_column(Text, nullable=True)
    provisioning_started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    uptime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provider
    provider_api: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_name: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cloud_host_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded

    # Relations
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_id: Mapped[str] = mapped_column(String(255), nullable=False)
    return_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Location
    availability_zone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    subnet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vpc_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tags / metadata
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded
    # NOTE: 'metadata' reserved by DeclarativeBase; column 'metadata' accessed as extra_metadata
    extra_metadata: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)
    health_checks: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded

    # Pricing
    price_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Misc
    result: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    security_group_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded
    image_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Timestamps (ISO-8601 TEXT)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class TemplateModel(Base):
    """ORM model for the `templates` table."""

    __tablename__ = "templates"

    # Core
    template_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("1"))

    # Provider
    provider_api: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded

    # Instance
    image_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_instances: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    instance_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Machine types (JSON-encoded dicts)
    machine_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    machine_types_ondemand: Mapped[str | None] = mapped_column(Text, nullable=True)
    machine_types_priority: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Network (JSON-encoded lists)
    subnet_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_group_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    network_zones: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_ip_assignment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Pricing / allocation
    price_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    allocation_strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    max_price: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Storage
    root_device_volume_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    iops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    throughput: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_encryption: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    encryption_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Access
    key_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    instance_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    launch_template_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Advanced
    monitoring_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Tags / metadata (JSON-encoded)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NOTE: 'metadata' reserved by DeclarativeBase; column 'metadata' accessed as extra_metadata
    extra_metadata: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)

    # Timestamps (ISO-8601 TEXT)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Misc
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
