"""Tests for storage repository serializer constants and legacy provider_type handling."""

import inspect

import pytest

import orb.infrastructure.storage.constants as _constants_mod
import orb.infrastructure.storage.repositories.machine_repository as _machine_repo_mod
import orb.infrastructure.storage.repositories.request_repository as _request_repo_mod


@pytest.mark.unit
@pytest.mark.infrastructure
class TestLegacyDefaultProviderTypeConstant:
    """Assert LEGACY_DEFAULT_PROVIDER_TYPE constant exists and is used correctly."""

    def test_constant_exists_in_storage_constants(self):
        """LEGACY_DEFAULT_PROVIDER_TYPE must be defined in storage constants module."""
        assert _constants_mod.LEGACY_DEFAULT_PROVIDER_TYPE == "aws"

    def test_constant_is_named_not_bare_string(self):
        """The constant must be a module-level name, not an inline literal."""
        assert hasattr(_constants_mod, "LEGACY_DEFAULT_PROVIDER_TYPE")

    def test_machine_repository_uses_constant_not_bare_string(self):
        """machine_repository.py must not contain a bare 'aws' string as a default."""
        source = inspect.getsource(_machine_repo_mod)
        # The bare literal default pattern must not appear
        assert "data.get('provider_type', 'aws')" not in source
        assert 'data.get("provider_type", "aws")' not in source

    def test_request_repository_uses_constant_not_bare_string(self):
        """request_repository.py must not contain a bare 'aws' string as a default."""
        source = inspect.getsource(_request_repo_mod)
        assert "data.get('provider_type', 'aws')" not in source
        assert 'data.get("provider_type", "aws")' not in source

    def test_machine_repository_does_not_inline_bare_aws_default(self):
        """machine_repository must not inline a bare 'aws' string as a provider_type default.

        The default now lives on Machine.provider_type = Field(default="aws") in the
        aggregate, so the repository no longer needs to import or reference the constant.
        This test guards against re-introducing an inline literal in _normalize_on_read.
        """
        source = inspect.getsource(_machine_repo_mod)
        assert "data.get('provider_type', 'aws')" not in source
        assert 'data.get("provider_type", "aws")' not in source
        assert "setdefault('provider_type', 'aws')" not in source
        assert 'setdefault("provider_type", "aws")' not in source

    def test_request_repository_imports_constant(self):
        """request_repository must import LEGACY_DEFAULT_PROVIDER_TYPE."""
        source = inspect.getsource(_request_repo_mod)
        assert "LEGACY_DEFAULT_PROVIDER_TYPE" in source

    def test_constant_has_explanatory_comment(self):
        """The constants module must contain a comment explaining the legacy default."""
        source = inspect.getsource(_constants_mod)
        assert "legacy" in source.lower() or "Legacy" in source


@pytest.mark.unit
@pytest.mark.infrastructure
class TestMachineSerializerLegacyProviderType:
    """MachineSerializer.from_dict falls back to LEGACY_DEFAULT_PROVIDER_TYPE for old records."""

    def _make_minimal_machine_data(self, **overrides):
        base = {
            "machine_id": "m-001",
            "name": "test-machine",
            "template_id": "tpl-1",
            "provider_name": "test-provider",
            "instance_type": "t3.micro",
            "image_id": "ami-12345678",
            "status": "pending",
        }
        base.update(overrides)
        return base

    def test_from_dict_uses_legacy_default_when_provider_type_absent(self):
        """Records without provider_type deserialize to 'aws' via the constant."""
        data = self._make_minimal_machine_data()
        assert "provider_type" not in data

        machine = _machine_repo_mod.MachineSerializer().from_dict(data)
        assert machine.provider_type == "aws"

    def test_from_dict_preserves_explicit_provider_type(self):
        """Records with an explicit provider_type are not overridden."""
        data = self._make_minimal_machine_data(provider_type="gcp")
        machine = _machine_repo_mod.MachineSerializer().from_dict(data)
        assert machine.provider_type == "gcp"

    def test_price_type_round_trip(self):
        """price_type survives to_dict -> from_dict round trip."""
        data = self._make_minimal_machine_data(provider_type="aws", price_type="spot")
        serializer = _machine_repo_mod.MachineSerializer()
        machine = serializer.from_dict(data)
        assert machine.price_type == "spot"
        serialized = serializer.to_dict(machine)
        assert serialized["price_type"] == "spot"

    def test_price_type_none_when_absent(self):
        """Records without price_type deserialize to None."""
        data = self._make_minimal_machine_data(provider_type="aws")
        machine = _machine_repo_mod.MachineSerializer().from_dict(data)
        assert machine.price_type is None

    def test_price_type_ondemand(self):
        """on-demand price_type round trips correctly."""
        data = self._make_minimal_machine_data(provider_type="aws", price_type="on-demand")
        serializer = _machine_repo_mod.MachineSerializer()
        machine = serializer.from_dict(data)
        serialized = serializer.to_dict(machine)
        assert serialized["price_type"] == "on-demand"


