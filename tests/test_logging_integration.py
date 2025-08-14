"""
Integration test for logging fixes - tests actual CLI execution.

This test runs the actual CLI command and verifies that the logging output
matches our expectations and doesn't contain any of the issues we fixed.
"""

import os
import re
import subprocess

import pytest

# Use existing test fixtures


class TestLoggingIntegration:
    """Integration tests for logging fixes using actual CLI execution."""

    def _get_test_env(self, complete_test_environment):
        """Get environment variables for subprocess execution."""
        env = os.environ.copy()
        env.update(
            {
                "HF_PROVIDER_CONFDIR": str(complete_test_environment),
                "AWS_ACCESS_KEY_ID": "testing",
                "AWS_SECRET_ACCESS_KEY": "testing",
                "AWS_SECURITY_TOKEN": "testing",
                "AWS_SESSION_TOKEN": "testing",
                "AWS_DEFAULT_REGION": "us-east-1",
                "ENVIRONMENT": "testing",
                "TESTING": "true",
                "MOTO_CALL_RESET_API": "false",  # Prevent moto from resetting
            }
        )
        return env

    def _run_cli_command(self, complete_test_environment, command=None):
        """Run CLI command with proper test environment and dry-run mode."""
        if command is None:
            command = ["templates", "list"]

        env = self._get_test_env(complete_test_environment)

        # Use sys.executable to get the current Python interpreter
        import sys

        python_executable = sys.executable

        # Add --dry-run flag before the subcommand (global argument)
        command_with_dry_run = [
            python_executable,
            "-m",
            "src.cli.main",
            "--dry-run",
        ] + command

        return subprocess.run(
            command_with_dry_run, capture_output=True, text=True, timeout=30, env=env
        )

    def test_templates_list_logging_output(self, complete_test_environment):
        """Test that 'templates list' command produces clean logging output."""
        # Run the actual CLI command with test environment
        result = self._run_cli_command(complete_test_environment)

        # Command should succeed with test environment
        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"

        # Parse the stderr output (where logs go)
        log_lines = result.stderr.strip().split("\n") if result.stderr.strip() else []

        # Verify no health check errors
        health_check_errors = [
            line for line in log_lines if "Error checking health of strategy" in line
        ]
        assert len(
            health_check_errors) == 0, f"Found health check errors: {health_check_errors}"

        # Verify no duplicate SSM parameter resolution
        ssm_resolution_lines = [
            line for line in log_lines if "Resolved SSM parameter" in line and "to AMI" in line
        ]

        # Should have exactly one SSM resolution log (no duplicates)
        assert (
            len(ssm_resolution_lines) <= 1
        ), f"Found duplicate SSM resolution: {ssm_resolution_lines}"

        # Verify provider mode is correctly detected
        provider_mode_lines = [
            line for line in log_lines if "Final provider mode:" in line]
        if provider_mode_lines:
            # Should show 'single' for test config (not 'unknown')
            assert any(
                "single" in line for line in provider_mode_lines
            ), f"Provider mode not correctly detected: {provider_mode_lines}"
            assert not any(
                "unknown" in line for line in provider_mode_lines
            ), f"Provider mode shows 'unknown': {provider_mode_lines}"

        # Verify provider names are correctly reported
        provider_names_lines = [
            line for line in log_lines if "Active provider names:" in line]
        if provider_names_lines:
            # Should contain the test provider instance
            provider_names_line = provider_names_lines[0]
            assert (
                "aws-test" in provider_names_line
            ), f"Missing aws-test provider: {provider_names_line}"

        # Verify no template preloading logs
        preload_lines = [line for line in log_lines if "Preloading templates" in line]
        assert len(
            preload_lines) == 0, f"Found template preloading logs: {preload_lines}"

        # Verify batch resolution log is present and correct
        batch_resolution_lines = [
            line
            for line in log_lines
            if "Batch resolved" in line and "unique SSM parameters" in line
        ]
        if batch_resolution_lines:
            # Should mention resolving 1 unique parameter for multiple templates
            batch_line = batch_resolution_lines[0]
            assert (
                "1 unique SSM parameters" in batch_line
            ), f"Batch resolution doesn't show correct count: {batch_line}"
            assert (
                "9 templates" in batch_line
            ), f"Batch resolution doesn't show correct template count: {batch_line}"

    def test_no_critical_errors_in_output(self, complete_test_environment):
        """Test that CLI execution doesn't produce any critical errors."""
        # Run the actual CLI command with test environment
        result = self._run_cli_command(complete_test_environment)

        # Command should succeed with test environment
        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"

        # Parse the stderr output (where logs go)
        log_output = result.stderr if result.stderr else ""

        # List of critical error patterns that should NOT appear
        critical_error_patterns = [
            r"Error checking health of strategy.*'aws'",
            r"Failed to initialize.*provider",
            r"Provider context not available",
            r"KeyError.*aws",
            r"AttributeError.*strategy",
            r"Exception.*health check",
            r"Traceback.*most recent call last",
        ]

        # Check for critical errors
        found_errors = []
        for pattern in critical_error_patterns:
            if re.search(pattern, log_output, re.IGNORECASE):
                found_errors.append(pattern)

        assert (
            len(found_errors) == 0
        ), f"Found critical errors in log output: {found_errors}\nFull log output:\n{log_output}"

    def test_json_output_structure(self, complete_test_environment):
        """Test that the JSON output has the expected structure."""
        # Run the actual CLI command with test environment
        result = self._run_cli_command(complete_test_environment)

        # Command should succeed with test environment
        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"

        # Parse JSON output
        import json

        try:
            output_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON output: {e}\nOutput: {result.stdout}")

        # Verify JSON structure
        assert "success" in output_data, "Missing 'success' field in output"
        assert output_data["success"] is True, "Command did not succeed"

        assert "templates" in output_data, "Missing 'templates' field in output"
        assert isinstance(output_data["templates"], list), "Templates should be a list"

        assert "total_count" in output_data, "Missing 'total_count' field in output"
        # With proper scheduler config, we should now have templates
        assert output_data["total_count"] > 0, "Should have templates from test fixtures"

        # Verify template structure
        assert len(output_data["templates"]) > 0, "Should have at least one template"
        template = output_data["templates"][0]
        required_fields = ["templateId", "imageId", "vmType", "maxNumber"]
        for field in required_fields:
            assert field in template, f"Missing required field '{field}' in template"

        # Verify AMI ID is resolved (not SSM parameter)
        assert template["imageId"].startswith(
            "ami-"), f"AMI ID not resolved: {template['imageId']}"

        # Verify we're getting the test template
        template_ids = [t["templateId"] for t in output_data["templates"]]
        assert (
            "test-template-1" in template_ids or "test-template-2" in template_ids
        ), f"Should contain test templates, got: {template_ids}"

    def test_logging_performance(self, complete_test_environment):
        """Test that logging doesn't significantly impact performance."""
        import time

        # Run the command and measure time with test environment
        start_time = time.time()
        result = self._run_cli_command(complete_test_environment)
        end_time = time.time()

        execution_time = end_time - start_time

        # Command should succeed with test environment
        assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"

        # Should complete within reasonable time (30 seconds is generous)
        assert execution_time < 30, f"Command took too long: {execution_time:.2f} seconds"

        # Parse log output to count log messages
        log_lines = result.stderr.strip().split("\n") if result.stderr.strip() else []

        # Should have reasonable number of log messages (dry-run mode produces more logs)
        assert (
            len(log_lines) < 150
        ), f"Too many log messages ({len(log_lines)}), possible logging spam"

        # Should have some log messages (not silent)
        assert (
            len(log_lines) > 10
        ), f"Too few log messages ({len(log_lines)}), possible logging issues"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
