#!/usr/bin/env python3
"""Check whether security exceptions now have a published upstream fix.

Reads every vulnerability ID suppressed in `.trivyignore.yaml` and every ID in
the `allow-ghsas` list of `.github/workflows/security-code.yml`, queries the
OSV API (https://api.osv.dev) for each one, and reports any whose affected
package now has a `fixed` version event.

The package ecosystem of each `.trivyignore.yaml` entry comes from its purls.
Debian OS-package CVEs (`pkg:deb/...`) are not covered by the generic CVE-ID
OSV record, so they are looked up under Debian's own `DEBIAN-<CVE-ID>` record
instead. PyPI CVEs (`pkg:pypi/...`) are looked up through the advisories OSV
links to the CVE and only fixes for the named PyPI packages count. For
Debian, a `fixed` event in that record's affected entry for the Debian release the
Dockerfile actually ships (see DEBIAN_ECOSYSTEM below) is treated as a fix
being available. A fix in a different Debian release (e.g. the next,
unstable release) is not actionable for this image and is ignored. IDs with
no matching OSV record are skipped silently.

A PyPI fix is only reported when it can be installed on the oldest Python the
project supports (`requires-python` in pyproject.toml): a fixed release whose
PyPI `requires_python` has a lower bound above that minimum is not actionable.
If PyPI cannot be queried or the metadata cannot be parsed, the fix is reported.

With --dry-run, findings are printed and no GitHub issue is created or
updated.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIVYIGNORE_PATH = REPO_ROOT / ".trivyignore.yaml"
SECURITY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "security-code.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
OSV_API_BASE = "https://api.osv.dev/v1/vulns/"
PYPI_API_BASE = "https://pypi.org/pypi/"
REQUEST_TIMEOUT_SECONDS = 15

# The only vulnerability ID shapes this script ever queries OSV for: CVE,
# GHSA (GitHub's fixed-width base32-like alphabet, 4-4-4), the
# DEBIAN-<CVE-ID> records Debian publishes, and PyPI advisories (PYSEC).
# query_osv() refuses to build a request URL for anything that doesn't
# match, since the ID ultimately comes from parsing local config files.
VULN_ID_RE = re.compile(
    r"^(CVE-\d{4}-\d+|GHSA(-[23456789cfghjmpqrvwx]{4}){3}|DEBIAN-CVE-\d{4}-\d+|PYSEC-\d{4}-\d+)$"
)

# The Dockerfile builds on python:<version>-slim, which is Debian 13
# (trixie) for every supported Python version. A fix published for a
# different Debian release (e.g. Debian:14/forky, still unstable) is not
# actionable here, so only this exact OSV ecosystem counts as "fixed".
DEBIAN_ECOSYSTEM = "Debian:13"


def load_trivyignore_entries(path: Path) -> list[tuple[str, list[str]]]:
    """Extract (vulnerability ID, purls) pairs from `.trivyignore.yaml`."""
    if not path.exists():
        return []

    entries: list[tuple[str, list[str]]] = []
    in_vulnerabilities = False
    in_purls = False
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        is_top_level_key = not line[:1].isspace()
        if is_top_level_key:
            in_vulnerabilities = line.strip() == "vulnerabilities:"
            in_purls = False
            continue
        if not in_vulnerabilities:
            continue
        match = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if match:
            entries.append((match.group(1), []))
            in_purls = False
            continue
        if entries and re.match(r"\s*purls:\s*$", line):
            in_purls = True
            continue
        purl = re.match(r"\s*-\s*(pkg:\S+)", line)
        if entries and in_purls and purl:
            entries[-1][1].append(purl.group(1))
        elif not line.lstrip().startswith("#"):
            in_purls = False
    return entries


def load_trivyignore_ids(path: Path) -> list[str]:
    """Extract vulnerability IDs from `.trivyignore.yaml` under `vulnerabilities:`."""
    return [vuln_id for vuln_id, _ in load_trivyignore_entries(path)]


_BLOCK_SCALAR_INDICATOR_RE = re.compile(r"^[>|][+-]?\d*$")


def load_allow_ghsas(path: Path) -> list[str]:
    """Extract GHSA IDs from the `allow-ghsas` dependency-review input.

    Handles an inline value and block scalars (`>`, `>-`, `|`, `|-`) as well as
    plain multi-line values: every line indented deeper than the key belongs to
    the value.
    """
    if not path.exists():
        return []

    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(?:-\s+)?allow-ghsas:\s*(.*)$", line)
        if not match:
            continue
        key_indent = len(match.group(1))
        inline = re.sub(r"\s+#.*$", "", match.group(2)).strip()
        parts = [] if _BLOCK_SCALAR_INDICATOR_RE.match(inline) else [inline]
        for continuation in lines[index + 1 :]:
            if (
                continuation.strip()
                and len(continuation) - len(continuation.lstrip()) <= key_indent
            ):
                break
            if continuation.strip():
                parts.append(continuation.strip())
        value = " ".join(parts).strip("'\"")
        return [v.strip("'\"") for v in re.split(r"[,\s]+", value) if v.strip("'\"")]
    return []


def _build_https_only_opener() -> urllib.request.OpenerDirector:
    """Return an opener that only speaks HTTPS.

    build_opener() would add the default file:// and ftp:// handlers; building
    the director by hand means a non-HTTPS URL cannot be opened at all.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


