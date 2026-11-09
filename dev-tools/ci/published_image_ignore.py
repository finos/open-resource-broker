#!/usr/bin/env python3
"""Ignore-file handling for the scheduled published-image scan.

The scan looks at the `:latest` image, which was built from a release tag. An
exception removed on `main` because Debian now ships a fix therefore still
applies to that image until the next release. Two subcommands:

``build``
    Writes the ignore file the gate scan uses: the union of `main`'s
    `.trivyignore.yaml` and the one at the release `:latest` points to. The
    release is found by matching the newest plain-semver git tags to `:latest`
    by image digest (`docker buildx imagetools inspect`, public image, no API
    scope). Anything unexpected (no tag matches, two match, the tag has no
    ignore file, docker is unavailable) falls back to `main`'s file alone and
    adds a visible warning to the job summary. Content of the tag is read as
    data only; nothing from it is executed.

``summarize``
    Reads a Trivy JSON report produced with `main`'s file alone and lists the
    findings that have a fix and are suppressed only by the release's file:
    fixable on `main` but not in the published image, i.e. a release is needed.
    It writes them to the job summary and keeps one tracking issue up to date.

Entries are merged per top-level section (`vulnerabilities`, `secrets`, ...),
de-duplicated on (id, normalised purls, paths), keeping the later `expired_at`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from check_security_exceptions import REPO_SLUG, find_audit_issues

IGNORE_FILE = ".trivyignore.yaml"
MAX_CANDIDATE_TAGS = 5
ISSUE_TITLE = "Published image lacks fixes available on main"
_SEMVER_TAG_RE = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")
_SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._~:\-]{0,99}")

Run = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=True)  # noqa: S603


def _yaml() -> Any:
    import yaml

    return yaml


# ── merge ────────────────────────────────────────────────────────────────────


def _entry_key(entry: dict[str, Any]) -> tuple[Any, tuple[str, ...], tuple[str, ...]]:
    purls = tuple(sorted({str(p).strip() for p in entry.get("purls") or []}))
    paths = tuple(sorted({str(p).strip() for p in entry.get("paths") or []}))
    return entry.get("id"), purls, paths


def merge_documents(docs: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Union of ignore-file documents, section by section.

    An entry present in several documents is kept once, with the later
    `expired_at` (an entry without one never expires, so it wins). Other fields
    come from the first document that has the entry.
    """
    merged: dict[str, Any] = {}
    buckets: dict[str, dict[Any, dict[str, Any]]] = {}
    for doc in docs:
        for section, value in (doc or {}).items():
            if not isinstance(value, list):
                merged.setdefault(section, value)
                continue
            merged.setdefault(section, None)
            bucket = buckets.setdefault(section, {})
            for entry in value:
                if not isinstance(entry, dict):
                    raise ValueError(f"unexpected entry in section {section!r}")
                key = _entry_key(entry)
                current = bucket.get(key)
                if current is None:
                    bucket[key] = copy.deepcopy(entry)
                elif "expired_at" in current and "expired_at" not in entry:
                    del current["expired_at"]
                elif "expired_at" in current and str(entry["expired_at"]) > str(
                    current["expired_at"]
                ):
                    current["expired_at"] = entry["expired_at"]
    for section, bucket in buckets.items():
        merged[section] = list(bucket.values())
    return merged


def ignore_ids(doc: dict[str, Any] | None) -> set[str]:
    return {
        str(entry.get("id"))
        for entry in (doc or {}).get("vulnerabilities") or []
        if isinstance(entry, dict)
    }


# ── release lookup ───────────────────────────────────────────────────────────


def newest_semver_tags(ls_remote_output: str, limit: int = MAX_CANDIDATE_TAGS) -> list[str]:
    """Newest plain `vX.Y.Z` tags (no prerelease suffix), as `X.Y.Z`, newest first."""
    found = []
    for line in ls_remote_output.splitlines():
        parts = line.split()
        ref = parts[-1] if parts else ""
        name = ref.removeprefix("refs/tags/")
        match = _SEMVER_TAG_RE.fullmatch(name)
        if match and ref.startswith("refs/tags/"):
            found.append((tuple(int(g) for g in match.groups()), name[1:]))
    return [version for _, version in sorted(set(found), reverse=True)[:limit]]


