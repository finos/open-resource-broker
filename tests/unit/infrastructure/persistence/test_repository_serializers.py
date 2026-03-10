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

    def test_machine_repository_imports_constant(self):
        """machine_repository must import LEGACY_DEFAULT_PROVIDER_TYPE."""
        source = inspect.getsource(_machine_repo_mod)
        assert "LEGACY_DEFAULT_PROVIDER_TYPE" in source

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
