"""Remove fixed Debian security exceptions from `.trivyignore.yaml` and open a PR.

Invoked as `check_security_exceptions.py --apply [--dry-run] [--yes]`, normally
through `make security-exception-fixes` (dry run) and
`make security-exception-fixes APPLY=1`.

The plan is recomputed from OSV and the repository files; issue bodies are never
parsed. All edits, the commit and the push happen in a throw-away worktree
created from a freshly fetched `origin/main`, so the checkout the tool is run
from can be dirty or on any branch: its working tree, index and branches are not
used or modified (only the `origin/main` remote-tracking ref is refreshed, and a
temporary worktree is registered and removed in the shared git directory). Only whole
`.trivyignore.yaml` entries for Debian packages are removed; GHSA allow-list
entries and PyPI packages stay manual.

The commit and push skip git hooks (`--no-verify`). The change only deletes
entries from a YAML file, so the repository's Python hooks have nothing to
inspect, they depend on the invoking user's local tools, and CI re-runs the
equivalent checks on the pull request. A signing-key prompt can still block an
unattended `--yes` run.

Nothing is ever deleted or force-pushed: an existing branch is resumed, left
alone or refused, never overwritten.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from check_security_exceptions import (
    REPO_ROOT,
    REPO_SLUG,
    Proposal,
    check_exception,
    collect_exceptions,
    find_audit_issues,
    issue_title,
    plan_exceptions,
)

IGNORE_FILE = ".trivyignore.yaml"
WORKFLOW_FILE = ".github/workflows/security-code.yml"
BRANCH_PREFIX = "automation/security-exceptions-"
BASE_BRANCH = "main"

# Deliberately not a release-triggering type: semantic-release only bumps the
# version for the types listed in [tool.semantic_release.commit_parser_options]
# (feat, fix, perf) and for breaking changes. Removing a suppression changes no
# shipped code. Squash merges use the PR title, which uses the same type.
COMMIT_TYPE = "chore(security)"
SUBJECT = "remove Trivy exceptions for vulnerabilities fixed in Debian trixie"
COMMIT_SUBJECT = f"{COMMIT_TYPE}: {SUBJECT}"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class FixesError(Exception):
    """A refusal or failure that should end the run with a non-zero exit."""


def _run(argv: list[str], cwd: Path | None = None, check: bool = True):
    """Run an argv list (never a shell string) and capture its text output."""
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=check)  # noqa: S603


@dataclass
class Deps:
    run: Runner = _run
    lookup: Callable[[str, list[str]], list[tuple[str, str]]] = check_exception
    ask: Callable[[str], str] = input
    out: Callable[[str], None] = print
    repo_root: Path = REPO_ROOT


# ── origin ───────────────────────────────────────────────────────────────────

_ORIGIN_RES = (
    re.compile(r"https://github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)(?:\.git)?/?"),
    re.compile(r"git@github\.com:([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)(?:\.git)?"),
    re.compile(r"ssh://git@github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)(?:\.git)?/?"),
)


def parse_origin_repo(url: str) -> str | None:
    """Return `owner/repo` for a GitHub remote URL, or None for anything else."""
    for pattern in _ORIGIN_RES:
        match = pattern.fullmatch(url.strip())
        if match:
            return match.group(1)
    return None


# ── ignore file editing ──────────────────────────────────────────────────────

_ENTRY_START_RE = re.compile(r"(\s*)-\s+id:\s*(\S+)\s*")


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FixesError("PyYAML is required: install it or run inside the project venv") from exc
    return yaml


def remove_entries(text: str, ids: set[str]) -> str:
    """Delete whole `vulnerabilities` entries by ID, keeping everything else byte for byte.

    An entry is its `- id:` line plus every following line indented deeper than
    the dash, plus one trailing blank line. Comments at the dash indent belong
    to the next entry and stay. Raises FixesError if an ID is not found.
    """
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    removed: set[str] = set()
    section = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() and not line[0].isspace() and not line.startswith("#"):
            section = line.strip()
        match = _ENTRY_START_RE.fullmatch(line.rstrip("\r\n"))
        if section == "vulnerabilities:" and match and match.group(2) in ids:
            dash_indent = len(match.group(1))
            end = index + 1
            while end < len(lines):
                nxt = lines[end]
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) > dash_indent:
                    end += 1
                else:
                    break
            if end < len(lines) and not lines[end].strip():
                end += 1
            removed.add(match.group(2))
            index = end
            continue
        kept.append(line)
        index += 1
    missing = ids - removed
    if missing:
        raise FixesError(f"entries not found in {IGNORE_FILE}: {sorted(missing)}")
    return "".join(kept)


def verify_edit(original: str, edited: str, ids: set[str]) -> None:
    """Assert the edited file equals the original minus exactly the entries with `ids`."""
    yaml = _yaml()
    before = yaml.safe_load(original)
    after = yaml.safe_load(edited)
    expected = copy.deepcopy(before)
    expected["vulnerabilities"] = [
        entry for entry in before.get("vulnerabilities") or [] if entry.get("id") not in ids
    ]
    if after != expected:
        raise FixesError(f"edited {IGNORE_FILE} is not the original minus the planned entries")


def removed_ids(base_text: str, branch_text: str) -> set[str] | None:
    """IDs removed between two ignore-file versions, or None if anything else differs."""
    yaml = _yaml()
    base = yaml.safe_load(base_text) or {}
    branch = yaml.safe_load(branch_text) or {}
    if set(base) != set(branch):
        return None
    for key in base:
        if key != "vulnerabilities" and base[key] != branch[key]:
            return None
    base_entries = base.get("vulnerabilities") or []
    branch_entries = branch.get("vulnerabilities") or []
    base_ids = {entry.get("id") for entry in base_entries}
    branch_ids = {entry.get("id") for entry in branch_entries}
    gone = {str(i) for i in base_ids if i not in branch_ids}
    # Entries that remain must be untouched, and nothing may be added.
    kept = [entry for entry in base_entries if entry.get("id") not in gone]
    if kept != branch_entries:
        return None
    return gone


def change_set_matches(
    changed_files: list[str], base_text: str, branch_text: str, planned_ids: set[str]
) -> bool:
    """True if a branch changes only the ignore file, removing exactly the planned entries."""
    if changed_files != [IGNORE_FILE]:
        return False
    return removed_ids(base_text, branch_text) == planned_ids


# ── identifiers and text ─────────────────────────────────────────────────────


def change_set_id(proposals: list[Proposal]) -> str:
    """First 12 hex digits of sha256 over the sorted (id, package, fixed_version) triples."""
    triples = sorted(
        (proposal.vuln_id, package, version)
        for proposal in proposals
        for package, version in proposal.fixes
    )
    digest = hashlib.sha256("\n".join("\t".join(triple) for triple in triples).encode())
    return digest.hexdigest()[:12]


def branch_name(proposals: list[Proposal]) -> str:
    return BRANCH_PREFIX + change_set_id(proposals)


def commit_message(proposals: list[Proposal]) -> str:
    lines = [
        COMMIT_SUBJECT,
        "",
        "Debian 13 (trixie) now ships fixed packages for the vulnerabilities below, so",
        f"their suppressions in {IGNORE_FILE} are no longer needed. The container build",
        "runs apt-get upgrade and picks the fixed packages up on its next build.",
        "",
    ]
    for proposal in sorted(proposals, key=lambda p: p.vuln_id):
        fixed = ", ".join(f"{package} {version}" for package, version in proposal.fixes)
        lines.append(f"- {proposal.vuln_id}: {', '.join(proposal.deb_packages)} (fixed in {fixed})")
    return "\n".join(lines) + "\n"


def pr_body(proposals: list[Proposal], issues: dict[str, int | None]) -> str:
    lines = [
        "## Description",
        f"Removes the {IGNORE_FILE} entries for vulnerabilities that Debian 13 (trixie) now "
        "fixes. The container build runs `apt-get upgrade`, so the next image build picks up the "
        "fixed packages.",
        "",
        "| Vulnerability | Binary packages | Fixed in |",
        "| --- | --- | --- |",
    ]
    for proposal in sorted(proposals, key=lambda p: p.vuln_id):
        fixed = ", ".join(f"`{package} {version}`" for package, version in proposal.fixes)
        packages = ", ".join(f"`{name}`" for name in proposal.deb_packages)
        lines.append(f"| {proposal.vuln_id} | {packages} | {fixed} |")
    lines += [
        "",
        "## Type of Change",
        "- [x] CI/CD or build process changes",
        "",
        "## Related Issues",
    ]
    numbered = [issues[p.vuln_id] for p in sorted(proposals, key=lambda p: p.vuln_id)]
    lines += [f"Fixes #{number}" for number in numbered if number is not None] or ["None"]
    lines += [
        "",
        "## Security Considerations",
        "- [x] No security implications",
        "",
        "Only whole suppression entries are removed; no other file changes.",
    ]
    return "\n".join(lines) + "\n"


# ── worktree ─────────────────────────────────────────────────────────────────


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


@contextmanager
def ephemeral_worktree(repo_root: Path, run: Runner, ref: str) -> Iterator[tuple[Path, Path]]:
    """Yield (scratch dir, worktree path) for a detached worktree at `ref`.

    The scratch dir is created here, recorded, and the only thing deleted on exit.
    SIGINT and SIGTERM raise so the cleanup runs; a stale registration left by a
    killed earlier run is pruned first.
    """
    previous: dict[int, Any] = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, _terminate)
    except ValueError:  # not the main thread
        previous = {}
    # Only registrations older than a day: a worktree of someone else that is
    # briefly unavailable is left alone, a leftover of a killed earlier run goes.
    run(["git", "worktree", "prune", "--expire=1.day.ago"], cwd=repo_root, check=False)
    scratch = Path(tempfile.mkdtemp(prefix="orb-security-exceptions-"))
    worktree = scratch / "wt"
    try:
        run(["git", "worktree", "add", "--detach", str(worktree), ref], cwd=repo_root)
        yield scratch, worktree
    finally:
        try:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_root, check=False)
            run(["git", "worktree", "prune", "--expire=1.day.ago"], cwd=repo_root, check=False)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


# ── GitHub lookups ───────────────────────────────────────────────────────────


def resolve_issue_numbers(
    proposals: list[Proposal], run: Runner, out: Callable[[str], None]
) -> dict[str, int | None]:
    """Map each ID to its audit issue number (exact title, bot author), or None."""
    numbers: dict[str, int | None] = {}
    for proposal in proposals:
        title = issue_title(proposal.vuln_id)
        matches = find_audit_issues(REPO_SLUG, title, run=lambda argv: run(argv))
        if len(matches) > 1:
            raise FixesError(f"more than one open audit issue titled {title!r}: {matches}")
        numbers[proposal.vuln_id] = matches[0] if matches else None
        if matches:
            out(f"  {proposal.vuln_id}: Fixes #{matches[0]}")
        else:
            out(f"  {proposal.vuln_id}: no open audit issue found; the PR will not reference one")
    return numbers


def _gh_json(run: Runner, argv: list[str]) -> list[dict[str, Any]]:
    import json

    return json.loads(run(["gh", *argv]).stdout or "[]")


def superseded_prs(run: Runner, branch: str) -> list[dict[str, Any]]:
    prs = _gh_json(
        run,
        ["pr", "list", "--repo", REPO_SLUG, "--state", "open", "--limit", "100",
         "--json", "number,headRefName,url"],
    )  # fmt: skip
    return [
        pr
        for pr in prs
        if str(pr.get("headRefName", "")).startswith(BRANCH_PREFIX)
        and pr.get("headRefName") != branch
    ]


@dataclass
class BranchState:
    exists: bool = False
    matches: bool = False
    prs: list[dict[str, Any]] = field(default_factory=list)


def inspect_remote_branch(
    run: Runner, worktree: Path, branch: str, planned_ids: set[str]
) -> BranchState:
    """Look at `branch` on origin: does it exist, does it carry the planned change set, PRs."""
    listing = run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], cwd=worktree)
    if not listing.stdout.strip():
        return BranchState()
    ref = f"refs/remotes/origin/{branch}"
    run(["git", "fetch", "--quiet", "origin", f"+refs/heads/{branch}:{ref}"], cwd=worktree)
    state = BranchState(exists=True)
    state.prs = _gh_json(
        run,
        ["pr", "list", "--repo", REPO_SLUG, "--head", branch, "--state", "all", "--limit", "20",
         "--json", "number,state,url"],
    )  # fmt: skip
    base = run(["git", "merge-base", f"origin/{BASE_BRANCH}", ref], cwd=worktree, check=False)
    if base.returncode != 0:
        return state
    merge_base = base.stdout.strip()
    changed = run(["git", "diff", "--name-only", merge_base, ref], cwd=worktree).stdout.split()
    base_text = run(["git", "show", f"{merge_base}:{IGNORE_FILE}"], cwd=worktree).stdout
    branch_text = run(["git", "show", f"{ref}:{IGNORE_FILE}"], cwd=worktree, check=False).stdout
    state.matches = change_set_matches(changed, base_text, branch_text, planned_ids)
    return state


def decide_existing(state: BranchState) -> str:
    """Return "none", "open_pr" or "resume" for an existing branch, or raise."""
    if not state.exists:
        return "none"
    if not state.matches:
        raise FixesError(
            "branch exists with different content; delete it to regenerate "
            "(this tool never deletes or force-pushes)"
        )
    if any(pr.get("state") == "OPEN" for pr in state.prs):
        return "open_pr"
    if state.prs:
        raise FixesError(
            "a closed or merged pull request already exists for this branch; "
            "decide manually whether to reopen it or delete the branch"
        )
    return "resume"


# ── main flow ────────────────────────────────────────────────────────────────


def run_fixes(*, dry_run: bool, assume_yes: bool, deps: Deps | None = None) -> int:
    deps = deps or Deps()
    try:
        return _run_fixes(dry_run=dry_run, assume_yes=assume_yes, deps=deps)
    except FixesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        command = " ".join(str(part) for part in exc.cmd[:3])
        detail = (exc.stderr or "").strip().splitlines()[-1:] or [""]
        print(f"error: `{command} ...` exited {exc.returncode}: {detail[0]}", file=sys.stderr)
        return 1


def _run_fixes(*, dry_run: bool, assume_yes: bool, deps: Deps) -> int:
    run, out, root = deps.run, deps.out, deps.repo_root

    for flags in (["get-url"], ["get-url", "--push"]):
        url = run(["git", "remote", *flags, "origin"], cwd=root).stdout.strip()
        if parse_origin_repo(url) != REPO_SLUG:
            kind = "push URL" if "--push" in flags else "URL"
            raise FixesError(f"origin {kind} must be {REPO_SLUG}; refusing to continue")
    run(["git", "fetch", "--quiet", "origin", BASE_BRANCH], cwd=root)
    base_sha = run(["git", "rev-parse", f"origin/{BASE_BRANCH}"], cwd=root).stdout.strip()
    script_sha = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    dirty = run(["git", "status", "--porcelain", "--", "dev-tools/ci"], cwd=root).stdout.strip()
    out(f"Base: origin/{BASE_BRANCH} at {base_sha}")
    out(f"Tool: run from {script_sha}{' (modified working copy)' if dirty else ''}")

    with ephemeral_worktree(root, run, f"origin/{BASE_BRANCH}") as (scratch, worktree):
        purls_by_id, trivyignore_ids = collect_exceptions(
            worktree / IGNORE_FILE, worktree / WORKFLOW_FILE
        )
        proposals = plan_exceptions(purls_by_id, trivyignore_ids, deps.lookup)
        mechanical = [p for p in proposals if p.mechanical]
        for proposal in proposals:
            if not proposal.mechanical and proposal.reason:
                out(f"manual: {proposal.vuln_id}: {proposal.reason.value}")
        if not mechanical:
            out("No fixed Debian entries to remove.")
            return 0

        ids = {p.vuln_id for p in mechanical}
        original = (worktree / IGNORE_FILE).read_text()
        edited = remove_entries(original, ids)
        verify_edit(original, edited, ids)
        (worktree / IGNORE_FILE).write_text(edited)
        changed = run(["git", "diff", "--name-only"], cwd=worktree).stdout.split()
        if changed != [IGNORE_FILE]:
            raise FixesError(f"unexpected changed files: {changed}")

        branch = branch_name(mechanical)
        out(f"Plan: remove {len(ids)} entr{'y' if len(ids) == 1 else 'ies'} on branch {branch}")
        out("Audit issues:")
        issues = resolve_issue_numbers(mechanical, run, out)
        for pr in superseded_prs(run, branch):
            out(f"note: open PR #{pr['number']} on {pr['headRefName']} is for a different set; "
                f"{pr['url']}")  # fmt: skip

        state = inspect_remote_branch(run, worktree, branch, ids)
        decision = decide_existing(state)
        if decision == "open_pr":
            url = next(pr["url"] for pr in state.prs if pr.get("state") == "OPEN")
            out(f"Branch and open pull request already exist: {url}")
            return 0

        out(run(["git", "diff", "--stat"], cwd=worktree).stdout)
        out(run(["git", "diff"], cwd=worktree).stdout)
        title = COMMIT_SUBJECT
        body = pr_body(mechanical, issues)
        out(f"PR title: {title}\n\n{body}")
        if decision == "resume":
            out(f"Branch {branch} exists with this change set and has no pull request.")
        if dry_run:
            out("Dry run: nothing written, committed, pushed or opened.")
            return 0
        if not assume_yes:
            try:
                answer = deps.ask("Push and open PR? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in {"y", "yes"}:
                out("Aborted.")
                return 1

        if decision == "none":
            (scratch / "commit-message.txt").write_text(commit_message(mechanical))
            run(["git", "checkout", "-b", branch], cwd=worktree)
            run(["git", "add", "--", IGNORE_FILE], cwd=worktree)
            run(["git", "commit", "--no-verify", "-F", str(scratch / "commit-message.txt")],
                cwd=worktree)  # fmt: skip
            try:
                run(["git", "push", "--no-verify", "origin", f"{branch}:refs/heads/{branch}"],
                    cwd=worktree)  # fmt: skip
            except subprocess.CalledProcessError:
                # Another run may have pushed first (non-fast-forward): treat as "exists".
                state = inspect_remote_branch(run, worktree, branch, ids)
                if not state.exists:
                    raise
                decision = decide_existing(state)
                if decision == "open_pr":
                    url = next(pr["url"] for pr in state.prs if pr.get("state") == "OPEN")
                    out(f"Branch and open pull request already exist: {url}")
                    return 0

        (scratch / "pr-body.md").write_text(body)
        created = run(
            ["gh", "pr", "create", "--repo", REPO_SLUG, "--head", branch, "--base", BASE_BRANCH,
             "--title", title, "--body-file", str(scratch / "pr-body.md")],
            cwd=worktree,
        )  # fmt: skip
        out(created.stdout.strip())
    return 0
