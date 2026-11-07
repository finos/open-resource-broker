#!/usr/bin/env python3
"""Drop accepted suppressed results from a SARIF file.

Usage: filter_sarif_suppressions.py IN OUT

Semgrep honours ``# nosemgrep`` comments when deciding its exit code, but still
writes the suppressed results into its SARIF output, so GitHub code scanning
keeps them open. This removes every result that has at least one suppression
whose ``state`` is absent or ``"accepted"`` (SARIF 2.1.0: an absent ``state``
means accepted) and keeps all other results, including those whose
suppressions are ``rejected`` or ``underReview``.

IN is never modified. Exits non-zero, without writing OUT, when IN cannot be
read, is not valid JSON, or has no ``runs`` array.
"""

import json
import sys


def _is_suppressed(result: dict) -> bool:
    return any(s.get("state", "accepted") == "accepted" for s in result.get("suppressions") or [])


def filter_sarif(sarif: dict) -> dict:
    for run in sarif["runs"]:
        if "results" in run:
            run["results"] = [r for r in run["results"] if not _is_suppressed(r)]
    return sarif


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: filter_sarif_suppressions.py IN OUT", file=sys.stderr)
        return 2
    src, dst = argv
    try:
        with open(src, encoding="utf-8") as f:
            sarif = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"cannot read SARIF from {src}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(sarif, dict) or not isinstance(sarif.get("runs"), list):
        print(f"{src} has no SARIF runs array", file=sys.stderr)
        return 1
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(filter_sarif(sarif), f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
