#!/usr/bin/env python3
"""Test to verify ListMachinesQuery handler registration."""

import subprocess
import sys
import tempfile
import os


def test_list_machines_handler_registration():
    """Test that ListMachinesQuery handler is properly registered without warnings."""

    # Create a temporary test environment
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up minimal config for testing
        config_path = os.path.join(temp_dir, "config.json")
        with open(config_path, "w") as f:
            f.write('{"storage": {"strategy": "json", "path": "' + temp_dir + '"}}')

        # Set environment variable to use test config
        env = os.environ.copy()
        env["ORB_CONFIG_PATH"] = config_path

        # Run orb machines list and capture output
        result = subprocess.run(
            [sys.executable, "-m", "orb_py.run", "machines", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd="/Users/flamurg/src/aws/symphony/open-resource-broker",
            check=False,
        )

        # Check that no handler registration warnings appear
        stderr_output = result.stderr.lower()

        # This should fail initially due to the handler mismatch
        assert "no handler registered" not in stderr_output, (
            f"Handler registration warning found: {result.stderr}"
        )
        assert "machinelistquery" not in stderr_output, (
            f"MachineListQuery warning found: {result.stderr}"
        )

        print(f"Command exit code: {result.returncode}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")


if __name__ == "__main__":
    test_list_machines_handler_registration()
