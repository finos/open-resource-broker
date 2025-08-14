"""
Test CLI migration functionality.

This test verifies that the CLI migration from monolithic run.py
to modular CLI package is working correctly.
"""

import os
import sys

from src.cli.field_mapping import get_field_value, get_template_field_mapping

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestCLIMigration:
    """Test the CLI migration functionality."""

    def test_cli_modules_can_be_imported(self):
        """Test that all CLI modules can be imported successfully."""
        # Test main CLI module
        from src.cli.main import (
            convert_to_legacy_args,
            execute_command,
            main,
            parse_args,
        )

        assert callable(main)
        assert callable(parse_args)
        assert callable(execute_command)
        assert callable(convert_to_legacy_args)

        # Test formatters module
        from src.cli.formatters import (
            format_list_output,
            format_output,
            format_table_output,
        )

        assert callable(format_output)
        assert callable(format_table_output)
        assert callable(format_list_output)

        # Test completion module
        from src.cli.completion import generate_bash_completion, generate_zsh_completion

        assert callable(generate_bash_completion)
        assert callable(generate_zsh_completion)

        # Test field mapping module
        assert callable(get_field_value)
        assert callable(get_template_field_mapping)

    def test_shell_completion_generation(self):
        """Test that shell completions can be generated."""
        from src.cli.completion import generate_bash_completion, generate_zsh_completion

        bash_completion = generate_bash_completion()
        assert isinstance(bash_completion, str)
        assert "#!/bin/bash" in bash_completion
        assert "_ohfp_completion" in bash_completion

        zsh_completion = generate_zsh_completion()
        assert isinstance(zsh_completion, str)
        assert "#compdef ohfp" in zsh_completion
        assert "_ohfp" in zsh_completion

    def test_output_formatting(self):
        """Test that output formatting works correctly."""
        from src.cli.formatters import format_output

        test_data = {"test": "data", "number": 42}

        # Test JSON formatting
        json_output = format_output(test_data, "json")
        assert isinstance(json_output, str)
        assert "test" in json_output
        assert "42" in json_output

        # Test YAML formatting
        yaml_output = format_output(test_data, "yaml")
        assert isinstance(yaml_output, str)
        assert "test: data" in yaml_output

    def test_field_mapping_utilities(self):
        """Test field mapping utilities work correctly."""

        # Test field value extraction
        test_data = {"templateId": "test-123", "maxNumber": 5}
        field_mapping = {"template_id": ["templateId", "template_id"]}

        value = get_field_value(test_data, field_mapping, "template_id")
        assert value == "test-123"

        # Test template field mapping
        mapping = get_template_field_mapping()
        assert isinstance(mapping, dict)
        assert "id" in mapping  # The actual field name is 'id', not 'template_id'

    def test_run_py_is_minimal(self):
        """Test that run.py is now minimal and delegates to CLI modules."""
        run_py_path = os.path.join(project_root, "src", "run.py")

        with open(run_py_path, "r") as f:
            content = f.read()

        # Should be very short now
        lines = content.strip().split("\n")
        assert len(lines) < 30, f"run.py should be minimal, but has {len(lines)} lines"

        # Should import from CLI modules
        assert "from src.cli.main import main" in content

        # Should delegate to main()
        assert "main()" in content