def image_digest(run: Run, ref: str) -> str | None:
    """Fingerprint of an image's top-level manifest, via `docker buildx imagetools inspect --raw`.

    Only equality between two references matters (is `:latest` the same manifest as
    `:X.Y.Z`), so the raw manifest bytes are hashed. `--raw` needs just the index
    itself: the formatted output also reads every platform manifest and fails when
    one of them is missing from the registry.
    """
    raw = run(["docker", "buildx", "imagetools", "inspect", "--raw", ref]).stdout
    if not raw.strip():
        return None
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _tag_digest(run: Run, image: str, tag: str) -> str | None:
    """Fingerprint of `image:tag`, or None when that tag was never published as an image."""
    try:
        return image_digest(run, f"{image}:{tag}")
    except subprocess.CalledProcessError:
        return None


def resolve_release(run: Run, image: str) -> tuple[str | None, str | None]:
    """Return (version, None) for the release `:latest` points to, else (None, warning)."""
    try:
        tags = newest_semver_tags(
            run(["git", "ls-remote", "--tags", "--refs", "origin", "refs/tags/v*"]).stdout
        )
        latest = image_digest(run, f"{image}:latest")
        if not tags or latest is None:
            return None, "no release tag or no digest for :latest could be determined"
        matches = [tag for tag in tags if _tag_digest(run, image, tag) == latest]
    except subprocess.CalledProcessError as exc:
        tool = " ".join(str(part) for part in exc.cmd[:3])
        last = ((exc.stderr or "").strip().splitlines() or [""])[-1]
        # No control characters or workflow-command markers from tool output.
        detail = re.sub(r"[^\x20-\x7e]", "", last).replace("::", "")[:200]
        return None, f"release lookup failed (`{tool}` exited {exc.returncode}: {detail})"
    except (OSError, ValueError) as exc:
        return None, f"release lookup failed ({type(exc).__name__})"
    if len(matches) != 1:
        return None, f":latest matches {len(matches)} of the newest release tags (expected 1)"
    return matches[0], None


def read_release_ignore(run: Run, version: str) -> dict[str, Any] | None:
    """The release tag's ignore file as parsed data, or None if absent or malformed."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        return None
    try:
        run(
            ["git", "fetch", "--quiet", "--depth=1", "--no-tags", "origin", f"refs/tags/v{version}"]
        )
        text = run(["git", "show", f"FETCH_HEAD:{IGNORE_FILE}"]).stdout
        doc = _yaml().safe_load(text)
    except (OSError, ValueError, subprocess.CalledProcessError, _yaml().YAMLError):
        return None
    if not isinstance(doc, dict):
        return None
    return doc


def build(main_file: Path, out: Path, image: str, run: Run = _run) -> tuple[str | None, str | None]:
    """Write the gate-scan ignore file. Returns (release version or None, warning or None)."""
    main_text = main_file.read_text()
    try:
        _yaml()
    except ImportError:
        out.write_text(main_text)
        return None, "PyYAML is not available"
    version, warning = resolve_release(run, image)
    release_doc = None
    if version is not None:
        release_doc = read_release_ignore(run, version)
        if release_doc is None:
            version, warning = None, f"release {version} has no usable {IGNORE_FILE}"
    if release_doc is None:
        out.write_text(main_text)
        return None, warning
    try:
        merged = merge_documents([_yaml().safe_load(main_text), release_doc])
    except (ValueError, _yaml().YAMLError):
        out.write_text(main_text)
        return None, f"release {version} ignore file could not be merged"
    out.write_text(_yaml().safe_dump(merged, sort_keys=False, width=100, allow_unicode=True))
    return version, None


# ── summary ──────────────────────────────────────────────────────────────────


def _token(value: object) -> str:
    return value if isinstance(value, str) and _SAFE_TOKEN_RE.fullmatch(value) else "(unavailable)"


def release_needed(
    report: dict[str, Any], main_doc: dict[str, Any] | None, release_doc: dict[str, Any] | None
) -> list[tuple[str, str, str, str, str]]:
    """Fixable findings suppressed only by the release's ignore file."""
    only_release = ignore_ids(release_doc) - ignore_ids(main_doc)
    rows = set()
    for result in report.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            if vuln.get("VulnerabilityID") in only_release and vuln.get("FixedVersion"):
                rows.add(
                    tuple(
                        _token(vuln.get(key))
                        for key in (
                            "VulnerabilityID",
                            "PkgName",
                            "InstalledVersion",
                            "FixedVersion",
                            "Severity",
                        )
                    )  # fmt: skip
                )
    return sorted(rows)  # type: ignore[arg-type]


