#!/usr/bin/env python3
"""Run OCI continuity pytest snapshot and save a structured report."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PASS_RE = re.compile(r"(?P<passed>\d+)\s+passed")


def _run(cmd: list[str], timeout: int = 1200) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    out = proc.stdout.strip()
    err = proc.stderr.strip()
    passed = None
    m = PASS_RE.search(out)
    if m:
        passed = int(m.group("passed"))
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "passed": passed,
        "stdout": out,
        "stderr": err,
        "ok": proc.returncode == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OCI continuity pytest slices")
    parser.add_argument("--python-bin", default="python", help="Python executable")
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="Optional report path (default data/oci_continuity_snapshot_<timestamp>.json)",
    )
    args = parser.parse_args()

    commands = [
        [
            args.python_bin,
            "-m",
            "pytest",
            "-q",
            "tests/unit/providers/oci",
        ],
        [
            args.python_bin,
            "-m",
            "pytest",
            "-q",
            "tests/unit/providers/oci/test_registration.py",
            "tests/unit/interface/test_provider_config_handler.py",
            "tests/unit/cli/test_args_contract.py",
        ],
    ]

    results = [_run(cmd) for cmd in commands]
    decision = "go" if all(r["ok"] for r in results) else "hold"

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "decision": decision,
    }

    report_file = args.report_file
    if report_file is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = Path("data") / f"oci_continuity_snapshot_{ts}.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Decision: {decision}")
    print(f"Report written: {report_file}")
    for item in results:
        cmd = " ".join(item["command"])
        status = "OK" if item["ok"] else "FAIL"
        passed = item["passed"]
        passed_text = f"{passed} passed" if passed is not None else "pass-count unavailable"
        print(f"- {status}: {cmd} ({passed_text})")

    return 0 if decision == "go" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
