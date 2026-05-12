"""Unit tests for MachineSerializer._normalize_on_read legacy field migration."""

import pytest

from orb.infrastructure.storage.repositories.machine_repository import MachineSerializer


def _serializer():
    return MachineSerializer()


@pytest.mark.unit
class TestNormalizeOnReadLegacyMigration:
    """vcpus/availability_zone/region are migrated from metadata → provider_data on read."""

    def test_migrates_vcpus_from_metadata(self):
        data = {"metadata": {"vcpus": 4}, "provider_data": {}}
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["vcpus"] == 4
        assert "vcpus" not in result["metadata"]

    def test_migrates_availability_zone_from_metadata(self):
        data = {"metadata": {"availability_zone": "us-east-1a"}, "provider_data": {}}
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["availability_zone"] == "us-east-1a"
        assert "availability_zone" not in result["metadata"]

    def test_migrates_region_from_metadata(self):
        data = {"metadata": {"region": "us-east-1"}, "provider_data": {}}
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["region"] == "us-east-1"
        assert "region" not in result["metadata"]

    def test_migrates_all_three_fields_at_once(self):
        data = {
            "metadata": {"vcpus": 8, "availability_zone": "eu-west-1b", "region": "eu-west-1"},
            "provider_data": {},
        }
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["vcpus"] == 8
        assert result["provider_data"]["availability_zone"] == "eu-west-1b"
        assert result["provider_data"]["region"] == "eu-west-1"
        assert "vcpus" not in result["metadata"]
        assert "availability_zone" not in result["metadata"]
        assert "region" not in result["metadata"]

    def test_preserves_existing_provider_data_value(self):
        """If provider_data already has the key, metadata value must NOT overwrite it."""
        data = {
            "metadata": {"vcpus": 2},
            "provider_data": {"vcpus": 16},
        }
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["vcpus"] == 16

    def test_preserves_existing_provider_data_az(self):
        data = {
            "metadata": {"availability_zone": "us-east-1a"},
            "provider_data": {"availability_zone": "us-west-2b"},
        }
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["availability_zone"] == "us-west-2b"

    def test_idempotent_second_run_is_noop(self):
        """Running _normalize_on_read twice produces the same result."""
        data = {
            "metadata": {"vcpus": 4, "availability_zone": "us-east-1a"},
            "provider_data": {},
        }
        s = _serializer()
        first = s._normalize_on_read(data)
        second = s._normalize_on_read(first)
        assert second["provider_data"]["vcpus"] == 4
        assert second["provider_data"]["availability_zone"] == "us-east-1a"
        assert "vcpus" not in second["metadata"]
        assert "availability_zone" not in second["metadata"]

    def test_no_migration_when_metadata_absent(self):
        """Records with no metadata key at all must not raise."""
        data = {"provider_data": {"vcpus": 2}}
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["vcpus"] == 2

    def test_no_migration_when_provider_data_absent(self):
        """Records with no provider_data key get it created with migrated values."""
        data = {"metadata": {"vcpus": 4, "availability_zone": "ap-southeast-1a"}}
        result = _serializer()._normalize_on_read(data)
        assert result["provider_data"]["vcpus"] == 4
        assert result["provider_data"]["availability_zone"] == "ap-southeast-1a"

    def test_other_metadata_fields_are_preserved(self):
        """Migration must not remove unrelated metadata fields."""
        data = {
            "metadata": {
                "vcpus": 4,
                "ami_id": "ami-0abc123",
                "ebs_optimized": True,
            },
            "provider_data": {},
        }
        result = _serializer()._normalize_on_read(data)
        assert result["metadata"]["ami_id"] == "ami-0abc123"
        assert result["metadata"]["ebs_optimized"] is True
        assert "vcpus" not in result["metadata"]

    def test_does_not_mutate_caller_dict(self):
        """_normalize_on_read must not mutate the input dict."""
        original_metadata = {"vcpus": 4}
        data = {"metadata": original_metadata, "provider_data": {}}
        _serializer()._normalize_on_read(data)
        # The original dict passed in should be unchanged
        assert "vcpus" in original_metadata
