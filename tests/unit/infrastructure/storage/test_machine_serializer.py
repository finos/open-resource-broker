"""Unit tests for MachineSerializer._normalize_on_read legacy field migration."""

import pytest

from orb.infrastructure.storage.repositories.machine_repository import MachineSerializer


def _serializer():
    return MachineSerializer()


# ---------------------------------------------------------------------------
# provider_api sentinel removal
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderApiSentinelRemoval:
    """Empty-string provider_api must never reach model_validate as an empty string.

    Rows with provider_api="" (from any historical write path that stored a
    sentinel value) must cause model_validate to raise ValidationError so
    _safe_deserialize_iter can log and skip them.
    """

    def test_empty_provider_api_raises_on_from_dict(self):
        """from_dict with provider_api='' must raise (model_validate rejects min_length=1)."""
        from pydantic import ValidationError

        data = {
            "machine_id": "i-orphan000000001",
            "template_id": "tpl-001",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "",
            "instance_type": "t2.micro",
            "image_id": "ami-00000000",
            "status": "pending",
            "schema_version": "2.0.0",
        }
        with pytest.raises((ValidationError, Exception)):
            _serializer().from_dict(data)

    def test_missing_provider_api_raises_on_from_dict(self):
        """from_dict with no provider_api key must raise (required field)."""
        from pydantic import ValidationError

        data = {
            "machine_id": "i-orphan000000002",
            "template_id": "tpl-001",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "instance_type": "t2.micro",
            "image_id": "ami-00000000",
            "status": "pending",
            "schema_version": "2.0.0",
        }
        with pytest.raises((ValidationError, Exception)):
            _serializer().from_dict(data)

    def test_normalize_removes_empty_provider_api_key(self):
        """_normalize_on_read must remove the provider_api key when it is empty
        so model_validate raises ValidationError rather than accepting ''."""
        data = {
            "machine_id": "i-orphan000000003",
            "provider_api": "",
        }
        result = _serializer()._normalize_on_read(data)
        assert "provider_api" not in result, (
            "_normalize_on_read must pop empty provider_api so model_validate rejects the row"
        )

    def test_normalize_keeps_nonempty_provider_api(self):
        """_normalize_on_read must not touch a valid provider_api value."""
        data = {
            "machine_id": "i-valid000000001",
            "provider_api": "EC2Fleet",
        }
        result = _serializer()._normalize_on_read(data)
        assert result["provider_api"] == "EC2Fleet"

    def test_safe_deserialize_iter_skips_empty_provider_api_rows(self, caplog):
        """_safe_deserialize_iter must skip rows with empty provider_api and log at ERROR."""
        import logging

        from orb.infrastructure.storage.base.repository_mixin import StorageRepositoryMixin

        class _Repo(StorageRepositoryMixin):
            def __init__(self):
                self.serializer = _serializer()
                self.logger = None

        repo = _Repo()

        bad_row = {
            "machine_id": "i-orphan000000004",
            "template_id": "tpl-001",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "",
            "instance_type": "t2.micro",
            "image_id": "ami-00000000",
            "status": "pending",
            "schema_version": "2.0.0",
        }
        good_row = {
            "machine_id": "i-valid000000002",
            "template_id": "tpl-001",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "RunInstances",
            "instance_type": "t2.micro",
            "image_id": "ami-00000000",
            "status": "pending",
            "schema_version": "2.0.0",
        }

        with caplog.at_level(logging.ERROR):
            results = list(repo._safe_deserialize_iter([bad_row, good_row]))

        # Only the valid row is returned
        assert len(results) == 1
        assert str(results[0].machine_id) == "i-valid000000002"

        # Skip counter incremented for machines
        assert repo._skipped_row_count.get("machines", 0) == 1


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


@pytest.mark.unit
class TestFindByReturnRequestIdStrictDeserialization:
    """find_by_return_request_id must raise on corrupt rows, not silently skip them.

    A skipped malformed row on the deprovisioning path is semantically
    indistinguishable from "machine already terminated", which would cause the
    return-request status poller to stamp COMPLETED prematurely.
    """

    def _make_repo(self, rows):
        """Build a MachineRepositoryImpl backed by an in-memory mock."""
        from unittest.mock import MagicMock

        from orb.infrastructure.storage.repositories.machine_repository import (
            MachineRepositoryImpl,
        )

        storage = MagicMock()
        storage.find_by_criteria.return_value = rows
        repo = MachineRepositoryImpl(storage)
        # Disable the entity_type write that requires a real attribute
        return repo

    def _good_row(self, machine_id="i-good000000001"):
        return {
            "machine_id": machine_id,
            "return_request_id": "ret-001",
            "template_id": "tpl-001",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "RunInstances",
            "instance_type": "t2.micro",
            "image_id": "ami-00000000",
            "status": "running",
            "schema_version": "2.0.0",
        }

    def _corrupt_row(self, machine_id="i-corrupt0000001"):
        """Row with empty provider_api — will fail model_validate (min_length=1)."""
        return {
            "machine_id": machine_id,
            "return_request_id": "ret-001",
            "template_id": "tpl-001",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "",  # corrupt — violates min_length=1 invariant
            "instance_type": "t2.micro",
            "image_id": "ami-00000000",
            "status": "running",
            "schema_version": "2.0.0",
        }

    def test_corrupt_row_raises_not_silently_skipped(self):
        """A machine row with empty provider_api must propagate an exception."""
        repo = self._make_repo([self._corrupt_row()])
        with pytest.raises(Exception):
            repo.find_by_return_request_id("ret-001")

    def test_good_rows_returned_normally(self):
        """Healthy rows must deserialize successfully."""
        repo = self._make_repo(
            [self._good_row("i-good000000001"), self._good_row("i-good000000002")]
        )
        results = repo.find_by_return_request_id("ret-001")
        assert len(results) == 2
