#!/usr/bin/env python3
"""Fail when a Semgrep upgrade reports fewer results than the previous version.

Usage: semgrep_baseline_check.py BASELINE_SARIF CURRENT_SARIF

Both files come from scans of the same source tree: BASELINE from the Semgrep
locked on the base commit, CURRENT from the Semgrep locked on the pull request.
The raw SARIF is compared, so results silenced by ``# nosemgrep`` count too. A
dependency bump that silently disables rules or the ignore handling lowers the
count, and the job stays green because a scan with fewer findings passes.
"""

import json
import sys


def count_results(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        sarif = json.load(f)
    return sum(len(run.get("results") or []) for run in sarif["runs"])


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: semgrep_baseline_check.py BASELINE_SARIF CURRENT_SARIF", file=sys.stderr)
        return 2
    try:
        baseline, current = (count_results(p) for p in argv)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read SARIF results: {exc}", file=sys.stderr)
        return 1
    print(f"Semgrep results: baseline {baseline}, current {current}")
    if current < baseline:
        print(
            "The updated Semgrep reports fewer results than the previous version on the "
            "same source. Check that its rules and ignore handling still work before merging.",
            file=sys.stderr,
        )
        return 1
    if baseline == 0:
        print("Baseline found no results, so this check could not detect a drop.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
