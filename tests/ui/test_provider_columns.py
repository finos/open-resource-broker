"""Tests for orb.ui.components.provider_columns.

Tests exercise the pure-Python logic: descriptor-to-ColumnDef conversion,
dotted-path resolution, provider filtering, and deduplication.  No Reflex
runtime is required — the rx stub from conftest.py satisfies all imports.
"""

from __future__ import annotations

from typing import Any

# conftest.py installs the rx stub before any orb.ui imports.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AWS_MACHINE_DESCRIPTOR: dict[str, Any] = {
    "key": "aws_instance_type",
    "path": "provider_data.instance_type",
    "label": "Instance Type",
    "kind": "text",
    "resource_type": "machines",
    "provider": "aws",
    "sortable": True,
    "default_visible": True,
    "lockable": False,
}

AWS_PRICE_DESCRIPTOR: dict[str, Any] = {
    "key": "aws_price_type",
    "path": "provider_data.price_type",
    "label": "Pricing",
    "kind": "badge",
    "resource_type": "machines",
    "provider": "aws",
    "sortable": False,
    "default_visible": False,
    "lockable": False,
    "badge_color_map": {"spot": "orange", "ondemand": "blue"},
}

AWS_REQUEST_DESCRIPTOR: dict[str, Any] = {
    "key": "aws_fleet_id",
    "path": "provider_data.fleet_id",
    "label": "Fleet ID",
    "kind": "code",
    "resource_type": "requests",
    "provider": "aws",
    "sortable": False,
    "default_visible": False,
    "lockable": False,
}

AZURE_MACHINE_DESCRIPTOR: dict[str, Any] = {
    "key": "azure_vm_size",
    "path": "provider_data.vm_size",
    "label": "VM Size",
    "kind": "text",
    "resource_type": "machines",
    "provider": "azure",
    "sortable": True,
    "default_visible": True,
    "lockable": False,
}

_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "aws": [AWS_MACHINE_DESCRIPTOR, AWS_PRICE_DESCRIPTOR, AWS_REQUEST_DESCRIPTOR],
    "azure": [AZURE_MACHINE_DESCRIPTOR],
}


# ---------------------------------------------------------------------------
# _dotted_get
# ---------------------------------------------------------------------------


