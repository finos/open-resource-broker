"""Unit tests for orb.cli.args_extractor.ArgsExtractor.

All tests build a real argparse.Namespace so we test actual attribute logic,
not Mock magic.
"""

from __future__ import annotations

import argparse

import pytest

from orb.cli.args_extractor import ArgsExtractor


def _ns(**kwargs) -> argparse.Namespace:
    """Build a Namespace with the supplied keyword arguments."""
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.unit
class TestExtractTemplateId:
    def test_returns_template_id_attribute_when_set(self):
        extractor = ArgsExtractor(_ns(template_id="tmpl-123"))
        assert extractor.extract_template_id() == "tmpl-123"

    def test_falls_back_to_template_ids_list(self):
        extractor = ArgsExtractor(_ns(template_ids=["tmpl-456", "tmpl-789"]))
        assert extractor.extract_template_id() == "tmpl-456"

    def test_returns_none_when_no_template_info(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_template_id() is None

    def test_template_id_none_with_ids_list_uses_list(self):
        extractor = ArgsExtractor(_ns(template_id=None, template_ids=["tmpl-x"]))
        assert extractor.extract_template_id() == "tmpl-x"

    def test_empty_template_ids_returns_none(self):
        extractor = ArgsExtractor(_ns(template_ids=[]))
        assert extractor.extract_template_id() is None


@pytest.mark.unit
class TestExtractRequestIds:
    def test_single_request_id_string(self):
        extractor = ArgsExtractor(_ns(request_id="req-1"))
        result = extractor.extract_request_ids()
        assert "req-1" in result

    def test_request_id_as_list(self):
        extractor = ArgsExtractor(_ns(request_id=["req-a", "req-b"]))
        result = extractor.extract_request_ids()
        assert set(result) == {"req-a", "req-b"}

    def test_request_ids_positional(self):
        extractor = ArgsExtractor(_ns(request_ids=["req-c", "req-d"]))
        result = extractor.extract_request_ids()
        assert "req-c" in result
        assert "req-d" in result

    def test_deduplication(self):
        extractor = ArgsExtractor(_ns(request_id="req-x", request_ids=["req-x", "req-y"]))
        result = extractor.extract_request_ids()
        assert result.count("req-x") == 1
        assert "req-y" in result

    def test_empty_when_no_ids(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_request_ids() == []


@pytest.mark.unit
class TestExtractMachineId:
    def test_returns_machine_id_attribute(self):
        extractor = ArgsExtractor(_ns(machine_id="m-001"))
        assert extractor.extract_machine_id() == "m-001"

    def test_falls_back_to_machine_ids_list(self):
        extractor = ArgsExtractor(_ns(machine_ids=["m-002", "m-003"]))
        assert extractor.extract_machine_id() == "m-002"

    def test_returns_none_when_absent(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_machine_id() is None

    def test_machine_id_none_uses_machine_ids_list(self):
        extractor = ArgsExtractor(_ns(machine_id=None, machine_ids=["m-005"]))
        assert extractor.extract_machine_id() == "m-005"


@pytest.mark.unit
class TestExtractProviderApi:
    def test_returns_provider_api_when_set(self):
        extractor = ArgsExtractor(_ns(provider_api="EC2Fleet"))
        assert extractor.extract_provider_api() == "EC2Fleet"

    def test_returns_none_when_absent(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_provider_api() is None


@pytest.mark.unit
class TestExtractCount:
    def test_returns_count_when_set(self):
        extractor = ArgsExtractor(_ns(count=5))
        assert extractor.extract_count() == 5

    def test_default_when_count_absent(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_count() == 1

    def test_custom_default_when_count_absent(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_count(default=3) == 3

    def test_count_none_uses_default(self):
        extractor = ArgsExtractor(_ns(count=None))
        assert extractor.extract_count(default=7) == 7

    def test_falls_back_to_second_positional_arg(self):
        extractor = ArgsExtractor(_ns(template_ids=["tmpl", "10"]))
        assert extractor.extract_count() == 10

    def test_non_numeric_second_positional_uses_default(self):
        extractor = ArgsExtractor(_ns(template_ids=["tmpl", "not-a-number"]))
        assert extractor.extract_count() == 1


@pytest.mark.unit
class TestExtractMetadata:
    def test_empty_metadata_when_no_attrs(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_metadata() == {}

    def test_dry_run_true_adds_key(self):
        extractor = ArgsExtractor(_ns(dry_run=True))
        result = extractor.extract_metadata()
        assert result["dry_run"] is True

    def test_dry_run_false_excluded(self):
        extractor = ArgsExtractor(_ns(dry_run=False))
        result = extractor.extract_metadata()
        assert "dry_run" not in result

    def test_metadata_key_value_string(self):
        extractor = ArgsExtractor(_ns(metadata=["env=staging"]))
        result = extractor.extract_metadata()
        assert result["env"] == "staging"

    def test_metadata_integer_value_parsed(self):
        extractor = ArgsExtractor(_ns(metadata=["count=3"]))
        result = extractor.extract_metadata()
        assert result["count"] == 3

    def test_metadata_bool_true_parsed(self):
        extractor = ArgsExtractor(_ns(metadata=["enabled=true"]))
        result = extractor.extract_metadata()
        assert result["enabled"] is True

    def test_metadata_bool_false_parsed(self):
        extractor = ArgsExtractor(_ns(metadata=["active=false"]))
        result = extractor.extract_metadata()
        assert result["active"] is False

    def test_metadata_multiple_items(self):
        extractor = ArgsExtractor(_ns(metadata=["a=1", "b=hello"]))
        result = extractor.extract_metadata()
        assert result["a"] == 1
        assert result["b"] == "hello"

    def test_metadata_item_without_equals_ignored(self):
        extractor = ArgsExtractor(_ns(metadata=["no-equals"]))
        result = extractor.extract_metadata()
        assert "no-equals" not in result


@pytest.mark.unit
class TestExtractFilePath:
    def test_returns_file_path(self):
        extractor = ArgsExtractor(_ns(file="/tmp/config.json"))
        assert extractor.extract_file_path() == "/tmp/config.json"

    def test_returns_none_when_absent(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_file_path() is None


@pytest.mark.unit
class TestExtractOutputFormat:
    def test_returns_format_when_set(self):
        extractor = ArgsExtractor(_ns(format="yaml"))
        assert extractor.extract_output_format() == "yaml"

    def test_returns_default_table_when_absent(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_output_format() == "table"

    def test_custom_default(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.extract_output_format(default="json") == "json"


@pytest.mark.unit
class TestHasFlag:
    def test_true_when_flag_set(self):
        extractor = ArgsExtractor(_ns(verbose=True))
        assert extractor.has_flag("verbose") is True

    def test_false_when_flag_not_set(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.has_flag("verbose") is False

    def test_false_when_flag_explicitly_false(self):
        extractor = ArgsExtractor(_ns(dry_run=False))
        assert extractor.has_flag("dry_run") is False


@pytest.mark.unit
class TestGetValue:
    def test_returns_attribute_value(self):
        extractor = ArgsExtractor(_ns(limit=10))
        assert extractor.get_value("limit") == 10

    def test_returns_default_when_absent(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.get_value("limit", default=50) == 50

    def test_none_default(self):
        extractor = ArgsExtractor(_ns())
        assert extractor.get_value("nonexistent") is None
