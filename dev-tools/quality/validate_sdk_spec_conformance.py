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

  3. Every step's ``sdk_methods.<lang>`` snippet names a method that actually
     exists on that language's hand-written client. This catches the class of
     bug where the fixture (and therefore the runtime parity runner that
     dispatches off it) references a client method that was renamed or removed
     — a drift the spec-only checks above cannot see, because the method name
     lives in the client source, not the OpenAPI document.

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

# ---------------------------------------------------------------------------
# Per-language client sources.  Each entry maps a fixture `sdk_methods` key to
# the hand-written client source file(s) whose method names must contain every
# method the scenario dispatches for that language.  The parity runner for each
# language dispatches off exactly these snippets, so a name that is not present
# in the client source is a real drift that would fail the runtime leg.
#
# `python` is intentionally absent from the fixture-driven set: there is no
# Python SDK in this repo, so any python snippet is documentation-only and is
# skipped rather than resolved.
# ---------------------------------------------------------------------------
CLIENT_SOURCES: dict[str, list[str]] = {
    "go": ["sdk/go/orb/client.go", "sdk/go/orb/client_ops.go"],
    "typescript": ["sdk/typescript/src/client.ts"],
    "java": ["sdk/java/src/main/java/org/finos/openresourcebroker/sdk/client/OrbClient.java"],
    "kotlin": ["sdk/kotlin/src/main/kotlin/org/finos/openresourcebroker/sdk/client/OrbClient.kt"],
    "csharp": ["sdk/csharp/src/FINOS.OpenResourceBroker/OrbClient.cs"],
}

# Languages that appear in the fixture but have no hand-written SDK in this repo
# and are therefore treated as documentation-only (not resolved to a method).
DOC_ONLY_LANGUAGES = {"python"}


def extract_method_name(lang: str, snippet: str) -> str | None:
    """Extract the invoked client method name from a fixture sdk_methods snippet.

    The snippets are illustrative call sites, e.g. ``await client.health()`` or
    ``c.RequestMachines(ctx, orb.RequestMachinesRequest{...})``.  We pull out the
    identifier that immediately follows the receiver (``client.``/``c.``) and
    precedes the argument list — that is the method whose existence we assert.
    Returns None when no method-call pattern is found (nothing to resolve).
    """
    # Match `<receiver>.<method>(` and capture <method>.  The receiver is any of
    # the conventional names used across the fixture (client / c).  This is
    # deliberately permissive on the receiver but strict on the shape.
    match = re.search(r"\b(?:client|c)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", snippet)
    if match:
        return match.group(1)
    return None


def method_defined_in_sources(lang: str, method: str, sources: list[str]) -> bool:
    """True if `method` is defined as a client method in any of `sources`.

    Uses a language-appropriate declaration pattern rather than a bare substring
    so a method mentioned only in a doc-comment or as a call site does not count
    as a definition.
    """
    if lang == "go":
        # func (c *Client) Health(   — exported method on the Client receiver.
        pattern = re.compile(
            r"func\s*\(\s*\w+\s*\*Client\s*\)\s*" + re.escape(method) + r"\s*\(",
        )
    elif lang == "typescript":
        # `async health(` or `health(` as a class member (optionally async).
        pattern = re.compile(
            r"(?:async\s+)?" + re.escape(method) + r"\s*\(",
        )
    elif lang == "java":
        # `public TemplateListResponse listTemplates(` — a public method decl.
        pattern = re.compile(
            r"public\s+[\w<>,.\[\]?\s]+\s+" + re.escape(method) + r"\s*\(",
        )
    elif lang == "kotlin":
        # `suspend fun health(` or `fun listTemplates(`.
        pattern = re.compile(
            r"fun\s+" + re.escape(method) + r"\s*\(",
        )
    elif lang == "csharp":
        # `public async Task<...> HealthAsync(` — a public method decl.
        pattern = re.compile(
            r"public\s+[\w<>,.\[\]?\s]+\s+" + re.escape(method) + r"\s*\(",
        )
    else:
        return False

    for source in sources:
        source_path = REPO_ROOT / source
        if not source_path.exists():
            continue
        if pattern.search(source_path.read_text(encoding="utf-8")):
            return True
    return False


def validate_sdk_methods(scenario_path: Path) -> list[str]:
    """Validate that each step's sdk_methods.<lang> resolves to a real client method.

    For every step and every language (except documentation-only languages), the
    snippet's invoked method name must be defined on that language's client.
    Returns error strings (empty when everything resolves).
    """
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    for step in scenario.get("steps", []):
        name = step.get("name", f"step {step.get('step', '?')}")
        sdk_methods = step.get("sdk_methods", {})
        if not sdk_methods:
            errors.append(f"[{name}] step has no sdk_methods mapping")
            continue

        for lang, snippet in sdk_methods.items():
            if lang in DOC_ONLY_LANGUAGES:
                continue
            if lang not in CLIENT_SOURCES:
                errors.append(
                    f"[{name}] sdk_methods has language '{lang}' with no known "
                    f"client source (add it to CLIENT_SOURCES or DOC_ONLY_LANGUAGES)"
                )
                continue

            method = extract_method_name(lang, snippet)
            if method is None:
                errors.append(
                    f"[{name}] sdk_methods.{lang} snippet has no recognisable "
                    f"client method call: {snippet!r}"
                )
                continue

            if not method_defined_in_sources(lang, method, CLIENT_SOURCES[lang]):
                errors.append(
                    f"[{name}] sdk_methods.{lang} calls '{method}(...)' but no such "
                    f"method is defined in {CLIENT_SOURCES[lang]}"
                )

    return errors


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
    method_errors = validate_sdk_methods(SCENARIO_PATH)
    all_errors = errors + method_errors

    if all_errors:
        logger.error(
            "SDK spec-conformance FAILED — parity scenario diverges from the spec/clients:"
        )
        for error in all_errors:
            logger.error("  - %s", error)
        return 1

    steps = json.loads(SCENARIO_PATH.read_text(encoding="utf-8")).get("steps", [])
    resolved_langs = sorted(set(CLIENT_SOURCES))
    logger.info(
        "SDK spec-conformance OK — all %s parity steps match the OpenAPI spec, "
        "and every sdk_methods snippet resolves to a real client method (%s).",
        len(steps),
        ", ".join(resolved_langs),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
