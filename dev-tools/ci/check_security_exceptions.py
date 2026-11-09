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

Every package name and version that reaches an issue body or a proposed change
first passes a per-ecosystem validator (full match, length capped). An entry
that fails validation, or that is not a Debian entry, is reported as a manual
case with a fixed reason code, and nothing from the OSV record is echoed.

With --dry-run, findings are printed and no GitHub issue is created or
updated.

With --apply, the fixed Debian entries are removed from `.trivyignore.yaml` and
a pull request is opened (see security_exception_fixes.py). Add --dry-run to
print the plan and diff only.
"""

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIVYIGNORE_PATH = REPO_ROOT / ".trivyignore.yaml"
SECURITY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "security-code.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
REPO_SLUG = "finos/open-resource-broker"
AUDIT_ISSUE_AUTHOR = "app/github-actions"
OSV_API_BASE = "https://api.osv.dev/v1/vulns/"
PYPI_API_BASE = "https://pypi.org/pypi/"
REQUEST_TIMEOUT_SECONDS = 15

# The only vulnerability ID shapes this script ever queries OSV for: CVE,
# GHSA (GitHub's fixed-width base32-like alphabet, 4-4-4), the
# DEBIAN-<CVE-ID> records Debian publishes, and PyPI advisories (PYSEC).
# query_osv() refuses to build a request URL for anything that doesn't
# match, since the ID ultimately comes from parsing local config files.
#
# The pattern ends in \Z rather than $ (which also accepts a trailing newline)
# and callers go through is_valid_vuln_id(), which adds a length cap.
MAX_TOKEN_LENGTH = 100
VULN_ID_RE = re.compile(
    r"^(CVE-\d{4}-\d+|GHSA(-[23456789cfghjmpqrvwx]{4}){3}|DEBIAN-CVE-\d{4}-\d+|PYSEC-\d{4}-\d+)\Z"
)


def is_valid_vuln_id(value: object) -> bool:
    """True for a string that fully matches VULN_ID_RE and is at most 100 characters."""
    return (
        isinstance(value, str)
        and len(value) <= MAX_TOKEN_LENGTH
        and VULN_ID_RE.fullmatch(value) is not None
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
    if not is_valid_vuln_id(vuln_id):
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


_PYPI_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PYPI_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!-]*")


_LOWER_BOUND_CLAUSE_RE = re.compile(r"^(?:>=|~=|===|==|>)\s*(\d+)(?:\.(\d+|\*))?")


def python_lower_bound(specifier: str | None) -> tuple[int, int] | None:
    """Return the highest lower bound implied by a requires-python specifier.

    `>=`, `~=`, `==` (including `.*` wildcards), `===` and `>` clauses each imply
    a minimum (major, minor); `<`, `<=` and `!=` clauses imply none. A missing
    minor component counts as 0.
    """
    bounds: list[tuple[int, int]] = []
    for clause in (specifier or "").split(","):
        match = _LOWER_BOUND_CLAUSE_RE.match(clause.strip())
        if match:
            major, minor = match.groups()
            bounds.append((int(major), 0 if minor in (None, "*") else int(minor)))
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
    if not _PYPI_NAME_RE.fullmatch(name) or not _PYPI_VERSION_RE.fullmatch(version):
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
        if is_valid_vuln_id(linked_id) and not linked_id.upper().startswith(("CVE-", "DEBIAN-"))
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


# Per-ecosystem validators. Every package name and version that is printed in
# an issue, a pull request or a commit passes one of these first. They use
# fullmatch (a trailing newline never passes) and a length cap, so a hostile
# OSV record cannot smuggle markdown, mentions or extra lines into the text.
_DEB_PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9+.\-]*")
_DEB_VERSION_RE = re.compile(r"[0-9][A-Za-z0-9.+~:\-]*")
_PYPI_NORMALISED_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
# Used only when the `packaging` library is not importable.
_PYPI_FALLBACK_VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+!\-]*")


def valid_deb_package(name: object) -> bool:
    return (
        isinstance(name, str)
        and len(name) <= MAX_TOKEN_LENGTH
        and _DEB_PACKAGE_RE.fullmatch(name) is not None
    )


def valid_deb_version(version: object) -> bool:
    return (
        isinstance(version, str)
        and len(version) <= MAX_TOKEN_LENGTH
        and _DEB_VERSION_RE.fullmatch(version) is not None
    )


def valid_pypi_package(name: object) -> bool:
    if not isinstance(name, str) or len(name) > MAX_TOKEN_LENGTH:
        return False
    return _PYPI_NORMALISED_NAME_RE.fullmatch(_normalise_package_name(name)) is not None


def valid_pypi_version(version: object) -> bool:
    if not isinstance(version, str) or not version or len(version) > MAX_TOKEN_LENGTH:
        return False
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return _PYPI_FALLBACK_VERSION_RE.fullmatch(version) is not None
    try:
        return str(Version(version)) == version
    except InvalidVersion:
        return False


class ManualReason(str, Enum):
    """Why an entry cannot be changed mechanically. Values are fixed strings."""

    GHSA_ALLOWLIST = "GHSA_ALLOWLIST"
    NON_DEB_ECOSYSTEM = "NON_DEB_ECOSYSTEM"
    INVALID_PACKAGE = "INVALID_PACKAGE"
    INVALID_VERSION = "INVALID_VERSION"
    NO_PURL = "NO_PURL"


@dataclass(frozen=True)
class Proposal:
    """A classified exception with a published fix.

    `fixes` holds only (package, version) pairs that passed validation and
    `deb_packages` only validated Debian binary package names, so everything
    here is safe to print. `reason` is None for a mechanical (Debian) entry.
    """

    vuln_id: str
    fixes: tuple[tuple[str, str], ...]
    deb_packages: tuple[str, ...]
    reason: ManualReason | None

    @property
    def mechanical(self) -> bool:
        return self.reason is None


def classify_exception(
    vuln_id: str,
    purls: list[str],
    fixes: list[tuple[str, str]],
    *,
    in_trivyignore: bool = True,
) -> Proposal:
    """Classify an exception that has a fix as mechanical (Debian) or manual."""
    if not is_valid_vuln_id(vuln_id):
        raise ValueError("refusing to classify an invalid vulnerability ID")
    kinds = {purl_ecosystem_and_name(purl) for purl in purls}
    ecosystems = {kind for kind, _ in kinds}
    validate_package, validate_version = (
        (valid_deb_package, valid_deb_version)
        if ecosystems == {"deb"}
        else (valid_pypi_package, valid_pypi_version)
    )
    shown_fixes = tuple(
        (package, version)
        for package, version in dict.fromkeys(fixes)
        if validate_package(package) and validate_version(version)
    )
    deb_packages = tuple(
        dict.fromkeys(
            name for kind, name in sorted(kinds) if kind == "deb" and valid_deb_package(name)
        )
    )

    def proposal(reason: ManualReason | None) -> Proposal:
        return Proposal(vuln_id, shown_fixes, deb_packages, reason)

    if not in_trivyignore or not vuln_id.startswith("CVE-"):
        return proposal(ManualReason.GHSA_ALLOWLIST)
    if not purls or "" in ecosystems:
        return proposal(ManualReason.NO_PURL)
    if ecosystems != {"deb"}:
        if any(not validate_package(pkg) for pkg, _ in fixes):
            return proposal(ManualReason.INVALID_PACKAGE)
        if any(not validate_version(ver) for _, ver in fixes):
            return proposal(ManualReason.INVALID_VERSION)
        return proposal(ManualReason.NON_DEB_ECOSYSTEM)
    if any(not valid_deb_package(name) for kind, name in kinds if kind == "deb"):
        return proposal(ManualReason.INVALID_PACKAGE)
    if any(not valid_deb_package(pkg) for pkg, _ in fixes):
        return proposal(ManualReason.INVALID_PACKAGE)
    if any(not valid_deb_version(ver) for _, ver in fixes):
        return proposal(ManualReason.INVALID_VERSION)
    return proposal(None)


def plan_exceptions(
    purls_by_id: dict[str, list[str]],
    trivyignore_ids: set[str],
    lookup: Callable[[str, list[str]], list[tuple[str, str]]] | None = None,
) -> list[Proposal]:
    """Look up every exception and classify those that now have a fix."""
    lookup = lookup or check_exception
    proposals = []
    for vuln_id in sorted(purls_by_id):
        fixes = lookup(vuln_id, purls_by_id[vuln_id])
        if fixes:
            proposals.append(
                classify_exception(
                    vuln_id,
                    purls_by_id[vuln_id],
                    fixes,
                    in_trivyignore=vuln_id in trivyignore_ids,
                )
            )
    return proposals


def issue_title(vuln_id: str) -> str:
    return f"Security exception {vuln_id} now has a fix"


def proposed_change_line(proposal: Proposal) -> str:
    if proposal.mechanical:
        packages = ", ".join(proposal.deb_packages)
        return f"remove .trivyignore.yaml entry {proposal.vuln_id} (deb: {packages})"
    return f"manual: {proposal.reason.value if proposal.reason else ''}"


def issue_body(proposal: Proposal) -> str:
    lines = [
        "OSV reports a fix for the package(s) covered by the "
        f"`{proposal.vuln_id}` security exception:",
        "",
    ]
    lines.extend(
        f"- `{package}` fixed in `{fixed_version}`" for package, fixed_version in proposal.fixes
    )
    if not proposal.fixes:
        lines.append("- (package or version details withheld: failed validation)")
    lines += ["", "Proposed change:", "", f"- {proposed_change_line(proposal)}", ""]
    if proposal.mechanical:
        lines += [
            "Run `make security-exception-fixes` (dry run), then `make security-exception-fixes APPLY=1`.",
            "",
        ]
    lines.append(
        "Re-evaluate the exception in `.trivyignore.yaml` or "
        "`.github/workflows/security-code.yml` and remove it if it is no "
        "longer needed."
    )
    return "\n".join(lines)


def _capture(argv: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, capture_output=True, text=True, check=True)


def find_audit_issues(repo: str, title: str, *, run: Callable = _capture) -> list[int]:
    """Numbers of open issues authored by the audit bot whose title is exactly `title`.

    `gh issue list --search` is fuzzy, so the exact-title and author checks are applied
    to the result; only the integer number is taken from it.
    """
    result = run(
        [
            "gh", "issue", "list", "--repo", repo, "--state", "open",
            "--author", AUDIT_ISSUE_AUTHOR, "--search", f"{title} in:title",
            "--json", "number,title,author", "--limit", "20",
        ]
    )  # fmt: skip
    numbers = []
    for item in json.loads(result.stdout or "[]"):
        author = (item.get("author") or {}).get("login")
        number = item.get("number")
        if item.get("title") == title and author == AUDIT_ISSUE_AUTHOR and isinstance(number, int):
            numbers.append(number)
    return numbers


def ensure_issue(proposal: Proposal, *, dry_run: bool) -> None:
    """Create a new issue, or update the existing one with the same title."""
    title = issue_title(proposal.vuln_id)
    body = issue_body(proposal)

    if dry_run:
        print(f"[dry-run] would create/update issue: {title}")
        print(body)
        return

    repo = os.environ.get("GITHUB_REPOSITORY", REPO_SLUG)
    matches = find_audit_issues(repo, title)
    if matches:
        subprocess.run(
            ["gh", "issue", "edit", str(matches[0]), "--repo", repo, "--body", body], check=True
        )
    else:
        subprocess.run(
            ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body],
            check=True,
        )


def collect_exceptions(
    trivyignore_path: Path = TRIVYIGNORE_PATH, workflow_path: Path = SECURITY_WORKFLOW_PATH
) -> tuple[dict[str, list[str]], set[str]]:
    """Return ({id: purls}, ids listed in .trivyignore.yaml) from the given files."""
    purls_by_id: dict[str, list[str]] = {}
    for vuln_id, purls in load_trivyignore_entries(trivyignore_path):
        purls_by_id.setdefault(vuln_id, []).extend(purls)
    trivyignore_ids = set(purls_by_id)
    for vuln_id in load_allow_ghsas(workflow_path):
        purls_by_id.setdefault(vuln_id, [])
    return purls_by_id, trivyignore_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print findings without creating or updating GitHub issues",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Fix mode: remove fixed Debian entries from .trivyignore.yaml in a temporary "
        "worktree and open a pull request. Without --dry-run, --apply really pushes and "
        "opens the PR (after a prompt unless --yes). With --dry-run it only prints the "
        "plan and diff.",
    )
    parser.add_argument("--yes", action="store_true", help="With --apply: skip the prompt")
    args = parser.parse_args()

    if args.apply:
        # Loaded on demand: the fix tool builds on this module, and the scheduled
        # audit never needs it.
        fix_tool = importlib.import_module("security_exception_fixes")
        return fix_tool.run_fixes(dry_run=args.dry_run, assume_yes=args.yes)

    purls_by_id, trivyignore_ids = collect_exceptions()
    if not purls_by_id:
        print("No security exceptions found to check.")
        return 0

    print(f"Checking {len(purls_by_id)} security exception(s): {', '.join(sorted(purls_by_id))}")
    proposals = plan_exceptions(purls_by_id, trivyignore_ids)
    fixed = {p.vuln_id for p in proposals}
    for vuln_id in sorted(purls_by_id):
        if vuln_id not in fixed:
            print(f"{vuln_id}: no fix found")
    for proposal in proposals:
        print(f"{proposal.vuln_id}: fix available -> {proposed_change_line(proposal)}")
        ensure_issue(proposal, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
