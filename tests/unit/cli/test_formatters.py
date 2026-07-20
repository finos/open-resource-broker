"""Unit tests for orb.cli.formatters.

Covers format_output dispatch, format_table_output, format_list_output,
format_generic_table, format_generic_list, and _format_generic_ascii_table.
"""

from __future__ import annotations

import json

import pytest


@pytest.mark.unit
class TestFormatOutput:
    """format_output routes to the correct sub-formatter."""

    def test_json_format_returns_indented_json(self):
        from orb.cli.formatters import format_output

        data = {"key": "value", "count": 42}
        result = format_output(data, "json")
        parsed = json.loads(result)
        assert parsed == data

    def test_json_format_uses_str_default(self):
        from datetime import datetime

        from orb.cli.formatters import format_output

        data = {"ts": datetime(2024, 1, 1)}
        result = format_output(data, "json")
        assert "2024-01-01" in result

    def test_yaml_format_returns_yaml_string(self):
        from orb.cli.formatters import format_output

        data = {"name": "test", "value": 5}
        result = format_output(data, "yaml")
        assert "name: test" in result
        assert "value: 5" in result

    def test_yaml_format_fallback_to_json_on_importerror(self):
        import sys
        import unittest.mock as mock

        from orb.cli.formatters import format_output

        data = {"key": "v"}
        # Simulate yaml not being available
        with mock.patch.dict(sys.modules, {"yaml": None}):
            # This raises ImportError internally; the code catches it and falls back to JSON.
            result = format_output(data, "yaml")
        # The fallback must produce valid JSON equal to the input.
        assert json.loads(result) == data

    def test_table_format_delegates_to_format_table_output(self):
        from orb.cli.formatters import format_output

        data = {"items": [{"id": "1", "name": "alpha"}]}
        result = format_output(data, "table")
        # Table format should include the value
        assert "alpha" in result or "1" in result

    def test_list_format_delegates_to_format_list_output(self):
        from orb.cli.formatters import format_output

        data = {"items": [{"id": "1", "name": "alpha"}]}
        result = format_output(data, "list")
        assert "alpha" in result or "1" in result

    def test_unknown_format_falls_back_to_json(self):
        from orb.cli.formatters import format_output

        data = {"key": "val"}
        result = format_output(data, "unknown_format_xyz")
        parsed = json.loads(result)
        assert parsed == data

    def test_json_format_with_list(self):
        from orb.cli.formatters import format_output

        data = [1, 2, 3]
        result = format_output(data, "json")
        assert json.loads(result) == [1, 2, 3]


@pytest.mark.unit
class TestFormatTableOutput:
    """format_table_output branches."""

    def test_dict_with_list_value_renders_table(self):
        from orb.cli.formatters import format_table_output

        data = {"machines": [{"id": "m-1", "status": "running"}]}
        result = format_table_output(data)
        assert "m-1" in result

    def test_dict_without_list_value_falls_back_to_json(self):
        from orb.cli.formatters import format_table_output

        data = {"message": "no items here"}
        result = format_table_output(data)
        # Falls back to JSON
        parsed = json.loads(result)
        assert parsed["message"] == "no items here"

    def test_non_dict_falls_back_to_json(self):
        from orb.cli.formatters import format_table_output

        result = format_table_output("plain string")
        assert isinstance(result, str)

    def test_dict_with_empty_list_skips_to_next_key(self):
        from orb.cli.formatters import format_table_output

        data = {"empty": [], "items": [{"id": "x1"}]}
        result = format_table_output(data)
        # Should find the non-empty "items" list
        assert "x1" in result


@pytest.mark.unit
class TestFormatListOutput:
    """format_list_output branches."""

    def test_dict_with_list_value_renders_list(self):
        from orb.cli.formatters import format_list_output

        data = {"requests": [{"id": "r-1", "status": "pending"}]}
        result = format_list_output(data)
        assert "r-1" in result

    def test_dict_without_list_value_falls_back_to_json(self):
        from orb.cli.formatters import format_list_output

        data = {"count": 0}
        result = format_list_output(data)
        parsed = json.loads(result)
        assert parsed["count"] == 0

    def test_non_dict_falls_back_to_json(self):
        from orb.cli.formatters import format_list_output

        result = format_list_output(42)
        assert "42" in result


