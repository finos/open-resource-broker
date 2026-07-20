"""Unit tests for template_repository: TemplateSerializer and TemplateRepositoryImpl."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orb.domain.template.template_aggregate import Template
from orb.domain.template.value_objects import TemplateId
from orb.infrastructure.storage.repositories.template_repository import (
    TemplateRepositoryImpl,
    TemplateSerializer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_TEMPLATE_ID = "tpl-001"


def _minimal_data() -> dict:
    return {
        "template_id": _TEMPLATE_ID,
        "name": "Test Template",
        "image_id": "ami-00000000",
        "provider_type": "aws",
        "provider_name": "aws-us-east-1",
        "provider_api": "RunInstances",
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
    }


def _make_template(template_id: str = _TEMPLATE_ID) -> Template:
    return Template.model_validate(
        dict(
            _minimal_data(),
            template_id=template_id,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )


def _make_repo(storage: MagicMock | None = None):
    if storage is None:
        storage = MagicMock()
    return TemplateRepositoryImpl(storage), storage


# ---------------------------------------------------------------------------
# TemplateSerializer — _apply_nullable_defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateSerializerApplyNullableDefaults:
    """_apply_nullable_defaults coerces NULL fields to safe empty containers."""

    def test_null_tags_coerced_to_empty_dict(self):
        result = TemplateSerializer._apply_nullable_defaults({"tags": None})
        assert result["tags"] == {}

    def test_null_metadata_coerced_to_empty_dict(self):
        result = TemplateSerializer._apply_nullable_defaults({"metadata": None})
        assert result["metadata"] == {}

    def test_null_security_group_ids_coerced_to_empty_list(self):
        result = TemplateSerializer._apply_nullable_defaults({"security_group_ids": None})
        assert result["security_group_ids"] == []

    def test_null_subnet_ids_coerced_to_empty_list(self):
        result = TemplateSerializer._apply_nullable_defaults({"subnet_ids": None})
        assert result["subnet_ids"] == []

    def test_null_network_zones_coerced_to_empty_list(self):
        result = TemplateSerializer._apply_nullable_defaults({"network_zones": None})
        assert result["network_zones"] == []

    def test_null_machine_types_coerced_to_empty_dict(self):
        result = TemplateSerializer._apply_nullable_defaults({"machine_types": None})
        assert result["machine_types"] == {}

    def test_null_machine_types_ondemand_coerced_to_empty_dict(self):
        result = TemplateSerializer._apply_nullable_defaults({"machine_types_ondemand": None})
        assert result["machine_types_ondemand"] == {}

    def test_null_machine_types_priority_coerced_to_empty_dict(self):
        result = TemplateSerializer._apply_nullable_defaults({"machine_types_priority": None})
        assert result["machine_types_priority"] == {}

    def test_existing_non_null_values_preserved(self):
        data = {
            "tags": {"env": "prod"},
            "subnet_ids": ["subnet-001"],
            "machine_types": {"t2.micro": 1},
        }
        result = TemplateSerializer._apply_nullable_defaults(data)
        assert result["tags"] == {"env": "prod"}
        assert result["subnet_ids"] == ["subnet-001"]
        assert result["machine_types"] == {"t2.micro": 1}


# ---------------------------------------------------------------------------
# TemplateSerializer — _normalize_machine_types
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateSerializerNormalizeMachineTypes:
    """_normalize_machine_types handles all HF/ORB machine type input formats."""

    def _s(self):
        return TemplateSerializer()

    def test_vm_type_single_string_to_dict(self):
        result = self._s()._normalize_machine_types({"vmType": "t2.micro"})
        assert result == {"t2.micro": 1}

    def test_vm_types_dict_passed_through(self):
        result = self._s()._normalize_machine_types({"vmTypes": {"t2.micro": 2, "m5.large": 1}})
        assert result == {"t2.micro": 2, "m5.large": 1}

    def test_instance_type_single_string_to_dict(self):
        result = self._s()._normalize_machine_types({"instance_type": "m5.large"})
        assert result == {"m5.large": 1}

    def test_instance_types_dict_passed_through(self):
        result = self._s()._normalize_machine_types({"instance_types": {"m5.large": 3}})
        assert result == {"m5.large": 3}

    def test_empty_data_returns_empty_dict(self):
        result = self._s()._normalize_machine_types({})
        assert result == {}

    def test_vm_type_takes_priority_over_instance_type(self):
        result = self._s()._normalize_machine_types(
            {"vmType": "t2.micro", "instance_type": "m5.large"}
        )
        assert result == {"t2.micro": 1}


# ---------------------------------------------------------------------------
# TemplateSerializer — to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateSerializerToDict:
    """to_dict produces a complete serializable dictionary from a Template."""

    def _s(self):
        return TemplateSerializer()

    def test_produces_required_keys(self):
        data = self._s().to_dict(_make_template())
        for key in (
            "template_id",
            "name",
            "image_id",
            "provider_type",
            "provider_name",
            "provider_api",
            "created_at",
            "updated_at",
            "schema_version",
        ):
            assert key in data, f"Missing key: {key}"

    def test_schema_version_is_2_0_0(self):
        data = self._s().to_dict(_make_template())
        assert data["schema_version"] == "2.0.0"

    def test_is_active_defaults_to_true(self):
        data = self._s().to_dict(_make_template())
        assert data["is_active"] is True

    def test_machine_types_key_present(self):
        data = self._s().to_dict(_make_template())
        assert "machine_types" in data

    def test_tags_defaults_to_empty_dict(self):
        data = self._s().to_dict(_make_template())
        assert data["tags"] == {}

    def test_metadata_defaults_to_empty_dict(self):
        data = self._s().to_dict(_make_template())
        assert data["metadata"] == {}


# ---------------------------------------------------------------------------
# TemplateSerializer — from_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateSerializerFromDict:
    """from_dict reconstructs a Template from stored data."""

    def _s(self):
        return TemplateSerializer()

    def test_round_trip_preserves_template_id(self):
        template = _make_template()
        data = self._s().to_dict(template)
        restored = self._s().from_dict(data)
        assert restored.template_id == _TEMPLATE_ID

    def test_round_trip_preserves_provider_api(self):
        template = _make_template()
        data = self._s().to_dict(template)
        restored = self._s().from_dict(data)
        assert restored.provider_api == "RunInstances"

    def test_round_trip_preserves_is_active(self):
        template = _make_template()
        data = self._s().to_dict(template)
        restored = self._s().from_dict(data)
        assert restored.is_active is True

    def test_legacy_template_id_key_accepted(self):
        """templateId (camelCase) must be handled as an alias for template_id."""
        data = dict(_minimal_data())
        data["templateId"] = data.pop("template_id")
        restored = self._s().from_dict(data)
        assert restored.template_id == _TEMPLATE_ID

    def test_legacy_image_id_key_accepted(self):
        """imageId (camelCase) is the HF wire-format — must map to image_id."""
        data = dict(_minimal_data())
        data["imageId"] = data.pop("image_id")
        restored = self._s().from_dict(data)
        assert restored.image_id == "ami-00000000"

    def test_legacy_subnet_id_single_string_accepted(self):
        """subnetId (HF wire key) must be wrapped in a list."""
        data = dict(_minimal_data(), subnetId="subnet-abc123")
        restored = self._s().from_dict(data)
        assert "subnet-abc123" in restored.subnet_ids

    def test_legacy_security_group_ids_key_accepted(self):
        data = dict(_minimal_data(), securityGroupIds=["sg-001"])
        restored = self._s().from_dict(data)
        assert "sg-001" in restored.security_group_ids

    def test_legacy_max_number_key_accepted(self):
        data = dict(_minimal_data(), maxNumber=20)
        restored = self._s().from_dict(data)
        assert restored.max_instances == 20

    def test_legacy_provider_api_camel_case_key_accepted(self):
        data = dict(_minimal_data())
        data["providerApi"] = data.pop("provider_api")
        restored = self._s().from_dict(data)
        assert restored.provider_api == "RunInstances"

    def test_missing_template_id_raises_value_error(self):
        data = {"name": "no-id-template", "image_id": "ami-0000"}
        with pytest.raises(Exception):
            self._s().from_dict(data)

    def test_is_active_defaults_to_true_when_absent(self):
        data = _minimal_data()
        data.pop("is_active", None)
        restored = self._s().from_dict(data)
        assert restored.is_active is True

    def test_caller_dict_not_mutated(self):
        data = _minimal_data()
        original_keys = set(data.keys())
        self._s().from_dict(data)
        assert set(data.keys()) == original_keys

    def test_price_type_defaults_to_ondemand(self):
        data = _minimal_data()
        data.pop("price_type", None)
        restored = self._s().from_dict(data)
        assert restored.price_type == "ondemand"

    def test_allocation_strategy_defaults_to_lowest_price(self):
        data = _minimal_data()
        data.pop("allocation_strategy", None)
        restored = self._s().from_dict(data)
        assert restored.allocation_strategy == "lowest_price"

    def test_vm_type_machine_type_key_normalized(self):
        data = dict(_minimal_data(), vmType="t2.medium")
        restored = self._s().from_dict(data)
        assert "t2.medium" in restored.machine_types

    def test_key_name_legacy_keys_accepted(self):
        """keyName and key_pair_name are HF-legacy aliases for key_name."""
        data = dict(_minimal_data(), keyName="my-key")
        restored = self._s().from_dict(data)
        assert restored.key_name == "my-key"

    def test_machine_role_falls_back_to_instance_profile(self):
        data = dict(
            _minimal_data(),
            instance_profile="arn:aws:iam::123456789012:instance-profile/my-profile",
        )
        restored = self._s().from_dict(data)
        assert "arn:aws" in (restored.machine_role or "")

    def test_defaults_service_applied_when_configured(self):
        """If a defaults_service is configured, it is called on deserialization."""
        defaults_service = MagicMock()
        defaults_service.resolve_template_defaults.return_value = _minimal_data()
        s = TemplateSerializer(defaults_service=defaults_service)
        s.from_dict(_minimal_data())
        defaults_service.resolve_template_defaults.assert_called_once()

    def test_defaults_service_failure_falls_back_to_original_data(self):
        """A failing defaults_service must not prevent deserialization."""
        defaults_service = MagicMock()
        defaults_service.resolve_template_defaults.side_effect = RuntimeError("service down")
        s = TemplateSerializer(defaults_service=defaults_service)
        restored = s.from_dict(_minimal_data())
        assert restored.template_id == _TEMPLATE_ID


# ---------------------------------------------------------------------------
# TemplateRepositoryImpl — save
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRepositoryImplSave:
    """save persists, updates the cache, and publishes events."""

    def test_save_calls_storage_save(self):
        repo, storage = _make_repo()
        repo.save(_make_template())
        assert storage.save.called

    def test_save_uses_template_id_as_entity_key(self):
        repo, storage = _make_repo()
        repo.save(_make_template())
        args = storage.save.call_args[0]
        entity_id, _ = args
        assert entity_id == _TEMPLATE_ID

    def test_save_stores_template_in_cache(self):
        repo, storage = _make_repo()
        template = _make_template()
        repo.save(template)
        # After save, the cache must hold the entry — storage.find_by_id is not needed
        storage.find_by_id.return_value = None
        cached = repo.cache.get(_TEMPLATE_ID)
        assert cached is not None

    def test_save_increments_version_via_version_manager(self):
        repo, storage = _make_repo()
        repo.save(_make_template())
        # version field must be in the dict passed to storage
        _, data = storage.save.call_args[0]
        assert "version" in data

    def test_save_raises_on_storage_failure(self):
        repo, storage = _make_repo()
        storage.save.side_effect = RuntimeError("no space")
        with pytest.raises(Exception):
            repo.save(_make_template())


# ---------------------------------------------------------------------------
# TemplateRepositoryImpl — get_by_id / find_by_id / find_by_template_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRepositoryImplGetById:
    """get_by_id returns from cache on hit and from storage on miss."""

    def test_get_by_id_returns_template_on_cache_miss(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _minimal_data()
        tid = TemplateId(value=_TEMPLATE_ID)
        result = repo.get_by_id(tid)
        assert result is not None
        assert result.template_id == _TEMPLATE_ID

    def test_get_by_id_uses_cache_on_second_call(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _minimal_data()
        tid = TemplateId(value=_TEMPLATE_ID)
        repo.get_by_id(tid)
        repo.get_by_id(tid)  # second call should hit cache
        # Cold cache: first call reads storage and caches; second is served
        # from cache, so storage is hit exactly once.
        assert storage.find_by_id.call_count == 1

    def test_get_by_id_returns_none_when_not_found(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = None
        tid = TemplateId(value=_TEMPLATE_ID)
        result = repo.get_by_id(tid)
        assert result is None

    def test_find_by_id_delegates_to_get_by_id(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _minimal_data()
        tid = TemplateId(value=_TEMPLATE_ID)
        result = repo.find_by_id(tid)
        assert result is not None

    def test_find_by_template_id_string_resolves_correctly(self):
        repo, storage = _make_repo()
        storage.find_by_id.return_value = _minimal_data()
        result = repo.find_by_template_id(_TEMPLATE_ID)
        assert result is not None


# ---------------------------------------------------------------------------
# TemplateRepositoryImpl — find_by_name / find_active / find_by_provider_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRepositoryImplFindByCriteria:
    """find_by_name, find_active_templates, find_by_provider_api delegate criteria."""

    def test_find_by_name_passes_name_to_storage(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_data()]
        result = repo.find_by_name("Test Template")
        assert result is not None
        storage.find_by_criteria.assert_called_once_with({"name": "Test Template"})

    def test_find_by_name_returns_none_when_not_found(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = []
        result = repo.find_by_name("Ghost Template")
        assert result is None

    def test_find_active_templates_queries_is_active_true(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_data()]
        results = repo.find_active_templates()
        assert len(results) == 1
        storage.find_by_criteria.assert_called_once_with({"is_active": True})

    def test_find_by_provider_api_passes_correct_criteria(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_data()]
        results = repo.find_by_provider_api("EC2Fleet")
        assert len(results) == 1
        storage.find_by_criteria.assert_called_once_with({"provider_api": "EC2Fleet"})

    def test_search_templates_passes_arbitrary_criteria(self):
        repo, storage = _make_repo()
        storage.find_by_criteria.return_value = [_minimal_data()]
        results = repo.search_templates({"provider_type": "aws", "is_active": True})
        assert len(results) == 1
        storage.find_by_criteria.assert_called_once_with(
            {"provider_type": "aws", "is_active": True}
        )


# ---------------------------------------------------------------------------
# TemplateRepositoryImpl — find_all / get_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRepositoryImplFindAll:
    """find_all and get_all return all templates from storage."""

    def test_find_all_returns_all_templates(self):
        repo, storage = _make_repo()
        storage.find_all.return_value = [
            _minimal_data(),
            dict(_minimal_data(), template_id="tpl-002", name="T2"),
        ]
        results = repo.find_all()
        assert len(results) == 2

    def test_find_all_returns_empty_when_no_templates(self):
        repo, storage = _make_repo()
        storage.find_all.return_value = []
        results = repo.find_all()
        assert results == []

    def test_get_all_is_alias_for_find_all(self):
        repo, storage = _make_repo()
        storage.find_all.return_value = [_minimal_data()]
        assert repo.get_all() == repo.find_all()


# ---------------------------------------------------------------------------
# TemplateRepositoryImpl — delete / exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRepositoryImplDeleteAndExists:
    """delete and exists correctly delegate to storage and update cache."""

    def test_delete_calls_storage_delete_with_template_id(self):
        repo, storage = _make_repo()
        tid = TemplateId(value=_TEMPLATE_ID)
        repo.delete(tid)
        storage.delete.assert_called_once_with(_TEMPLATE_ID)

    def test_delete_removes_entry_from_cache(self):
        repo, _ = _make_repo()
        # Pre-populate the cache by saving first
        repo.save(_make_template())
        assert repo.cache.get(_TEMPLATE_ID) is not None
        # Now delete
        repo.delete(TemplateId(value=_TEMPLATE_ID))
        assert repo.cache.get(_TEMPLATE_ID) is None

    def test_exists_returns_true_when_storage_confirms(self):
        repo, storage = _make_repo()
        storage.exists.return_value = True
        assert repo.exists(TemplateId(value=_TEMPLATE_ID)) is True

    def test_exists_returns_false_when_absent(self):
        repo, storage = _make_repo()
        storage.exists.return_value = False
        assert repo.exists(TemplateId(value=_TEMPLATE_ID)) is False


# ---------------------------------------------------------------------------
# TemplateRepositoryImpl — count_by_provider_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateRepositoryImplCountByProviderApi:
    """count_by_provider_api fast-paths to SQL, else falls back to Python grouping."""

    def test_fast_path_delegates_to_count_by_column(self):
        repo, storage = _make_repo()
        storage.count_by_column.return_value = {"RunInstances": 5, "EC2Fleet": 2}
        counts = repo.count_by_provider_api()
        assert counts == {"RunInstances": 5, "EC2Fleet": 2}
        storage.count_by_column.assert_called_once_with("provider_api")

    def test_slow_path_when_count_by_column_absent(self):
        storage = MagicMock(
            spec=["find_by_id", "find_by_criteria", "find_all", "delete", "exists", "save"]
        )
        storage.find_all.return_value = []
        repo = TemplateRepositoryImpl(storage)
        counts = repo.count_by_provider_api()
        assert isinstance(counts, dict)

    def test_fast_path_falls_back_when_empty_result(self):
        repo, storage = _make_repo()
        storage.count_by_column.return_value = {}
        storage.find_all.return_value = []
        counts = repo.count_by_provider_api()
        assert isinstance(counts, dict)
