#!/usr/bin/env python3
"""Check whether security exceptions now have a published upstream fix.

Reads every vulnerability ID suppressed in `.trivyignore.yaml` and every ID in
the `allow-ghsas` list of `.github/workflows/security-code.yml`, queries the
OSV API (https://api.osv.dev) for each one, and reports any whose affected
package now has a `fixed` version event.

Debian OS-package CVEs are not covered by the generic CVE-ID OSV record, so
they are looked up under Debian's own `DEBIAN-<CVE-ID>` record instead; a
`fixed` event in that record's affected entry for the Debian release the
Dockerfile actually ships (see DEBIAN_ECOSYSTEM below) is treated as a fix
being available. A fix in a different Debian release (e.g. the next,
unstable release) is not actionable for this image and is ignored. IDs with
no matching OSV record are skipped silently.

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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIVYIGNORE_PATH = REPO_ROOT / ".trivyignore.yaml"
SECURITY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "security-code.yml"
OSV_API_BASE = "https://api.osv.dev/v1/vulns/"
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


def load_trivyignore_ids(path: Path) -> list[str]:
    """Extract vulnerability IDs from `.trivyignore.yaml` under `vulnerabilities:`."""
    if not path.exists():
        return []

    ids = []
    in_vulnerabilities = False
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        is_top_level_key = not line[:1].isspace()
        if is_top_level_key:
            in_vulnerabilities = line.strip() == "vulnerabilities:"
            continue
        if in_vulnerabilities:
            match = re.match(r"\s*-\s*id:\s*(\S+)", line)
            if match:
                ids.append(match.group(1))
    return ids


def load_allow_ghsas(path: Path) -> list[str]:
    """Extract GHSA IDs from the `allow-ghsas` dependency-review input."""
    if not path.exists():
        return []

    match = re.search(r"allow-ghsas:\s*(.+)", path.read_text())
    if not match:
        return []
    value = match.group(1).strip().strip("'\"")
    return [v.strip() for v in re.split(r"[,\s]+", value) if v.strip()]


def query_osv(vuln_id: str) -> dict | None:
    """Fetch an OSV vulnerability record by ID, or None if it does not exist.

    The ID is validated against VULN_ID_RE and percent-encoded before being
    appended to the fixed https:// OSV_API_BASE, so the final URL can never
    resolve to a file:// path or another unexpected scheme even though it
    is built from a value that ultimately comes from local config files.
    """
    if not VULN_ID_RE.match(vuln_id):
        print(f"skipping {vuln_id!r}: not a recognised OSV vulnerability ID", file=sys.stderr)
        return None

    url = OSV_API_BASE + urllib.parse.quote(vuln_id, safe="-")
    if not url.startswith(OSV_API_BASE):
        raise ValueError(f"refusing to request unexpected URL {url!r}")

    request = urllib.request.Request(url)
    try:
        # url is built from the fixed https:// OSV_API_BASE plus an ID that
        # has already been validated against VULN_ID_RE and percent-encoded
        # above, and is checked to still start with OSV_API_BASE immediately
        # before this call, so it can never resolve to a file:// path.
        with (
            urllib.request.urlopen(  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response
        ):
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def find_fixes(record: dict, *, debian_only: bool) -> list[tuple[str, str]]:
    """Return (package, fixed_version) pairs for any 'fixed' event in the record.

    For Debian records, only the DEBIAN_ECOSYSTEM affected entry is
    considered: a fix in a different Debian release is not actionable for
    this image's base image.
    """
    fixes = []
    for affected in record.get("affected", []):
        package = affected.get("package", {})
        ecosystem = package.get("ecosystem", "")
        if debian_only and ecosystem != DEBIAN_ECOSYSTEM:
            continue
        name = package.get("name", "unknown")
        for value_range in affected.get("ranges", []):
            for event in value_range.get("events", []):
                if "fixed" in event:
                    fixes.append((name, event["fixed"]))
    return fixes


def check_exception(vuln_id: str) -> list[tuple[str, str]]:
    """Look up a single exception ID and return any fixes OSV now reports.

    Debian OS-package CVEs are not merged into the generic CVE-ID record OSV
    aggregates from the CVE List (that record only tracks the upstream GIT
    history, not Debian's package-level fix status). Debian publishes its
    own per-distro records instead, under the deterministic ID
    `DEBIAN-<CVE-ID>`, so those are queried directly.
    """
    is_debian_cve = vuln_id.upper().startswith("CVE-")
    lookup_id = f"DEBIAN-{vuln_id}" if is_debian_cve else vuln_id
    record = query_osv(lookup_id)
    if record is None:
        return []
    return find_fixes(record, debian_only=is_debian_cve)


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

    ids = sorted(
        set(load_trivyignore_ids(TRIVYIGNORE_PATH) + load_allow_ghsas(SECURITY_WORKFLOW_PATH))
    )
    if not ids:
        print("No security exceptions found to check.")
        return 0

    print(f"Checking {len(ids)} security exception(s): {', '.join(ids)}")
    for vuln_id in ids:
        fixes = check_exception(vuln_id)
        if fixes:
            print(f"{vuln_id}: fix available -> {fixes}")
            ensure_issue(vuln_id, fixes, dry_run=args.dry_run)
        else:
            print(f"{vuln_id}: no fix found")

    return 0


if __name__ == "__main__":
    sys.exit(main())
