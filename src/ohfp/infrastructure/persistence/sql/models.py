"""SQLAlchemy models for persistence."""

import enum
import json
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class JsonSerializableMixin:
    """Mixin for JSON serializable models."""

    def to_dict(self):
        """Convert model to dictionary."""
        result = {}
        for column in self.__table__.columns:
            value = getattr(self, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, enum.Enum):
                value = value.value
            result[column.name] = value
        return result

    @classmethod
    def from_dict(cls, data):
        """Create model from dictionary."""
        # Handle enum fields
        for column in cls.__table__.columns:
            if isinstance(column.type, Enum) and column.name in data:
                enum_class = column.type.enum_class
                data[column.name] = enum_class(data[column.name])
        return cls(**data)

    def to_json(self):
        """Convert model to JSON string."""
        return json.dumps(self.to_dict())


class MachineModel(Base, JsonSerializableMixin):
    """SQLAlchemy model for machines."""

    __tablename__ = "machines"

    machine_id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False)
    instance_type = Column(String(50), nullable=False)
    private_ip = Column(String(50), nullable=True)
    public_ip = Column(String(50), nullable=True)
    result = Column(String(50), nullable=False)
    launch_time = Column(Integer, nullable=False)
    message = Column(Text, nullable=True)
    provider_api = Column(String(50), nullable=True)
    resource_id = Column(String(255), nullable=True)
    price_type = Column(String(50), nullable=True)
    cloud_host_id = Column(String(255), nullable=True)
    # Using model_metadata instead of metadata to avoid conflict with SQLAlchemy's reserved keyword
    # The domain model uses metadata, but SQLAlchemy reserves this name in the
    # Declarative API
    model_metadata = Column("model_metadata", JSON, nullable=True)
    health_checks = Column(JSON, nullable=True)
    request_id = Column(String(36), ForeignKey("requests.request_id"), nullable=False)
    template_id = Column(String(36), ForeignKey("templates.template_id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = Column(Integer, default=0)

    # Relationships
    request = relationship("RequestModel", back_populates="machines")
    template = relationship("TemplateModel", back_populates="machines")


class RequestModel(Base, JsonSerializableMixin):
    """SQLAlchemy model for requests."""

    __tablename__ = "requests"

    request_id = Column(String(36), primary_key=True)
    status = Column(String(50), nullable=False)
    request_type = Column(String(50), nullable=False)
    template_id = Column(String(36), ForeignKey("templates.template_id"), nullable=True)
    number_of_machines = Column(Integer, nullable=False)
    machine_ids = Column(JSON, nullable=True)  # List of machine IDs
    parameters = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    version = Column(Integer, default=0)

    # Relationships
    machines = relationship("MachineModel", back_populates="request")
    template = relationship("TemplateModel", back_populates="requests")


class TemplateModel(Base, JsonSerializableMixin):
    """SQLAlchemy model for templates."""

    __tablename__ = "templates"

    template_id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    provider_api = Column(String(50), nullable=False)
    instance_type = Column(String(50), nullable=False)
    price_type = Column(String(50), nullable=False)
    max_number = Column(Integer, nullable=False)
    is_available = Column(Boolean, default=True)
    parameters = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = Column(Integer, default=0)

    # Relationships
    machines = relationship("MachineModel", back_populates="template")
    requests = relationship("RequestModel", back_populates="template")
