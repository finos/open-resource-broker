"""Tests for EntitySerializer component."""

import pytest
from dataclasses import dataclass
from typing import Any, Dict

from infrastructure.storage.components.entity_serializer import (
    BaseEntitySerializer,
    EntitySerializer,
)


@dataclass
class TestEntity:
    """Test entity for serialization tests."""

    id: str
    name: str
    value: int


class TestEntitySerializer(BaseEntitySerializer):
    """Test implementation of EntitySerializer."""

    def to_dict(self, entity: TestEntity) -> Dict[str, Any]:
        """Convert TestEntity to dictionary."""
        return {
            "id": entity.id,
            "name": entity.name,
            "value": entity.value,
        }

    def from_dict(self, data: Dict[str, Any]) -> TestEntity:
        """Convert dictionary to TestEntity."""
        return TestEntity(
            id=data["id"],
            name=data["name"],
            value=data["value"],
        )


class TestEntitySerializerComponent:
    """Test EntitySerializer component."""

    def test_serializer_interface(self):
        """Test that EntitySerializer is abstract."""
        with pytest.raises(TypeError):
            EntitySerializer()

    def test_base_serializer_initialization(self):
        """Test BaseEntitySerializer initialization."""
        serializer = TestEntitySerializer()
        assert serializer.logger is not None

    def test_to_dict_serialization(self):
        """Test entity to dictionary serialization."""
        serializer = TestEntitySerializer()
        entity = TestEntity(id="test-1", name="Test Entity", value=42)

        result = serializer.to_dict(entity)

        assert result == {
            "id": "test-1",
            "name": "Test Entity",
            "value": 42,
        }

    def test_from_dict_deserialization(self):
        """Test dictionary to entity deserialization."""
        serializer = TestEntitySerializer()
        data = {
            "id": "test-1",
            "name": "Test Entity",
            "value": 42,
        }

        result = serializer.from_dict(data)

        assert isinstance(result, TestEntity)
        assert result.id == "test-1"
        assert result.name == "Test Entity"
        assert result.value == 42

    def test_round_trip_serialization(self):
        """Test complete serialization round trip."""
        serializer = TestEntitySerializer()
        original = TestEntity(id="test-1", name="Test Entity", value=42)

        # Serialize to dict and back
        data = serializer.to_dict(original)
        result = serializer.from_dict(data)

        assert result == original
