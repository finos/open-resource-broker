#!/usr/bin/env python3
"""Static SDK spec-conformance check.

Validates, without a running server, that the cross-language artefacts stay in
lock-step with the hardened OpenAPI contract:

  1. Every step in ``sdk/parity/scenario.json`` references a (method, path,
     operationId) triple that actually exists in ``sdk/spec/openapi.json``.
     This is what wires the parity fixture into CI: a scenario that names an
     operation the spec does not define fails the build instead of silently
     drifting.

  2. Every operation the parity scenario exercises resolves to a real spec
     route, so a wrong verb (e.g. POST vs PUT) or a stale path template is
     caught statically — the exact class of bug that a hand-written client can
     otherwise ship green when its language has no live-orb contract leg.

Exit status is non-zero when any mismatch is found so the check can gate CI.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "sdk" / "spec" / "openapi.json"
SCENARIO_PATH = REPO_ROOT / "sdk" / "parity" / "scenario.json"

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def load_spec_operations(spec_path: Path) -> dict[str, dict[str, str]]:
    """Return {path_template: {METHOD: operationId}} from the OpenAPI spec."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    operations: dict[str, dict[str, str]] = {}
    for path_template, methods in spec.get("paths", {}).items():
        for method, definition in methods.items():
            if method.lower() not in HTTP_METHODS:
                continue
            operations.setdefault(path_template, {})[method.upper()] = definition.get(
                "operationId", ""
            )
    return operations


def _normalise_path(path: str) -> str:
    """Collapse path-parameter names so {request_id} == {requestId} == {id}."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def validate_scenario(scenario_path: Path, operations: dict[str, dict[str, str]]) -> list[str]:
    """Validate scenario steps against spec operations. Returns error strings."""
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    # Build a path-normalised lookup so {request_id} matches the spec template.
    normalised_ops: dict[str, dict[str, str]] = {}
    for path_template, methods in operations.items():
        normalised_ops.setdefault(_normalise_path(path_template), {}).update(methods)

    steps = scenario.get("steps", [])
    if not steps:
        errors.append("scenario.json declares no steps")

    for step in steps:
        name = step.get("name", f"step {step.get('step', '?')}")
        method = step.get("http_method", "").upper()
        path = step.get("http_path", "")
        operation_id = step.get("operation_id", "")

        norm_path = _normalise_path(path)
        methods_for_path = normalised_ops.get(norm_path)
        if methods_for_path is None:
            errors.append(f"[{name}] path '{path}' is not defined in the OpenAPI spec")
            continue

        spec_operation_id = methods_for_path.get(method)
        if spec_operation_id is None:
            errors.append(
                f"[{name}] method '{method}' not defined for path '{path}' "
                f"in the spec (available: {sorted(methods_for_path)})"
            )
            continue

        if operation_id and spec_operation_id and operation_id != spec_operation_id:
            errors.append(
                f"[{name}] operationId mismatch for {method} {path}: "
                f"scenario='{operation_id}' spec='{spec_operation_id}'"
            )

    return errors


def main() -> int:
    if not SPEC_PATH.exists():
        logger.error("Spec not found: %s", SPEC_PATH)
        return 1
    if not SCENARIO_PATH.exists():
        logger.error("Parity scenario not found: %s", SCENARIO_PATH)
        return 1

    operations = load_spec_operations(SPEC_PATH)
    logger.info(
        "Loaded %d spec operations across %d paths from %s",
        sum(len(m) for m in operations.values()),
        len(operations),
        SPEC_PATH.relative_to(REPO_ROOT),
    )

    errors = validate_scenario(SCENARIO_PATH, operations)
    if errors:
        logger.error("SDK spec-conformance FAILED — parity scenario diverges from the spec:")
        for error in errors:
            logger.error("  - %s", error)
        return 1

    logger.info(
        "SDK spec-conformance OK — all %s parity steps match the OpenAPI spec.",
        len(json.loads(SCENARIO_PATH.read_text(encoding="utf-8")).get("steps", [])),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
