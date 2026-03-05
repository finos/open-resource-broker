#!/usr/bin/env python3
"""CI Security dispatcher for the Open Resource Broker."""

import subprocess
import sys


def handle_bandit():
    """Handle bandit security scan."""
    print("Running Bandit security scan...")
    return ["./dev-tools/setup/run_tool.sh", "bandit", "-r", "src", "--exit-zero"]


def handle_safety():
    """Handle safety dependency scan."""
    print("Running Safety dependency scan...")
    return ["./dev-tools/setup/run_tool.sh", "safety", "check"]


def handle_other_tools(tool):
    """Handle other security tools."""
    return ["./dev-tools/security/ci_security.py", tool]


def get_command(tool):
    """Get command for security tool."""
    if tool == "bandit":
        return handle_bandit()
    elif tool == "safety":
        return handle_safety()
    elif tool in ["trivy", "hadolint", "semgrep", "trivy-fs", "trufflehog"]:
        return handle_other_tools(tool)
    else:
        print(f"ERROR: Unknown security tool: {tool}")
        return None


def main():
    args = sys.argv[1:]

    if not args:
        print(
            "ERROR: Security tool required (bandit, safety, trivy, hadolint, semgrep, trivy-fs, trufflehog)"
        )
        return 1

    cmd = get_command(args[0])
    if not cmd:
        return 1

    try:
        result = subprocess.run(cmd, check=True)
        return result.returncode
    except subprocess.CalledProcessError as e:
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())