_OSV_OPENER = _build_https_only_opener()


def query_osv(vuln_id: str) -> dict | None:
    """Fetch an OSV vulnerability record by ID, or None if it does not exist.

    The ID is validated against VULN_ID_RE and percent-encoded before being
    appended to the fixed https:// OSV_API_BASE, and requests go through an
    HTTPS-only opener, so the final URL can never resolve to a file:// path or
    another unexpected scheme even though it is built from a value that
    ultimately comes from local config files.
    """
    if not VULN_ID_RE.match(vuln_id):
        print(f"skipping {vuln_id!r}: not a recognised OSV vulnerability ID", file=sys.stderr)
        return None

    url = OSV_API_BASE + urllib.parse.quote(vuln_id, safe="-")
    if not url.startswith(OSV_API_BASE):
        raise ValueError(f"refusing to request unexpected URL {url!r}")

    try:
        with _OSV_OPENER.open(
            urllib.request.Request(url), timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


_PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PYPI_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")


def python_lower_bound(specifier: str | None) -> tuple[int, int] | None:
    """Return the highest `>=X[.Y]` lower bound in a requires-python specifier."""
    bounds = [
        (int(major), int(minor or 0))
        for major, minor in re.findall(r">=\s*(\d+)(?:\.(\d+))?", specifier or "")
    ]
    return max(bounds) if bounds else None


def project_minimum_python(path: Path = PYPROJECT_PATH) -> tuple[int, int] | None:
    """Read the lower bound of `requires-python` from pyproject.toml, if any."""
    try:
        text = path.read_text()
    except OSError:
        return None
    match = re.search(r'^requires-python\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return python_lower_bound(match.group(1)) if match else None


@lru_cache(maxsize=None)
def fetch_requires_python(name: str, version: str) -> str | None:
    """Return a PyPI release's `requires_python`, or None when it cannot be determined.

    The name and version are validated and percent-encoded before being appended
    to the fixed https:// PYPI_API_BASE, and the request goes through the same
    HTTPS-only opener as OSV lookups.
    """
    if not _PYPI_NAME_RE.match(name) or not _PYPI_VERSION_RE.match(version):
        return None
    url = (
        f"{PYPI_API_BASE}{urllib.parse.quote(name, safe='')}/"
        f"{urllib.parse.quote(version, safe='')}/json"
    )
    if not url.startswith(PYPI_API_BASE):
        return None
    try:
        with _OSV_OPENER.open(
            urllib.request.Request(url), timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            requires_python = json.loads(response.read()).get("info", {}).get("requires_python")
    except (OSError, ValueError, AttributeError):
        return None
    return requires_python if isinstance(requires_python, str) else None


def needs_newer_python(name: str, version: str) -> bool:
    """True when a PyPI release cannot be installed on the project's minimum Python."""
    minimum = project_minimum_python()
    if minimum is None:
        return False
    bound = python_lower_bound(fetch_requires_python(name, version))
    return bound is not None and bound > minimum


def _normalise_package_name(name: str) -> str:
    """PEP 503 name normalisation, so `Foo_Bar` and `foo-bar` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def purl_ecosystem_and_name(purl: str) -> tuple[str, str]:
    """Return the (kind, package name) of a purl, e.g. ("deb", "libacl1")."""
    match = re.match(r"^pkg:([a-z0-9.+-]+)/(?:[^/@?#]+/)*([^/@?#]+)", purl)
    if not match:
        return "", ""
    return match.group(1), urllib.parse.unquote(match.group(2))


def find_fixes(
    record: dict,
    *,
    debian_only: bool = False,
    ecosystem: str | None = None,
    package_names: frozenset[str] | None = None,
) -> list[tuple[str, str]]:
    """Return (package, fixed_version) pairs for any 'fixed' event in the record.

    For Debian records, only the DEBIAN_ECOSYSTEM affected entry is
    considered: a fix in a different Debian release is not actionable for
    this image's base image. ``ecosystem`` restricts the result to one OSV
    ecosystem and ``package_names`` (PEP 503 normalised) to those packages.
    """
    if debian_only:
        ecosystem = DEBIAN_ECOSYSTEM
    fixes = []
    for affected in record.get("affected", []):
        package = affected.get("package", {})
        if ecosystem is not None and package.get("ecosystem", "") != ecosystem:
            continue
        name = package.get("name", "unknown")
        if package_names is not None and _normalise_package_name(name) not in package_names:
            continue
        for value_range in affected.get("ranges", []):
            for event in value_range.get("events", []):
                if "fixed" not in event:
                    continue
                if package.get("ecosystem") == "PyPI" and needs_newer_python(name, event["fixed"]):
                    continue
                fixes.append((name, event["fixed"]))
    return fixes


def _linked_advisory_ids(record: dict) -> list[str]:
    """IDs of the non-CVE advisories OSV links to a record (GHSA, PYSEC)."""
    linked = [*record.get("aliases", []), *record.get("related", [])]
    return [
        linked_id
        for linked_id in dict.fromkeys(linked)
        if VULN_ID_RE.match(linked_id) and not linked_id.upper().startswith(("CVE-", "DEBIAN-"))
    ]


def check_exception(vuln_id: str, purls: list[str] | None = None) -> list[tuple[str, str]]:
    """Look up a single exception ID and return any fixes OSV now reports.

    The ecosystem comes from the entry's purls. Debian OS-package CVEs are not
    merged into the generic CVE-ID record OSV aggregates from the CVE List
    (that record only tracks the upstream GIT history, not Debian's
    package-level fix status). Debian publishes its own per-distro records
    instead, under the deterministic ID `DEBIAN-<CVE-ID>`, so only entries
    with a `pkg:deb` purl are queried that way. A PyPI CVE is looked up
    through the PyPI advisories OSV links to it, counting fixes for the
    purl's packages only. Anything else (a GHSA, or a CVE with no purl) is
    looked up under its own ID.
    """
    kinds = {purl_ecosystem_and_name(purl) for purl in purls or []}
    is_cve = vuln_id.upper().startswith("CVE-")
    if is_cve and any(kind == "deb" for kind, _ in kinds):
        record = query_osv(f"DEBIAN-{vuln_id}")
        return [] if record is None else find_fixes(record, debian_only=True)

    pypi_names = frozenset(_normalise_package_name(name) for kind, name in kinds if kind == "pypi")
    if is_cve and pypi_names:
        cve_record = query_osv(vuln_id)
        if cve_record is None:
            return []
        fixes = find_fixes(cve_record, ecosystem="PyPI", package_names=pypi_names)
        for advisory_id in _linked_advisory_ids(cve_record):
            advisory = query_osv(advisory_id)
            if advisory is not None:
                fixes.extend(find_fixes(advisory, ecosystem="PyPI", package_names=pypi_names))
        return list(dict.fromkeys(fixes))

    record = query_osv(vuln_id)
    return [] if record is None else find_fixes(record)


def issue_title(vuln_id: str) -> str:
    return f"Security exception {vuln_id} now has a fix"


def issue_body(vuln_id: str, fixes: list[tuple[str, str]]) -> str:
    lines = [
        f"OSV reports a fix for the package(s) covered by the `{vuln_id}` security exception:",
        "",
    ]
    lines.extend(f"- `{package}` fixed in `{fixed_version}`" for package, fixed_version in fixes)
    lines.append("")
    lines.append(
        "Re-evaluate the exception in `.trivyignore.yaml` or "
        "`.github/workflows/security-code.yml` and remove it if it is no "
        "longer needed."
    )
    return "\n".join(lines)


def ensure_issue(vuln_id: str, fixes: list[tuple[str, str]], *, dry_run: bool) -> None:
    """Create a new issue, or update the existing one with the same title."""
    title = issue_title(vuln_id)
    body = issue_body(vuln_id, fixes)

    if dry_run:
        print(f"[dry-run] would create/update issue: {title}")
        print(body)
        return

    existing = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--search", title, "--json", "number,title"],
        capture_output=True,
        text=True,
        check=True,
    )
    matches = [item for item in json.loads(existing.stdout or "[]") if item["title"] == title]
    if matches:
        subprocess.run(
            ["gh", "issue", "edit", str(matches[0]["number"]), "--body", body], check=True
        )
    else:
        subprocess.run(["gh", "issue", "create", "--title", title, "--body", body], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print findings without creating or updating GitHub issues",
    )
    args = parser.parse_args()

    purls_by_id: dict[str, list[str]] = {}
    for vuln_id, purls in load_trivyignore_entries(TRIVYIGNORE_PATH):
        purls_by_id.setdefault(vuln_id, []).extend(purls)
    for vuln_id in load_allow_ghsas(SECURITY_WORKFLOW_PATH):
        purls_by_id.setdefault(vuln_id, [])
    ids = sorted(purls_by_id)
    if not ids:
        print("No security exceptions found to check.")
        return 0

    print(f"Checking {len(ids)} security exception(s): {', '.join(ids)}")
    for vuln_id in ids:
        fixes = check_exception(vuln_id, purls_by_id[vuln_id])
        if fixes:
            print(f"{vuln_id}: fix available -> {fixes}")
            ensure_issue(vuln_id, fixes, dry_run=args.dry_run)
        else:
            print(f"{vuln_id}: no fix found")

    return 0


if __name__ == "__main__":
    sys.exit(main())