@pytest.mark.unit
@pytest.mark.infrastructure
class TestRequestSerializerLegacyProviderType:
    """RequestSerializer.from_dict falls back to LEGACY_DEFAULT_PROVIDER_TYPE for old records."""

    def _make_minimal_request_data(self, **overrides):
        from datetime import datetime, timezone

        base = {
            "request_id": "req-00000000-0000-0000-0000-000000000001",
            "template_id": "tpl-1",
            "machine_count": 1,
            "request_type": "acquire",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        base.update(overrides)
        return base

    def test_from_dict_uses_legacy_default_when_provider_type_absent(self):
        """Records without provider_type deserialize to 'aws' via the constant."""
        data = self._make_minimal_request_data()
        assert "provider_type" not in data

        request = _request_repo_mod.RequestSerializer().from_dict(data)
        assert request.provider_type == "aws"

    def test_from_dict_preserves_explicit_provider_type(self):
        """Records with an explicit provider_type are not overridden."""
        data = self._make_minimal_request_data(provider_type="azure")
        request = _request_repo_mod.RequestSerializer().from_dict(data)
        assert request.provider_type == "azure"


@pytest.mark.unit
@pytest.mark.infrastructure
class TestMachineSerializerPriceType:
    """MachineSerializer round-trips Machine.price_type through JSON storage."""

    def _make_minimal_machine_data(self, **overrides):
        base = {
            "machine_id": "m-001",
            "name": "test-machine",
            "template_id": "tpl-1",
            "provider_name": "test-provider",
            "instance_type": "t3.micro",
            "image_id": "ami-12345678",
            "status": "pending",
        }
        base.update(overrides)
        return base

    def _make_machine(self, **overrides):
        from orb.domain.base.value_objects import InstanceType
        from orb.domain.machine.aggregate import Machine
        from orb.domain.machine.machine_identifiers import MachineId
        from orb.domain.machine.machine_status import MachineStatus

        defaults = dict(
            machine_id=MachineId(value="i-1234567890abcdef0"),
            template_id="tpl-1",
            provider_type="aws",
            provider_name="aws-us-east-1",
            instance_type=InstanceType(value="m5.large"),
            image_id="ami-12345678",
            status=MachineStatus.RUNNING,
        )
        defaults.update(overrides)
        return Machine(**defaults)

    def test_from_dict_reads_price_type(self):
        """Stored price_type is restored onto the Machine aggregate."""
        data = self._make_minimal_machine_data(price_type="spot")
        machine = _machine_repo_mod.MachineSerializer().from_dict(data)
        assert machine.price_type == "spot"

    def test_from_dict_defaults_missing_price_type_to_none(self):
        """Legacy records without price_type deserialize with price_type=None."""
        data = self._make_minimal_machine_data()
        assert "price_type" not in data
        machine = _machine_repo_mod.MachineSerializer().from_dict(data)
        assert machine.price_type is None

    def test_to_dict_writes_price_type(self):
        """Machine.price_type appears in the serialized dict."""
        machine = self._make_machine(price_type="ondemand")
        serialized = _machine_repo_mod.MachineSerializer().to_dict(machine)
        assert serialized["price_type"] == "ondemand"

    def test_round_trip_preserves_price_type(self):
        """Machine → to_dict → from_dict preserves price_type end-to-end."""
        machine = self._make_machine(price_type="spot")
        serializer = _machine_repo_mod.MachineSerializer()
        restored = serializer.from_dict(serializer.to_dict(machine))
        assert restored.price_type == "spot"


@pytest.mark.unit
@pytest.mark.infrastructure
class TestMachineSerializerTagsMigration:
    """MachineSerializer.from_dict falls back to metadata.tags for pre-PR-209 records."""

    def _make_minimal_machine_data(self, **overrides):
        base = {
            "machine_id": "m-001",
            "name": "test-machine",
            "template_id": "tpl-1",
            "provider_name": "test-provider",
            "instance_type": "t3.micro",
            "image_id": "ami-12345678",
            "status": "pending",
        }
        base.update(overrides)
        return base

    def test_from_dict_reads_metadata_tags_when_top_level_tags_absent(self):
        """Legacy records with tags only in metadata.tags are migrated transparently."""
        data = self._make_minimal_machine_data(
            tags={},
            metadata={"tags": {"Environment": "prod", "Owner": "team-x"}},
        )
        machine = _machine_repo_mod.MachineSerializer().from_dict(data)
        assert machine.tags.tags == {"Environment": "prod", "Owner": "team-x"}

    def test_from_dict_prefers_top_level_tags_when_both_present(self):
        """Top-level tags take precedence over metadata.tags when both are present."""
        data = self._make_minimal_machine_data(
            tags={"New": "value"},
            metadata={"tags": {"Old": "value"}},
        )
        machine = _machine_repo_mod.MachineSerializer().from_dict(data)
        assert machine.tags.tags == {"New": "value"}
