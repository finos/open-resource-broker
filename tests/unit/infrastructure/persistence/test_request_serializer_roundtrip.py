"""Round-trip test for RequestSerializer.

Guards against drift: if a field is added to Request but not to
RequestSerializer.to_dict / from_dict, this test fails.
"""

from datetime import datetime, timezone

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestStatus, RequestType
from orb.infrastructure.storage.repositories.request_repository import RequestSerializer


def _make_fully_populated_request() -> Request:
    """Build a Request with every non-excluded field set to a non-default, non-None value."""
    return Request(
        # Core identification
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000001"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        provider_name="aws-us-east-1",
        # Request configuration
        template_id="tpl-roundtrip-001",
        requested_count=3,
        desired_capacity=3,
        # Provider tracking
        provider_api="EC2Fleet",
        # Resource tracking
        resource_ids=["fleet-0abc123def456789a", "fleet-0def456abc789012b"],
        machine_ids=["i-0abc123def456789a", "i-0def456abc789012b"],
        # Request state
        status=RequestStatus.IN_PROGRESS,
        status_message="Processing request",
        # Results
        successful_count=2,
        failed_count=1,
        # Lifecycle timestamps
        started_at=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 15, 10, 5, 0, tzinfo=timezone.utc),
        # Metadata and error details
        metadata={"timeout": 300, "tags": {"Env": "test"}, "region": "us-east-1"},
        error_details={"error_0": {"message": "instance limit", "details": {}}},
        # Provider-specific data
        provider_data={"fleet_id": "fleet-abc", "spot_price": "0.05"},
        # Versioning
        version=4,
        # Base entity timestamps
        created_at=datetime(2026, 1, 15, 9, 58, 0, tzinfo=timezone.utc),
    )


@pytest.mark.unit
@pytest.mark.infrastructure
class TestRequestSerializerRoundTrip:
    """RequestSerializer must preserve every non-excluded Request field through to_dict → from_dict."""

    def test_round_trip_preserves_all_fields(self):
        """If a field is added to Request but not to RequestSerializer, this test fails."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        serialized = serializer.to_dict(request)
        restored = serializer.from_dict(serialized)

        # Compare the fields that the serializer is responsible for persisting.
        # We compare by name against the model fields minus excluded ones.
        excluded = Request._SERIALIZATION_EXCLUDED_FIELDS
        original_dump = request.model_dump(mode="json")
        restored_dump = restored.model_dump(mode="json")

        for field_name in Request.model_fields:
            if field_name in excluded:
                continue
            assert original_dump[field_name] == restored_dump[field_name], (
                f"Field '{field_name}' lost in round-trip: "
                f"{original_dump[field_name]!r} != {restored_dump[field_name]!r}"
            )

    def test_round_trip_request_id(self):
        """request_id must survive serialization as a proper RequestId value object."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert str(restored.request_id) == str(request.request_id)

    def test_round_trip_status(self):
        """status must survive serialization as the correct RequestStatus enum value."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.status == request.status

    def test_round_trip_requested_count(self):
        """requested_count must survive even though it is stored under the legacy key 'machine_count'."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.requested_count == request.requested_count

    def test_round_trip_desired_capacity(self):
        """desired_capacity must survive serialization."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.desired_capacity == request.desired_capacity

    def test_round_trip_provider_data(self):
        """provider_data dict must survive serialization intact."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.provider_data == request.provider_data

    def test_round_trip_machine_ids(self):
        """machine_ids list must survive serialization."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.machine_ids == request.machine_ids

    def test_round_trip_resource_ids(self):
        """resource_ids list must survive serialization."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.resource_ids == request.resource_ids

    def test_round_trip_timestamps(self):
        """All three lifecycle timestamps must survive serialization with timezone info."""
        request = _make_fully_populated_request()
        serializer = RequestSerializer()

        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.created_at == request.created_at
        assert restored.started_at == request.started_at
        assert restored.completed_at == request.completed_at

    def test_round_trip_with_none_optional_fields(self):
        """Optional fields set to None must survive the round-trip without becoming something else."""
        request = Request(
            request_id=RequestId(value="req-00000000-0000-0000-0000-000000000002"),
            request_type=RequestType.ACQUIRE,
            provider_type="aws",
            template_id="tpl-minimal",
            requested_count=1,
            created_at=datetime(2026, 1, 15, 9, 58, 0, tzinfo=timezone.utc),
        )
        serializer = RequestSerializer()
        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.provider_name is None
        assert restored.provider_api is None
        assert restored.started_at is None
        assert restored.completed_at is None
        assert restored.status_message is None

    def test_round_trip_return_request(self):
        """A RETURN request with machine_ids must survive serialization."""
        request = Request(
            request_id=RequestId(value="ret-00000000-0000-0000-0000-000000000003"),
            request_type=RequestType.RETURN,
            provider_type="aws",
            provider_name="aws-us-east-1",
            template_id="return-request",
            requested_count=2,
            desired_capacity=2,
            machine_ids=["i-0abc123def456789a", "i-0def456abc789012b"],
            status=RequestStatus.PENDING,
            created_at=datetime(2026, 1, 15, 9, 58, 0, tzinfo=timezone.utc),
        )
        serializer = RequestSerializer()
        restored = serializer.from_dict(serializer.to_dict(request))

        assert restored.request_type == RequestType.RETURN
        assert restored.machine_ids == request.machine_ids
        assert restored.requested_count == request.requested_count
