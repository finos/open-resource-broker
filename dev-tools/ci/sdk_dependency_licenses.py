#!/usr/bin/env python3
"""Check the runtime dependency licenses of the Java, Kotlin and C# SDKs.

Reads the committed lockfiles, looks up each runtime package's license in its
registry metadata, and evaluates the result with the same allowlist and SPDX
expression logic as `check_runtime_licenses.py` (the single source of truth).

Inputs
- Java / Kotlin: `gradle.lockfile`. Only lines whose configuration list
  contains exactly `runtimeClasspath` are runtime dependencies; test, compile-only
  and build-tool configurations are excluded.
- C#: `packages.lock.json`. `Direct`, `Transitive` and `CentralTransitive`
  entries are runtime dependencies; `Project` references are skipped.

Metadata
- Maven: the artifact's POM on Maven Central. Several `<license>` elements
  mean OR (Maven's dual-license convention), so they are joined with OR. A POM
  with no `<licenses>` inherits from its `<parent>`, which is walked up.
- NuGet: the `<license type="expression">` element of the package's nuspec.

Parsing needs defusedxml, installed in the job from
`.github/requirements/defusedxml.txt` (hash-locked).

Network egress is limited to `repo1.maven.org` and `api.nuget.org` over HTTPS,
including every redirect hop. XML with a DOCTYPE or entity declaration is refused.

Fail closed: a lockfile with no runtime packages, a package whose metadata
cannot be fetched or has no license, and any license string that does not
resolve to an allowed SPDX expression are all violations.

The resolved set (name, version, license) per SDK is printed and, when
`--summary` or `$GITHUB_STEP_SUMMARY` is set, appended there as markdown.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from defusedxml.ElementTree import ParseError, fromstring

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_runtime_licenses import check, to_spdx

MAVEN_CENTRAL = "https://repo1.maven.org/maven2"
NUGET_FLAT = "https://api.nuget.org/v3-flatcontainer"
ALLOWED_HOSTS = frozenset({"repo1.maven.org", "api.nuget.org"})
MAX_PARENT_DEPTH = 10
MAX_BYTES = 5_000_000
RUNTIME_NUGET_TYPES = frozenset({"Direct", "Transitive", "CentralTransitive"})
UNRESOLVED = "UNKNOWN"

Fetcher = Callable[[str], bytes]
Package = tuple[str, str]  # (name, version)

_SPDX_TOKEN_RE = re.compile(r"^[A-Za-z0-9.+-]+$")


def _require_allowed(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.netloc.lower() not in ALLOWED_HOSTS
    ):
        raise ValueError(f"refusing to fetch {url}")


class _AllowedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to an allowlisted HTTPS host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _require_allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_AllowedRedirects)


def fetch_url(url: str) -> bytes:
    """GET `url` over HTTPS from an allowlisted host, with retries and a size cap."""
    _require_allowed(url)
    last: Exception | None = None
    for attempt in range(3):
        try:
            with _OPENER.open(url, timeout=30) as resp:
                data = resp.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError(f"response from {url} exceeds {MAX_BYTES} bytes")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(2**attempt)
    raise RuntimeError(f"could not fetch {url}: {last}")


# ---------------------------------------------------------------------------
# Lockfiles
# ---------------------------------------------------------------------------


def parse_gradle_lockfile(path: Path) -> list[Package]:
    """Return sorted (group:artifact, version) for the runtimeClasspath only."""
    found: set[Package] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        coordinate, _, configurations = line.partition("=")
        if "runtimeClasspath" not in configurations.split(","):
            continue
        group_artifact, _, version = coordinate.rpartition(":")
        found.add((group_artifact, version))
    return sorted(found)


def parse_nuget_lockfile(path: Path) -> list[Package]:
    """Return sorted (package id, resolved version) across all target frameworks."""
    found: set[Package] = set()
    for packages in json.loads(path.read_text()).get("dependencies", {}).values():
        for name, info in packages.items():
            if info.get("type") in RUNTIME_NUGET_TYPES and info.get("resolved"):
                found.add((name, info["resolved"]))
    return sorted(found)


# ---------------------------------------------------------------------------
# Registry metadata
# ---------------------------------------------------------------------------


def parse_xml(data: bytes) -> Any:
    """Parse registry XML with defusedxml, refusing DTDs, entities and external refs.

    Registry metadata never needs them, and they are the vector for entity
    expansion attacks. Every failure surfaces as ValueError so the caller
    marks just that package unresolved.
    """
    try:
        return fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ParseError, LookupError) as exc:
        raise ValueError(f"malformed XML: {exc}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: Any, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name and child.text and child.text.strip():
            return child.text.strip()
    return None


def _pom_url(group: str, artifact: str, version: str) -> str:
    return (
        f"{MAVEN_CENTRAL}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"
    )


def pom_license_names(group: str, artifact: str, version: str, fetch: Fetcher) -> list[str]:
    """License names from a POM, walking `<parent>` until one declares licenses."""
    seen: set[tuple[str, str, str]] = set()
    for _ in range(MAX_PARENT_DEPTH):
        key = (group, artifact, version)
        if key in seen or "${" in version:
            return []
        seen.add(key)
        root = parse_xml(fetch(_pom_url(*key)))
        licenses = next((c for c in root if _local(c.tag) == "licenses"), None)
        names = [
            name
            for lic in (licenses if licenses is not None else [])
            if (name := _child_text(lic, "name"))
        ]
        if names:
            return names
        parent = next((c for c in root if _local(c.tag) == "parent"), None)
        if parent is None:
            return []
        group = _child_text(parent, "groupId") or ""
        artifact = _child_text(parent, "artifactId") or ""
        version = _child_text(parent, "version") or ""
        if not (group and artifact and version):
            return []
    return []


def to_expression(names: list[str]) -> str:
    """Join license names into one SPDX expression, with OR between them.

    Each name goes through the shared alias table. A name that is neither an
    alias nor a bare SPDX-style token is not trusted into the expression: it
    becomes an unallowed LicenseRef, so it can only fail, never inject syntax.
    """
    if not names:
        return UNRESOLVED
    parts = []
    for name in names:
        resolved = to_spdx(name)
        if resolved is name and not _SPDX_TOKEN_RE.match(name):
            resolved = "LicenseRef-unmapped"
        parts.append(resolved)
    if len(parts) == 1:
        return parts[0]
    return " OR ".join(f"({part})" for part in parts)


def maven_license(group_artifact: str, version: str, fetch: Fetcher) -> str:
    group, _, artifact = group_artifact.partition(":")
    try:
        return to_expression(pom_license_names(group, artifact, version, fetch))
    except (OSError, ValueError, RuntimeError):
        return UNRESOLVED


def nuget_license(name: str, version: str, fetch: Fetcher) -> str:
    lower = name.lower()
    url = f"{NUGET_FLAT}/{lower}/{version.lower()}/{lower}.nuspec"
    try:
        root = parse_xml(fetch(url))
    except (OSError, ValueError, RuntimeError):
        return UNRESOLVED
    for element in root.iter():
        if _local(element.tag) == "license" and element.get("type") == "expression":
            return (element.text or "").strip() or UNRESOLVED
    return UNRESOLVED


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def resolve(
    sdk: str, packages: list[Package], lookup: Callable[[str, str], str]
) -> list[dict[str, str]]:
    return [
        {"Name": name, "Version": version, "License": lookup(name, version), "SDK": sdk}
        for name, version in packages
    ]


def summary_markdown(sdk: str, rows: list[dict[str, str]], violations: set[str]) -> str:
    lines = [
        f"### {sdk} runtime dependency licenses",
        "",
        f"{len(rows)} package(s) resolved, {len(violations)} violation(s).",
        "",
        "| Package | Version | License | Allowed |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        verdict = "no" if row["Name"] in violations else "yes"
        lines.append(f"| {row['Name']} | {row['Version']} | {row['License']} | {verdict} |")
    return "\n".join(lines) + "\n"


def run(sdks: dict[str, tuple[str, Path]], fetch: Fetcher, summary: Path | None) -> int:
    """Check each SDK; `sdks` maps a label to (ecosystem, lockfile path)."""
    exit_code = 0
    for sdk, (ecosystem, lockfile) in sdks.items():
        if ecosystem == "maven":
            packages = parse_gradle_lockfile(lockfile)
            rows = resolve(sdk, packages, lambda n, v: maven_license(n, v, fetch))
        else:
            packages = parse_nuget_lockfile(lockfile)
            rows = resolve(sdk, packages, lambda n, v: nuget_license(n, v, fetch))
        if not rows:
            print(f"{sdk}: no runtime packages found in {lockfile}", file=sys.stderr)
            exit_code = 1
            continue
        _, violations = check(rows, ignore_packages=set())
        bad = {name for name, _ in violations}
        for name, license_str in violations:
            print(
                f"{sdk}: license {license_str!r} not in the FINOS runtime allowlist "
                f"for package {name}",
                file=sys.stderr,
            )
        report = summary_markdown(sdk, rows, bad)
        print(report)
        if summary:
            with summary.open("a") as handle:
                handle.write(report + "\n")
        if bad:
            exit_code = 1
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", type=Path, help="Java gradle.lockfile")
    parser.add_argument("--kotlin", type=Path, help="Kotlin gradle.lockfile")
    parser.add_argument("--csharp", type=Path, help="C# packages.lock.json")
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path(os.environ["GITHUB_STEP_SUMMARY"])
        if os.environ.get("GITHUB_STEP_SUMMARY")
        else None,
        help="Markdown file to append the resolved set to (default: $GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args()

    sdks: dict[str, tuple[str, Path]] = {}
    if args.java:
        sdks["Java"] = ("maven", args.java)
    if args.kotlin:
        sdks["Kotlin"] = ("maven", args.kotlin)
    if args.csharp:
        sdks["C#"] = ("nuget", args.csharp)
    if not sdks:
        parser.error("give at least one of --java, --kotlin, --csharp")
    return run(sdks, fetch_url, args.summary)


if __name__ == "__main__":
    sys.exit(main())
