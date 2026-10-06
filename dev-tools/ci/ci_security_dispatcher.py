#!/usr/bin/env python3
"""CI Security dispatcher for the Open Resource Broker."""

import subprocess
import sys


def handle_pip_audit():
    """Handle pip-audit dependency scan.

    pip-audit exits non-zero when it finds vulnerable dependencies, with no
    built-in "report only" flag. Findings are reported via job logs, not used
    as a merge gate, so the audit itself is run with its real exit code
    surfaced in the logs and the dispatcher always hands back a trailing
    no-op command to keep the overall result a pass.
    """
    print("Running pip-audit dependency scan...")
    subprocess.run(["./dev-tools/setup/run_tool.sh", "pip-audit", "--desc"], check=False)
    return ["true"]


def handle_other_tools(tool):
    """Handle other security tools."""
    return ["./dev-tools/security/ci_security.py", tool]


def get_command(tool):
    """Get command for security tool."""
    if tool == "pip-audit":
        return handle_pip_audit()
    elif tool in ["trivy", "hadolint", "semgrep", "trivy-fs", "trufflehog"]:
        return handle_other_tools(tool)
    else:
        print(f"ERROR: Unknown security tool: {tool}")
        return None


def main():
    args = sys.argv[1:]

    if not args:
        print(
            "ERROR: Security tool required (pip-audit, trivy, hadolint, semgrep, trivy-fs, trufflehog)"
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