class TestDottedGet:
    def _get_fn(self):
        from orb.ui.components.provider_columns import _dotted_get

        return _dotted_get

    def test_single_level(self):
        fn = self._get_fn()
        assert fn({"a": "x"}, "a") == "x"

    def test_two_levels(self):
        fn = self._get_fn()
        assert fn({"a": {"b": "deep"}}, "a.b") == "deep"

    def test_three_levels(self):
        fn = self._get_fn()
        assert fn({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_missing_segment_returns_empty_string(self):
        fn = self._get_fn()
        assert fn({}, "a.b.c") == ""

    def test_none_value_returns_empty_string(self):
        fn = self._get_fn()
        assert fn({"a": None}, "a") == ""

    def test_intermediate_non_dict_returns_empty_string(self):
        fn = self._get_fn()
        assert fn({"a": "not-a-dict"}, "a.b") == ""

    def test_empty_row(self):
        fn = self._get_fn()
        assert fn({}, "provider_data.instance_type") == ""


# ---------------------------------------------------------------------------
# build_provider_columns
# ---------------------------------------------------------------------------


class TestBuildProviderColumns:
    def _build(self, schemas, resource_type, active_provider):
        from orb.ui.components.provider_columns import build_provider_columns

        return build_provider_columns(schemas, resource_type, active_provider)

    def test_empty_schemas_returns_empty_list(self):
        result = self._build({}, "machines", None)
        assert result == []

    def test_filters_by_resource_type_machines(self):
        result = self._build(_SCHEMAS, "machines", None)
        keys = [c.key for c in result]
        assert "aws_instance_type" in keys
        assert "aws_price_type" in keys
        assert "aws_fleet_id" not in keys  # request-only

    def test_filters_by_resource_type_requests(self):
        result = self._build(_SCHEMAS, "requests", None)
        keys = [c.key for c in result]
        assert "aws_fleet_id" in keys
        assert "aws_instance_type" not in keys

    def test_all_provider_merges_aws_and_azure(self):
        result = self._build(_SCHEMAS, "machines", "All")
        keys = [c.key for c in result]
        assert "aws_instance_type" in keys
        assert "azure_vm_size" in keys

    def test_none_provider_merges_all(self):
        result = self._build(_SCHEMAS, "machines", None)
        keys = [c.key for c in result]
        assert "aws_instance_type" in keys
        assert "azure_vm_size" in keys

    def test_specific_provider_excludes_others(self):
        result = self._build(_SCHEMAS, "machines", "aws")
        keys = [c.key for c in result]
        assert "aws_instance_type" in keys
        assert "azure_vm_size" not in keys

    def test_azure_specific_only_azure(self):
        result = self._build(_SCHEMAS, "machines", "azure")
        keys = [c.key for c in result]
        assert "azure_vm_size" in keys
        assert "aws_instance_type" not in keys

    def test_deduplication_last_wins(self):
        """When two providers declare the same key the last one wins."""
        dup_schemas = {
            "prov_a": [
                {
                    "key": "shared_key",
                    "path": "a.val",
                    "label": "Label A",
                    "kind": "text",
                    "resource_type": "machines",
                }
            ],
            "prov_b": [
                {
                    "key": "shared_key",
                    "path": "b.val",
                    "label": "Label B",
                    "kind": "code",
                    "resource_type": "machines",
                }
            ],
        }
        result = self._build(dup_schemas, "machines", None)
        assert len(result) == 1
        assert result[0].key == "shared_key"
        # Last-wins: prov_b's label
        assert result[0].title == "Label B"

    def test_column_def_attributes_text(self):
        result = self._build({"aws": [AWS_MACHINE_DESCRIPTOR]}, "machines", None)
        assert len(result) == 1
        col = result[0]
        assert col.key == "aws_instance_type"
        assert col.title == "Instance Type"
        assert col.sortable is True
        assert col.default_visible is True
        assert col.lockable is False

    def test_column_def_attributes_badge(self):
        result = self._build({"aws": [AWS_PRICE_DESCRIPTOR]}, "machines", None)
        assert len(result) == 1
        col = result[0]
        assert col.key == "aws_price_type"
        assert col.sortable is False
        assert col.default_visible is False

    def test_unknown_provider_returns_empty(self):
        result = self._build(_SCHEMAS, "machines", "nonexistent")
        assert result == []

    def test_descriptors_with_missing_key_are_skipped(self):
        bad_schemas = {
            "prov": [
                {"path": "x.y", "label": "No key", "kind": "text", "resource_type": "machines"}
            ]
        }
        result = self._build(bad_schemas, "machines", None)
        assert result == []

    def test_non_list_descriptor_value_is_skipped(self):
        result = self._build({"aws": "not-a-list"}, "machines", None)  # type: ignore[arg-type]
        assert result == []


# ---------------------------------------------------------------------------
# resolve_provider_row_fields
# ---------------------------------------------------------------------------


class TestResolveProviderRowFields:
    def _resolve(self, row, schemas, resource_type, active_provider):
        from orb.ui.components.provider_columns import resolve_provider_row_fields

        return resolve_provider_row_fields(row, schemas, resource_type, active_provider)

    def test_empty_schemas_returns_empty_dict(self):
        result = self._resolve(
            {"provider_data": {"instance_type": "t3.medium"}}, {}, "machines", None
        )
        assert result == {}

    def test_extracts_dotted_path(self):
        row = {"provider_data": {"instance_type": "t3.medium"}}
        result = self._resolve(row, {"aws": [AWS_MACHINE_DESCRIPTOR]}, "machines", None)
        assert result.get("aws_instance_type") == "t3.medium"

    def test_missing_path_produces_empty_string(self):
        row = {"provider_data": {}}
        result = self._resolve(row, {"aws": [AWS_MACHINE_DESCRIPTOR]}, "machines", None)
        assert result.get("aws_instance_type") == ""

    def test_resource_type_filter(self):
        row = {"provider_data": {"instance_type": "t3"}}
        # Request descriptors should not appear when resolving for machines
        result = self._resolve(row, _SCHEMAS, "machines", None)
        assert "aws_fleet_id" not in result
        assert "aws_instance_type" in result

    def test_provider_filter_excludes_others(self):
        row = {"provider_data": {"instance_type": "t3", "vm_size": "Standard_D4"}}
        result = self._resolve(row, _SCHEMAS, "machines", "aws")
        assert "aws_instance_type" in result
        assert "azure_vm_size" not in result

    def test_all_provider_includes_all(self):
        row = {"provider_data": {"instance_type": "t3", "vm_size": "Standard_D4"}}
        result = self._resolve(row, _SCHEMAS, "machines", "All")
        assert "aws_instance_type" in result
        assert "azure_vm_size" in result

    def test_values_are_strings(self):
        row = {"provider_data": {"instance_type": 42}}
        result = self._resolve(row, {"aws": [AWS_MACHINE_DESCRIPTOR]}, "machines", None)
        assert isinstance(result.get("aws_instance_type"), str)
        assert result["aws_instance_type"] == "42"
