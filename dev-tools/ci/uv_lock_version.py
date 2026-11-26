#!/usr/bin/env python3
"""Print the version a package resolves to in uv.lock.

Usage: uv_lock_version.py PACKAGE [LOCKFILE]

Exits 1 when the package is absent, has no version, or resolves to more than
one distinct version (a cache key must name exactly one). Needs Python 3.11+
for tomllib.
"""

import re
import sys

try:
    import tomllib
except ImportError:  # pragma: no cover - interpreter older than 3.11
    sys.exit("tomllib is unavailable: run with Python 3.11 or newer")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_versions(package: str, lock_path: str = "uv.lock") -> set[str]:
    with open(lock_path, "rb") as f:
        lock = tomllib.load(f)
    wanted = _normalize(package)
    return {
        str(p.get("version", ""))
        for p in lock.get("package", [])
        if _normalize(str(p.get("name", ""))) == wanted
    }


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    package = argv[1]
    versions = lock_versions(package, *argv[2:])
    if not versions or "" in versions:
        print(f"{package}: no version found in the lockfile", file=sys.stderr)
        return 1
    if len(versions) > 1:
        print(f"{package}: multiple versions in the lockfile: {sorted(versions)}", file=sys.stderr)
        return 1
    print(next(iter(versions)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