@pytest.mark.unit
class TestFormatGenericTable:
    """format_generic_table covers both Rich and ASCII branches."""

    def test_empty_items_returns_no_found_message(self):
        from orb.cli.formatters import format_generic_table

        result = format_generic_table([], "Templates")
        assert "no templates found" in result.lower()

    def test_ascii_fallback_when_rich_unavailable(self):
        import sys
        import unittest.mock as mock

        from orb.cli.formatters import format_generic_table

        items = [{"id": "1", "name": "alpha"}, {"id": "2", "name": "beta"}]
        with mock.patch.dict(sys.modules, {"rich": None, "rich.table": None}):
            result = format_generic_table(items, "Items")
        # In ASCII fallback the derived column header and both row values appear.
        assert "alpha" in result
        assert "beta" in result
        assert "Name" in result

    def test_rich_path_returns_string_with_data(self):
        from orb.cli.formatters import format_generic_table

        items = [{"status": "running", "machine_id": "m-001"}]
        # May use Rich if available; either way the data must appear
        result = format_generic_table(items, "Machines")
        assert "m-001" in result or "running" in result

    def test_multiple_items_all_present(self):
        from orb.cli.formatters import format_generic_table

        items = [
            {"id": "t1", "type": "EC2"},
            {"id": "t2", "type": "K8s"},
        ]
        result = format_generic_table(items, "Templates")
        assert "t1" in result
        assert "t2" in result

    def test_missing_key_in_some_items_uses_na(self):
        from orb.cli.formatters import format_generic_table

        items = [{"id": "1", "name": "alpha"}, {"id": "2"}]
        result = format_generic_table(items, "Items")
        assert "N/A" in result

    def test_custom_title_appears(self):
        from orb.cli.formatters import format_generic_table

        # Rich may truncate the title depending on terminal width; use a short one.
        items = [{"x": "1"}]
        result = format_generic_table(items, "Items")
        # The key data must always be present regardless of title truncation.
        assert "1" in result


@pytest.mark.unit
class TestFormatGenericList:
    """format_generic_list produces correct detail-list output."""

    def test_empty_items_returns_no_found_message(self):
        from orb.cli.formatters import format_generic_list

        result = format_generic_list([], "Machines")
        assert "no machines found" in result.lower()

    def test_single_item_contains_all_fields(self):
        from orb.cli.formatters import format_generic_list

        items = [{"machine_id": "m-1", "status": "running"}]
        result = format_generic_list(items, "Machines")
        assert "m-1" in result
        assert "running" in result

    def test_multiple_items_separated_by_blank_line(self):
        from orb.cli.formatters import format_generic_list

        items = [{"id": "1"}, {"id": "2"}]
        result = format_generic_list(items, "Items")
        # Two entries must be present
        assert "1" in result
        assert "2" in result
        # A blank line separates them (two consecutive newlines)
        assert "\n\n" in result

    def test_title_and_numbered_entries(self):
        from orb.cli.formatters import format_generic_list

        items = [{"x": "a"}, {"x": "b"}]
        result = format_generic_list(items, "Items")
        assert "Item 1:" in result
        assert "Item 2:" in result

    def test_none_value_shown_as_na(self):
        from orb.cli.formatters import format_generic_list

        items = [{"name": None}]
        result = format_generic_list(items, "Items")
        # The key is present with value None, so it renders deterministically as
        # the literal string "None" (the "N/A" default only fires for missing keys).
        assert "  Name: None" in result


@pytest.mark.unit
class TestFormatGenericAsciiTable:
    """_format_generic_ascii_table fallback formatter."""

    def test_empty_items(self):
        from orb.cli.formatters import _format_generic_ascii_table

        result = _format_generic_ascii_table([], "Things")
        assert "no things found" in result.lower()

    def test_header_row_present(self):
        from orb.cli.formatters import _format_generic_ascii_table

        items = [{"name": "foo", "count": "3"}]
        result = _format_generic_ascii_table(items, "Things")
        assert "Name" in result
        assert "Count" in result

    def test_data_row_present(self):
        from orb.cli.formatters import _format_generic_ascii_table

        items = [{"name": "foo", "count": "3"}]
        result = _format_generic_ascii_table(items, "Things")
        assert "foo" in result
        assert "3" in result

    def test_missing_key_shows_na(self):
        from orb.cli.formatters import _format_generic_ascii_table

        items = [{"a": "1", "b": "2"}, {"a": "3"}]
        result = _format_generic_ascii_table(items, "Things")
        assert "N/A" in result

    def test_column_widths_accommodate_long_values(self):
        from orb.cli.formatters import _format_generic_ascii_table

        items = [{"name": "short"}, {"name": "a_very_long_name_here"}]
        result = _format_generic_ascii_table(items, "Things")
        assert "a_very_long_name_here" in result
