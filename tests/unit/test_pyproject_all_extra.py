"""Guard: the `all` extra must cover every runtime optional-dependency.

`all` is a hand-curated union of the runtime extras.  Nothing in packaging
enforces that a newly-added runtime extra is also wired into `all`, so it is
easy to add e.g. a new provider extra and silently leave `pip install
orb-py[all]` incomplete.  This test asserts `all` transitively includes every
runtime extra except an explicit denylist:

- test/tooling extras (`dev`, `test-*`) are not runtime features;
- `k8s-legacy` is the deprecated Symphony HostFactory plugin whose heavy legacy
  deps are intentionally kept out of `all`.

If this fails after adding an extra, either add it to `all` or, if it is
deliberately excluded, add it to `_DENYLIST` with a reason.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Extras that are intentionally NOT part of `all` (with rationale above).
_DENYLIST = frozenset(
    {
        "all",  # self
        "dev",  # dev tooling, not runtime
        "test-aws",  # test-only
        "test-k8s",  # test-only
        "k8s-legacy",  # deprecated legacy plugin, heavy deps kept out of `all`
    }
)


def _load_extras() -> dict[str, list[str]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def _resolve(name: str, extras: dict[str, list[str]], seen: set[str] | None = None) -> set[str]:
    """Return the set of concrete package names an extra pulls in, following
    ``orb-py[...]`` self-references transitively."""
    seen = seen if seen is not None else set()
    if name in seen:
        return set()
    seen.add(name)
    pkgs: set[str] = set()
    for dep in extras.get(name, []):
        if dep.startswith("orb-py["):
            inner = dep[dep.index("[") + 1 : dep.index("]")]
            for ref in inner.split(","):
                pkgs |= _resolve(ref.strip(), extras, seen)
        else:
            # Strip version/marker/sub-extra to get the bare distribution name.
            pkgs.add(dep.split(">")[0].split("=")[0].split("[")[0].split(";")[0].strip())
    return pkgs


def test_all_extra_covers_every_runtime_extra() -> None:
    extras = _load_extras()
    assert "all" in extras, "pyproject is missing the `all` extra"

    all_pkgs = _resolve("all", extras)
    runtime_extras = [name for name in extras if name not in _DENYLIST]

    missing: dict[str, list[str]] = {}
    for name in runtime_extras:
        gap = _resolve(name, extras) - all_pkgs
        if gap:
            missing[name] = sorted(gap)

    assert not missing, (
        "The `all` extra does not cover these runtime extras — add them to `all` "
        f"(or to _DENYLIST if deliberately excluded): {missing}"
    )


def test_denylisted_extras_still_exist() -> None:
    """Keep the denylist honest: every denylisted name (except `all` itself)
    must be a real extra, so a renamed/removed extra doesn't rot silently."""
    extras = _load_extras()
    for name in _DENYLIST - {"all"}:
        assert name in extras, f"_DENYLIST names '{name}', which is not an extra anymore"


if __name__ == "__main__":
    test_all_extra_covers_every_runtime_extra()
    test_denylisted_extras_still_exist()
    print("ok")
