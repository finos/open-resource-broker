"""Guard: the ``dev`` extra (pip shim) must cover every package in the ``dev``
dependency-group (PEP 735).

The [dependency-groups] section is the authoritative list of CI/dev
dependencies.  The [project.optional-dependencies] ``dev`` entry is a pip
compatibility shim that must mirror the full resolved set.  This test catches
the drift where someone adds a package to a group but forgets to also add it
to the pip shim.

Resolution rules:
- Dependency-group entries are either plain requirement strings or
  ``{include-group = "name"}`` dicts — resolve recursively.
- Optional-dependency entries may be plain requirement strings or
  ``orb-py[extra,...]`` self-references — skip self-references on both sides
  so they never cause a false mismatch (e.g. the ``typecheck`` group contains
  ``orb-py[all]`` for runtime deps; those are not part of the dev toolchain).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomllib  # available via backport in Python 3.10

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(req: str) -> str:
    """Return the bare, normalised distribution name for a requirement string.

    Strips version specifiers (>=, ==, <, !=), extras ([...]), environment
    markers (;...), and whitespace.  Normalises PEP 503 name: lower-case and
    collapse ``-``, ``_``, ``.`` to ``_`` so ``PyYAML`` == ``pyyaml`` ==
    ``py_yaml``.
    """
    # Take only the package name part (before any specifier or bracket)
    bare = re.split(r"[\[>=<!;]", req)[0].strip()
    return bare.lower().replace("-", "_").replace(".", "_")


def _is_self_ref(req: str) -> bool:
    """Return True if *req* is an ``orb-py[...]`` self-reference."""
    return req.startswith("orb-py")


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def _resolve_group(name: str, groups: dict, seen: set[str] | None = None) -> set[str]:
    """Recursively resolve a dependency-group to a set of normalised package names.

    Skips ``orb-py`` self-references (e.g. ``orb-py[all]`` in the
    ``typecheck`` group provides runtime deps, not dev toolchain packages).
    """
    seen = seen if seen is not None else set()
    if name in seen:
        return set()
    seen.add(name)

    pkgs: set[str] = set()
    for entry in groups.get(name, []):
        if isinstance(entry, dict):
            sub = entry.get("include-group")
            if sub:
                pkgs |= _resolve_group(sub, groups, seen)
        else:
            req = str(entry)
            if _is_self_ref(req):
                # Skip orb-py self-references — they pull in runtime deps,
                # not dev-toolchain packages, and would cause false mismatches.
                continue
            pkgs.add(_normalize(req))
    return pkgs


def _resolve_extra(name: str, extras: dict, seen: set[str] | None = None) -> set[str]:
    """Recursively resolve an optional-dependency extra to normalised package names.

    Follows ``orb-py[sub-extra]`` self-references transitively, so that
    ``orb-py[all]`` in the ``dev`` extra expands to the runtime packages it
    provides (fastapi, uvicorn, opentelemetry-*, …).  This matters because the
    ``test`` dependency-group lists those runtime deps as literals; on the extra
    side pip resolves them via ``[all]`` rather than re-listing them, and the
    resolver must mirror that to compare like-for-like.
    """
    seen = seen if seen is not None else set()
    if name in seen:
        return set()
    seen.add(name)

    pkgs: set[str] = set()
    for dep in extras.get(name, []):
        if dep.startswith("orb-py["):
            inner = dep[dep.index("[") + 1 : dep.index("]")]
            for ref in inner.split(","):
                pkgs |= _resolve_extra(ref.strip(), extras, seen)
        else:
            pkgs.add(_normalize(dep))
    return pkgs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dev_extra_covers_dev_group() -> None:
    """The ``dev`` optional-dependency extra must be a superset of the
    concrete packages resolved from the ``dev`` dependency-group.

    If this fails, a package was added to a dependency-group sub-group but
    the ``dev`` pip shim was not updated to match.  Add the missing package(s)
    to the ``dev`` entry in ``[project.optional-dependencies]``.
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    groups = data["dependency-groups"]
    extras = data["project"]["optional-dependencies"]

    assert "dev" in groups, "pyproject.toml is missing the 'dev' dependency-group"
    assert "dev" in extras, "pyproject.toml is missing the 'dev' optional-dependency"

    group_pkgs = _resolve_group("dev", groups)
    extra_pkgs = _resolve_extra("dev", extras)

    missing = group_pkgs - extra_pkgs
    assert not missing, (
        "The 'dev' optional-dependency shim is missing packages that are in "
        "the 'dev' dependency-group.  Add these to [project.optional-dependencies] dev:\n"
        + "\n".join(f"  {p}" for p in sorted(missing))
    )


def test_dev_group_exists_and_includes_ci() -> None:
    """Basic structural check: ``dev`` group must exist and include the ``ci`` group."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    groups = data["dependency-groups"]

    assert "dev" in groups, "Missing 'dev' dependency-group"
    assert "ci" in groups, "Missing 'ci' dependency-group"

    included = {
        entry["include-group"]
        for entry in groups["dev"]
        if isinstance(entry, dict) and "include-group" in entry
    }
    assert "ci" in included, "The 'dev' dependency-group no longer includes 'ci'"


if __name__ == "__main__":
    test_dev_extra_covers_dev_group()
    test_dev_group_exists_and_includes_ci()
    print("ok")
