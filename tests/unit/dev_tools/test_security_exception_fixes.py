"""Tests for the tool that removes fixed Debian security exceptions and opens a PR.

OSV, GitHub and the user's git configuration are all faked: lookups are injected,
`gh` calls are answered by a stub, and git runs against a local bare repository
that stands in for origin. Nothing touches the network.
"""

import json
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

_DEV_TOOLS_PATH = str(Path(__file__).parents[3] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

import check_security_exceptions as audit  # type: ignore[import]  # noqa: E402
import security_exception_fixes as fixes  # type: ignore[import]  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
ORIGIN_URL = "https://github.com/finos/open-resource-broker.git"

IGNORE_TEXT = """\
# header comment
vulnerabilities:
  # section comment

  - id: CVE-2026-1001
    purls:
      - pkg:deb/debian/zlib1g
    statement: >-
      first line
      second line
    expired_at: 2027-01-27

  # comment belonging to the next entry
  - id: CVE-2026-1002
    purls:
      - pkg:deb/debian/libfoo1
      - pkg:deb/debian/libfoo-dev
    statement: still vulnerable
    expired_at: 2027-01-27

  - id: CVE-2026-1003
    purls:
      - pkg:pypi/filelock
    statement: python
    expired_at: 2027-01-27

  - id: CVE-2026-1004
    purls:
      - pkg:deb/debian/libbar2
    statement: fixed in trixie
    expired_at: 2027-01-27

secrets:
  - id: private-key
    paths:
      - "**/ca.key"
    statement: test fixture
"""

WORKFLOW_TEXT = """\
jobs:
  review:
    steps:
      - uses: actions/dependency-review-action@x
        with:
          allow-ghsas: GHSA-8mgp-746c-j5xp
"""

FIXED = {
    "CVE-2026-1001": [("zlib", "1:1.3.dfsg+really1.3.1-1+deb13u1")],
    "CVE-2026-1004": [("bar", "2.0-1+deb13u1")],
    "CVE-2026-1003": [("filelock", "4.2.1")],
    "GHSA-8mgp-746c-j5xp": [("x", "1.0")],
}


def lookup(vuln_id, purls):
    return FIXED.get(vuln_id, [])


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[commit]\n\tgpgsign = false\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return config


@pytest.fixture
def repos(tmp_path, git_env):
    """A bare origin with main, plus a user checkout of it."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("init", "-b", "main", str(seed), cwd=tmp_path)
    (seed / ".trivyignore.yaml").write_text(IGNORE_TEXT)
    (seed / ".github/workflows").mkdir(parents=True)
    (seed / ".github/workflows/security-code.yml").write_text(WORKFLOW_TEXT)
    (seed / "README.md").write_text("readme\n")
    git("add", ".", cwd=seed)
    git("commit", "-m", "initial", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    git("clone", str(origin), str(clone), cwd=tmp_path)
    return origin, clone, seed


class Harness:
    """Runner that fakes the GitHub CLI and the origin URL; everything else is real."""

    def __init__(
        self, clone, issues=None, head_prs=None, open_prs=None, origin_url=ORIGIN_URL, push_url=None
    ):
        self.clone = clone
        self.origin_url = origin_url
        self.push_url = push_url or origin_url
        self.issues = issues if issues is not None else {}
        self.head_prs = head_prs or []
        self.open_prs = open_prs or []
        self.calls: list[list[str]] = []
        self.created: list[list[str]] = []
        self.push_race = False
        self.out: list[str] = []
        self.answers = ["y"]

    def run(self, argv, cwd=None, check=True):
        self.calls.append(list(argv))
        if argv[:4] == ["git", "remote", "get-url", "--push"]:
            return subprocess.CompletedProcess(argv, 0, self.push_url + "\n", "")
        if argv[:4] == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(argv, 0, self.origin_url + "\n", "")
        if argv[0] == "gh":
            return self._gh(argv)
        if argv[:2] == ["git", "push"] and self.push_race:
            fixes._run(argv, cwd=cwd, check=True)
            raise subprocess.CalledProcessError(1, argv, "", "rejected")
        return fixes._run(argv, cwd=cwd, check=check)

    def _gh(self, argv):
        sub = argv[1:3]
        if sub == ["issue", "list"]:
            title = argv[argv.index("--search") + 1].removesuffix(" in:title")
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.issues.get(title, [])), "")
        if sub == ["pr", "list"]:
            payload = self.head_prs if "--head" in argv else self.open_prs
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        if sub == ["pr", "create"]:
            self.created.append(argv)
            return subprocess.CompletedProcess(argv, 0, "https://github.com/x/y/pull/1\n", "")
        raise AssertionError(f"unexpected gh call: {argv}")

    def deps(self, lookup_fn=lookup):
        return fixes.Deps(
            run=self.run,
            lookup=lookup_fn,
            ask=lambda _prompt: self.answers.pop(0) if self.answers else "",
            out=self.out.append,
            repo_root=self.clone,
        )

    def text(self):
        return "\n".join(self.out)


def audit_issue(number, vuln_id, author="app/github-actions"):
    return {
        "number": number,
        "title": audit.issue_title(vuln_id),
        "author": {"login": author},
    }


def default_issues():
    return {
        audit.issue_title("CVE-2026-1001"): [audit_issue(11, "CVE-2026-1001")],
        audit.issue_title("CVE-2026-1004"): [audit_issue(14, "CVE-2026-1004")],
    }


def origin_branches(origin):
    out = git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=origin).stdout
    return out.split()


@pytest.mark.unit
class TestRemoveEntries:
    def test_whole_entries_removed_comments_and_rest_kept(self):
        edited = fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-1001", "CVE-2026-1004"})
        assert "CVE-2026-1001" not in edited and "CVE-2026-1004" not in edited
        assert "# header comment" in edited and "# section comment" in edited
        assert "# comment belonging to the next entry" in edited
        assert "secrets:\n  - id: private-key" in edited
        fixes.verify_edit(IGNORE_TEXT, edited, {"CVE-2026-1001", "CVE-2026-1004"})

    def test_removal_touches_only_the_entry_lines(self):
        edited = fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-1002"})
        removed = set(IGNORE_TEXT.splitlines()) - set(edited.splitlines())
        assert removed == {
            "  - id: CVE-2026-1002",
            "      - pkg:deb/debian/libfoo1",
            "      - pkg:deb/debian/libfoo-dev",
            "    statement: still vulnerable",
        }

    def test_missing_id_is_an_error(self):
        with pytest.raises(fixes.FixesError):
            fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-9999"})

    def test_verify_edit_rejects_collateral_change(self):
        bad = fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-1001"}).replace(
            "still vulnerable", "changed"
        )
        with pytest.raises(fixes.FixesError):
            fixes.verify_edit(IGNORE_TEXT, bad, {"CVE-2026-1001"})

    def test_secrets_section_entries_are_never_removed(self):
        text = IGNORE_TEXT.replace("- id: private-key", "- id: CVE-2026-1001")
        edited = fixes.remove_entries(text, {"CVE-2026-1001"})
        assert "paths:" in edited and edited.count("CVE-2026-1001") == 1


@pytest.mark.unit
class TestReplayOfZlibRemoval:
    """Replay the removal of the three zlib exceptions against the file as it was before."""

    ZLIB = ("CVE-2026-27171", "CVE-2026-76844", "CVE-2026-85091")

    def test_planned_removals_equal_the_historical_diff(self, monkeypatch):
        before = (FIXTURES / "trivyignore_before_zlib_removal.yaml").read_text()
        after = (FIXTURES / "trivyignore_after_zlib_removal.yaml").read_text()
        zlib_fix = "1:1.3.dfsg+really1.3.1-1+deb13u1"
        records = {
            f"DEBIAN-{cve}": {
                "affected": [
                    {
                        "package": {"ecosystem": "Debian:13", "name": "zlib"},
                        "ranges": [{"events": [{"introduced": "0"}, {"fixed": zlib_fix}]}],
                    }
                ]
            }
            for cve in self.ZLIB
        }
        monkeypatch.setattr(audit, "query_osv", lambda vuln_id: records.get(vuln_id))
        path = Path(__file__).parent / "fixtures" / "trivyignore_before_zlib_removal.yaml"
        purls_by_id, in_file = audit.collect_exceptions(path, Path("/nonexistent"))
        proposals = audit.plan_exceptions(purls_by_id, in_file)
        mechanical = {p.vuln_id for p in proposals if p.mechanical}
        assert mechanical == set(self.ZLIB)
        edited = fixes.remove_entries(before, mechanical)
        fixes.verify_edit(before, edited, mechanical)
        assert edited == after


@pytest.mark.unit
class TestTextAndIdentifiers:
    def proposals(self):
        return [
            audit.classify_exception(
                "CVE-2026-1004", ["pkg:deb/debian/libbar2"], [("bar", "2.0-1")]
            ),
            audit.classify_exception(
                "CVE-2026-1001", ["pkg:deb/debian/zlib1g"], [("zlib", "1:1.3-1")]
            ),
        ]

    def test_change_set_id_is_stable_and_order_independent(self):
        a = self.proposals()
        assert fixes.change_set_id(a) == fixes.change_set_id(list(reversed(a)))
        assert re.fullmatch(r"[0-9a-f]{12}", fixes.change_set_id(a))
        assert fixes.branch_name(a) == f"automation/security-exceptions-{fixes.change_set_id(a)}"

    def test_change_set_id_depends_on_id_package_and_version(self):
        base = fixes.change_set_id(self.proposals())
        other = [
            audit.classify_exception(
                "CVE-2026-1004", ["pkg:deb/debian/libbar2"], [("bar", "2.0-2")]
            ),
            self.proposals()[1],
        ]
        assert fixes.change_set_id(other) != base
        assert fixes.change_set_id(self.proposals()[:1]) != base

    def test_commit_message_template(self):
        msg = fixes.commit_message(self.proposals())
        subject = msg.splitlines()[0]
        assert re.fullmatch(r"chore\(security\): [a-z][^\n]{10,}", subject)
        assert "- CVE-2026-1001: zlib1g (fixed in zlib 1:1.3-1)" in msg
        assert msg.endswith("\n") and "\n\n" in msg

    def test_commit_type_does_not_trigger_a_release(self):
        text = (audit.REPO_ROOT / "pyproject.toml").read_text()
        triggering = set()
        for key in ("minor_tags", "patch_tags"):
            match = re.search(rf"^{key}\s*=\s*\[([^\]]*)\]", text, re.MULTILINE)
            assert match, key
            triggering |= set(re.findall(r'"([^"]+)"', match.group(1)))
        commit_type = re.match(r"([a-z]+)(\([^)]*\))?(!)?:", fixes.COMMIT_SUBJECT)
        assert commit_type and commit_type.group(1) not in triggering
        assert commit_type.group(3) is None
        assert "BREAKING" not in fixes.commit_message(self.proposals())

    def test_pr_body_references_issues_and_only_validated_values(self):
        body = fixes.pr_body(self.proposals(), {"CVE-2026-1001": 11, "CVE-2026-1004": None})
        assert "Fixes #11" in body and "Fixes #None" not in body
        assert "| CVE-2026-1001 | `zlib1g` | `zlib 1:1.3-1` |" in body

    def test_pr_body_without_issues(self):
        body = fixes.pr_body(self.proposals(), {"CVE-2026-1001": None, "CVE-2026-1004": None})
        assert "Fixes" not in body

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://github.com/finos/open-resource-broker.git", "finos/open-resource-broker"),
            ("https://github.com/finos/open-resource-broker", "finos/open-resource-broker"),
            ("git@github.com:finos/open-resource-broker.git", "finos/open-resource-broker"),
            ("ssh://git@github.com/finos/open-resource-broker.git", "finos/open-resource-broker"),
            ("https://github.com/someone/open-resource-broker.git", "someone/open-resource-broker"),
            ("https://evil.example/finos/open-resource-broker.git", None),
            ("https://github.com/finos/open-resource-broker.git\n--upload-pack=x", None),
            ("/tmp/local/repo", None),
        ],
    )
    def test_parse_origin_repo(self, url, expected):
        assert fixes.parse_origin_repo(url) == expected


@pytest.mark.unit
class TestChangeSetComparison:
    def test_matches_when_exactly_the_planned_entries_are_removed(self):
        edited = fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-1001"})
        assert fixes.change_set_matches(
            [".trivyignore.yaml"], IGNORE_TEXT, edited, {"CVE-2026-1001"}
        )

    def test_other_changed_file_does_not_match(self):
        edited = fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-1001"})
        assert not fixes.change_set_matches(
            [".trivyignore.yaml", "README.md"], IGNORE_TEXT, edited, {"CVE-2026-1001"}
        )

    def test_different_removal_set_does_not_match(self):
        edited = fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-1001", "CVE-2026-1004"})
        assert not fixes.change_set_matches(
            [".trivyignore.yaml"], IGNORE_TEXT, edited, {"CVE-2026-1001"}
        )

    def test_edited_remaining_entry_does_not_match(self):
        edited = fixes.remove_entries(IGNORE_TEXT, {"CVE-2026-1001"}).replace(
            "python", "something else"
        )
        assert not fixes.change_set_matches(
            [".trivyignore.yaml"], IGNORE_TEXT, edited, {"CVE-2026-1001"}
        )

    def test_added_entry_does_not_match(self):
        edited = IGNORE_TEXT.replace("secrets:", "  - id: CVE-2026-2000\n\nsecrets:")
        assert not fixes.change_set_matches([".trivyignore.yaml"], IGNORE_TEXT, edited, set())


@pytest.mark.unit
class TestEphemeralWorktree:
    def test_removed_on_success_and_checkout_untouched(self, repos):
        _, clone, _ = repos
        (clone / "README.md").write_text("dirty\n")
        (clone / "untracked.txt").write_text("x")
        git("checkout", "-b", "feature", cwd=clone)
        before = git("status", "--porcelain", cwd=clone).stdout
        with fixes.ephemeral_worktree(clone, fixes._run, "origin/main") as (scratch, wt):
            assert (wt / ".trivyignore.yaml").exists()
            assert "orb-security-exceptions-" in git("worktree", "list", cwd=clone).stdout
        assert not scratch.exists()
        assert git("worktree", "list", "--porcelain", cwd=clone).stdout.count("worktree ") == 1
        assert git("status", "--porcelain", cwd=clone).stdout == before
        assert git("branch", "--show-current", cwd=clone).stdout.strip() == "feature"
        assert (clone / "README.md").read_text() == "dirty\n"

    def test_removed_on_exception(self, repos):
        _, clone, _ = repos
        seen = []
        caught = []
        try:
            with fixes.ephemeral_worktree(clone, fixes._run, "origin/main") as (scratch, _wt):
                seen.append(scratch)
                raise RuntimeError("boom")
        except RuntimeError as exc:
            caught.append(exc)
        assert caught
        assert seen and not seen[0].exists()
        assert git("worktree", "list", "--porcelain", cwd=clone).stdout.count("worktree ") == 1

    def test_signals_raise_so_cleanup_runs_and_handlers_are_restored(self, repos):
        _, clone, _ = repos
        before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        seen = []
        with pytest.raises(SystemExit) as exc:
            with fixes.ephemeral_worktree(clone, fixes._run, "origin/main") as (scratch, _wt):
                seen.append(scratch)
                assert signal.getsignal(signal.SIGTERM) is fixes._terminate
                assert signal.getsignal(signal.SIGINT) is fixes._terminate
                fixes._terminate(signal.SIGTERM, None)
        assert exc.value.code == 128 + signal.SIGTERM
        assert seen and not seen[0].exists()
        assert {s: signal.getsignal(s) for s in before} == before

    def test_recent_missing_registration_is_left_alone(self, repos):
        _, clone, _ = repos
        gone = clone.parent / "gone-wt"
        git("worktree", "add", "--detach", str(gone), "origin/main", cwd=clone)
        shutil.rmtree(gone)
        with fixes.ephemeral_worktree(clone, fixes._run, "origin/main"):
            pass
        assert "gone-wt" in git("worktree", "list", cwd=clone).stdout

    def test_stale_registration_is_pruned_at_start(self, repos):
        _, clone, _ = repos
        stale = clone.parent / "stale-wt"
        git("worktree", "add", "--detach", str(stale), "origin/main", cwd=clone)
        import os

        shutil.rmtree(stale)
        admin = next((clone / ".git" / "worktrees").glob("stale-wt*"))
        old = 1_000_000_000
        os.utime(admin / "index", (old, old))
        with fixes.ephemeral_worktree(clone, fixes._run, "origin/main"):
            assert str(stale) not in git("worktree", "list", cwd=clone).stdout


@pytest.mark.unit
class TestRunFixes:
    def run(self, h, dry_run=False, yes=True, lookup_fn=lookup):
        return fixes.run_fixes(dry_run=dry_run, assume_yes=yes, deps=h.deps(lookup_fn))

    def test_dry_run_prints_plan_and_writes_nothing(self, repos):
        origin, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        assert self.run(h, dry_run=True) == 0
        text = h.text()
        base = git("rev-parse", "origin/main", cwd=clone).stdout.strip()
        assert f"Base: origin/main at {base}" in text
        removed = [line for line in text.splitlines() if line.startswith("-  - id:")]
        assert removed == ["-  - id: CVE-2026-1001", "-  - id: CVE-2026-1004"]
        assert "manual: CVE-2026-1003: NON_DEB_ECOSYSTEM" in text
        assert "manual: GHSA-8mgp-746c-j5xp: GHSA_ALLOWLIST" in text
        assert "Fixes #11" in text and "Fixes #14" in text
        assert "Dry run" in text
        assert origin_branches(origin) == ["main"]
        assert not h.created
        assert git("worktree", "list", "--porcelain", cwd=clone).stdout.count("worktree ") == 1

    def test_apply_pushes_branch_commits_only_the_ignore_file_and_opens_pr(self, repos):
        origin, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        assert self.run(h) == 0
        branches = [b for b in origin_branches(origin) if b != "main"]
        assert len(branches) == 1 and branches[0].startswith("automation/security-exceptions-")
        diff = git("diff", "--name-only", "main", branches[0], cwd=origin).stdout.split()
        assert diff == [".trivyignore.yaml"]
        subject = git("log", "-1", "--format=%s", branches[0], cwd=origin).stdout.strip()
        assert subject == fixes.COMMIT_SUBJECT
        assert len(h.created) == 1
        create = h.created[0]
        assert create[create.index("--repo") + 1] == "finos/open-resource-broker"
        assert create[create.index("--head") + 1] == branches[0]
        assert "--body-file" in create
        assert git("worktree", "list", "--porcelain", cwd=clone).stdout.count("worktree ") == 1

    def test_hooks_are_skipped_for_commit_and_push(self, repos):
        _, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        self.run(h)
        for call in h.calls:
            if call[:2] in (["git", "commit"], ["git", "push"]):
                assert "--no-verify" in call

    def test_uv_is_never_invoked(self, repos):
        _, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        self.run(h)
        assert h.calls
        for call in h.calls:
            assert call[0] != "uv" and "uv" not in call and "lock" not in call[:2]

    def test_all_subprocess_calls_are_argv_lists_and_repo_is_explicit(self, repos):
        _, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        self.run(h)
        for call in h.calls:
            assert isinstance(call, list) and all(isinstance(a, str) for a in call)
            if call[0] == "gh":
                assert call[call.index("--repo") + 1] == "finos/open-resource-broker"

    def test_prompt_declined_aborts_without_pushing(self, repos):
        origin, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        h.answers = ["n"]
        assert self.run(h, yes=False) == 1
        assert origin_branches(origin) == ["main"] and not h.created

    def test_prompt_accepted(self, repos):
        _, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        assert self.run(h, yes=False) == 0
        assert len(h.created) == 1

    def test_wrong_origin_is_refused_before_any_worktree(self, repos):
        _, clone, _ = repos
        h = Harness(clone, origin_url="https://github.com/someone/fork.git")
        assert self.run(h) == 1
        assert not any(c[:3] == ["git", "worktree", "add"] for c in h.calls)

    def test_wrong_push_url_is_refused_before_any_worktree(self, repos):
        _, clone, _ = repos
        h = Harness(clone, push_url="git@github.com:someone/open-resource-broker.git")
        assert self.run(h) == 1
        assert not any(c[:3] == ["git", "worktree", "add"] for c in h.calls)
        assert not any(c[:2] == ["git", "push"] for c in h.calls)

    def test_nothing_to_do(self, repos):
        _, clone, _ = repos
        h = Harness(clone)
        assert self.run(h, lookup_fn=lambda *_: []) == 0
        assert "No fixed Debian entries" in h.text()

    def test_worktree_removed_when_planning_raises(self, repos):
        _, clone, _ = repos
        h = Harness(clone)

        def boom(*_):
            raise RuntimeError("osv down")

        with pytest.raises(RuntimeError):
            self.run(h, lookup_fn=boom)
        assert git("worktree", "list", "--porcelain", cwd=clone).stdout.count("worktree ") == 1

    def test_checkout_untouched_by_apply(self, repos):
        _, clone, _ = repos
        (clone / "README.md").write_text("dirty\n")
        before = git("status", "--porcelain", cwd=clone).stdout
        h = Harness(clone, issues=default_issues())
        self.run(h)
        assert git("status", "--porcelain", cwd=clone).stdout == before
        assert (clone / "README.md").read_text() == "dirty\n"
        assert (clone / ".trivyignore.yaml").read_text() == IGNORE_TEXT

    # issue lookup ---------------------------------------------------------

    def test_zero_issue_matches_omits_fixes_and_says_so(self, repos):
        _, clone, _ = repos
        h = Harness(clone, issues={})
        assert self.run(h, dry_run=True) == 0
        assert "no open audit issue found" in h.text()
        assert "Fixes #" not in h.text()

    def test_several_issue_matches_abort(self, repos):
        _, clone, _ = repos
        title = audit.issue_title("CVE-2026-1001")
        issues = {title: [audit_issue(11, "CVE-2026-1001"), audit_issue(12, "CVE-2026-1001")]}
        h = Harness(clone, issues=issues)
        assert self.run(h) == 1
        assert not h.created

    def test_user_created_issue_with_same_title_is_ignored(self, repos):
        _, clone, _ = repos
        title = audit.issue_title("CVE-2026-1001")
        issues = {title: [audit_issue(99, "CVE-2026-1001", author="someone")]}
        h = Harness(clone, issues=issues)
        self.run(h, dry_run=True)
        assert "Fixes #99" not in h.text()

    def test_fuzzy_search_hit_with_other_title_is_ignored(self, repos):
        _, clone, _ = repos
        title = audit.issue_title("CVE-2026-1001")
        near = audit_issue(7, "CVE-2026-10019")
        h = Harness(clone, issues={title: [near]})
        self.run(h, dry_run=True)
        assert "Fixes #7" not in h.text()

    # existing branch ------------------------------------------------------

    def first_run_branch(self, repos):
        origin, clone, _ = repos
        h = Harness(clone, issues=default_issues())
        assert self.run(h) == 0
        return [b for b in origin_branches(origin) if b != "main"][0]

    def test_equal_change_set_without_pr_opens_the_missing_pr(self, repos):
        origin, clone, _ = repos
        branch = self.first_run_branch(repos)
        tip = git("rev-parse", branch, cwd=origin).stdout
        h = Harness(clone, issues=default_issues(), head_prs=[])
        assert self.run(h) == 0
        assert len(h.created) == 1
        assert git("rev-parse", branch, cwd=origin).stdout == tip
        assert not any(c[:2] == ["git", "commit"] for c in h.calls)

    def test_equal_change_set_with_open_pr_is_a_noop(self, repos):
        _, clone, _ = repos
        self.first_run_branch(repos)
        pr = {"number": 5, "state": "OPEN", "url": "https://github.com/x/y/pull/5"}
        h = Harness(clone, issues=default_issues(), head_prs=[pr])
        assert self.run(h) == 0
        assert not h.created and "pull/5" in h.text()

    def test_main_moving_does_not_break_resume(self, repos):
        _, clone, seed = repos
        self.first_run_branch(repos)
        (seed / "README.md").write_text("moved\n")
        git("commit", "-am", "move main", cwd=seed)
        git("push", "origin", "main", cwd=seed)
        h = Harness(clone, issues=default_issues(), head_prs=[])
        assert self.run(h) == 0
        assert len(h.created) == 1

    def test_different_content_exits_non_zero_and_leaves_branch(self, repos):
        origin, clone, seed = repos
        branch = self.first_run_branch(repos)
        tip = git("rev-parse", branch, cwd=origin).stdout
        git("fetch", "origin", branch, cwd=seed)
        git("checkout", "-b", "other", f"origin/{branch}", cwd=seed)
        (seed / "README.md").write_text("extra\n")
        git("commit", "-am", "extra", cwd=seed)
        git("push", "origin", f"other:{branch}", cwd=seed)
        h = Harness(clone, issues=default_issues())
        assert self.run(h) == 1
        assert not h.created
        assert git("rev-parse", branch, cwd=origin).stdout != tip  # still the foreign tip
        assert branch in origin_branches(origin)

    def test_closed_unmerged_pr_requires_a_human_choice(self, repos):
        _, clone, _ = repos
        self.first_run_branch(repos)
        pr = {"number": 5, "state": "CLOSED", "url": "https://github.com/x/y/pull/5"}
        h = Harness(clone, issues=default_issues(), head_prs=[pr])
        assert self.run(h) == 1
        assert not h.created

    def test_non_fast_forward_push_is_treated_as_existing_branch(self, repos):
        origin, clone, _ = repos
        h = Harness(clone, issues=default_issues(), head_prs=[])
        h.push_race = True
        assert self.run(h) == 0
        assert len(h.created) == 1
        assert [b for b in origin_branches(origin) if b != "main"]

    def test_push_failure_without_branch_is_an_error(self, repos):
        _, clone, _ = repos

        class NoPush(Harness):
            def run(self, argv, cwd=None, check=True):
                if argv[:2] == ["git", "push"]:
                    raise subprocess.CalledProcessError(1, argv, "", "denied")
                return super().run(argv, cwd, check)

        h = NoPush(clone, issues=default_issues())
        assert self.run(h) == 1
        assert not h.created

    def test_superseded_automation_prs_are_listed_not_closed(self, repos):
        _, clone, _ = repos
        open_prs = [
            {
                "number": 3,
                "headRefName": "automation/security-exceptions-aaaaaaaaaaaa",
                "url": "u3",
            },
            {"number": 4, "headRefName": "feat/unrelated", "url": "u4"},
        ]
        h = Harness(clone, issues=default_issues(), open_prs=open_prs)
        self.run(h, dry_run=True)
        assert "open PR #3" in h.text() and "#4" not in h.text()
        assert not any(c[:3] == ["gh", "pr", "close"] for c in h.calls)


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("make") is None, reason="make not available")
class TestMakeTarget:
    def command(self, *args):
        result = subprocess.run(
            ["make", "-n", "security-exception-fixes", *args],
            cwd=audit.REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def test_default_is_a_dry_run(self):
        out = self.command()
        assert "--apply --dry-run" in out and "--yes" not in out

    @pytest.mark.parametrize("value", ["0", "no", "", "true", "11"])
    def test_anything_but_one_stays_a_dry_run(self, value):
        assert "--dry-run" in self.command(f"APPLY={value}")

    def test_apply_one_acts(self):
        out = self.command("APPLY=1")
        assert "--apply" in out and "--dry-run" not in out and "--yes" not in out

    def test_yes_alone_does_not_apply(self):
        out = self.command("YES=1")
        assert "--dry-run" in out

    def test_apply_and_yes(self):
        out = self.command("APPLY=1", "YES=1")
        assert "--dry-run" not in out and "--yes" in out
