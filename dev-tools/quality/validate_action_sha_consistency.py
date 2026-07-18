#!/usr/bin/env python3
"""Assert every ``action@version`` is pinned to the SAME commit SHA repo-wide.

Workflows pin third-party actions to a full commit SHA with a ``# vX`` version
comment, e.g.::

    uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020  # v4

If two workflows pin the same ``action`` + version comment to *different* SHAs,
one of them is stale (or, worse, invalid) — a class of bug that only surfaces at
release time when the job fails at action resolution.  This check parses every
``.github/workflows/*.yml`` file and fails when a given (action, version-comment)
pair maps to more than one SHA.

Runs offline (no network).  Does not validate that the SHA exists upstream — it
only enforces internal consistency.
"""

from __future__ import annotations

import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# uses: owner/repo(/subpath)?@<40-hex-sha>  # <version comment>
USES_RE = re.compile(
    r"uses:\s*(?P<action>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-/]+)@"
    r"(?P<sha>[0-9a-f]{40})\s*#\s*(?P<comment>.+?)\s*$"
)


def scan() -> dict[tuple[str, str], dict[str, list[str]]]:
    """Return {(action, comment): {sha: [file:line, ...]}}."""
    findings: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_RE.search(line)
            if not match:
                continue
            key = (match.group("action"), match.group("comment"))
            findings[key][match.group("sha")].append(f"{wf.name}:{lineno}")
    return findings


def main() -> int:
    if not WORKFLOWS_DIR.is_dir():
        logger.error("Workflows directory not found: %s", WORKFLOWS_DIR)
        return 1

    findings = scan()
    errors = 0
    for (action, comment), sha_map in sorted(findings.items()):
        if len(sha_map) > 1:
            errors += 1
            logger.error(
                "Inconsistent pin for %s (# %s) — %d distinct SHAs:",
                action,
                comment,
                len(sha_map),
            )
            for sha, locations in sorted(sha_map.items()):
                logger.error("    %s  <- %s", sha, ", ".join(locations))

    if errors:
        logger.error(
            "\nAction SHA consistency FAILED: %d action@version pin(s) diverge.",
            errors,
        )
        logger.error("Reconcile every workflow to a single verified SHA for each action@version.")
        return 1

    logger.info(
        "Action SHA consistency OK — %d distinct action@version pins, all internally consistent.",
        len(findings),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