MAX_TABLE_ROWS = 100


def summary_markdown(
    rows: list[tuple[str, str, str, str, str]], limit: int = MAX_TABLE_ROWS
) -> str:
    lines = ["### Published image: release needed", ""]
    if not rows:
        return "\n".join(
            [*lines, "No findings are fixable on main but missing from the image.", ""]
        )
    lines += [
        "Fixable in main, not in the published image: release needed.",
        "",
        "| Vulnerability | Package | Installed | Fixed | Severity |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {' | '.join(row)} |" for row in rows[:limit]]
    if len(rows) > limit:
        lines += ["", f"{len(rows) - limit} more finding(s) omitted; the first {limit} are listed."]
    return "\n".join([*lines, ""])


def manage_issue(repo: str, rows: list[tuple[str, ...]], body: str, run: Run = _run) -> None:
    """Create or update the single tracking issue; close it when nothing is pending."""
    matches = find_audit_issues(repo, ISSUE_TITLE, run=run)
    if rows:
        if matches:
            run(["gh", "issue", "edit", str(matches[0]), "--repo", repo, "--body", body])
        else:
            run(["gh", "issue", "create", "--repo", repo, "--title", ISSUE_TITLE, "--body", body])
    elif matches:
        run(
            ["gh", "issue", "close", str(matches[0]), "--repo", repo, "--comment",
             "The published image now includes the fixes available on main."]
        )  # fmt: skip


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--image", required=True, help="Image reference without a tag")
    b.add_argument("--main-file", type=Path, default=Path(IGNORE_FILE))
    b.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("summarize")
    s.add_argument("--report", type=Path, required=True, help="Trivy JSON from main's file alone")
    s.add_argument("--main-file", type=Path, default=Path(IGNORE_FILE))
    s.add_argument("--release", default="", help="Release version, empty if unresolved")
    s.add_argument("--manage-issue", action="store_true")
    args = parser.parse_args()
    summary = os.environ.get("GITHUB_STEP_SUMMARY")

    if args.command == "build":
        version, warning = build(args.main_file, args.out, args.image)
        if warning:
            print(f"::warning::published-image gate uses main's ignore file only: {warning}")
            _append(
                summary,
                f"### Published image gate degraded\n\nIgnore file of the release behind "
                f":latest was not used ({warning}). The gate scan uses main's file alone.\n",
            )
        else:
            _append(summary, f"Published image gate: ignore entries of main and release {version}.")
        _append(os.environ.get("GITHUB_OUTPUT"), f"release={version or ''}")
        return 0

    if not args.release:
        _append(summary, "Release needed: not evaluated because the release was not resolved.\n")
        return 0
    if not args.report.exists():
        print("::warning::advisory scan report missing; release-needed check skipped")
        return 0
    yaml = _yaml()
    main_doc = yaml.safe_load(args.main_file.read_text())
    release_doc = read_release_ignore(_run, args.release)
    if release_doc is None:
        # Without the release's file the delta cannot be computed; leave the issue alone.
        print("::warning::release ignore file unavailable; release-needed check skipped")
        _append(
            summary, "Release needed: not evaluated because the release file was unavailable.\n"
        )
        return 0
    rows = release_needed(json.loads(args.report.read_text()), main_doc, release_doc)
    text = summary_markdown(rows)
    _append(summary, text)
    if args.manage_issue:
        manage_issue(os.environ.get("GITHUB_REPOSITORY", REPO_SLUG), rows, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
