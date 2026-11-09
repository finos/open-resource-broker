"""Tests for the ignore file used by the scheduled published-image scan."""

import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_DEV_TOOLS_PATH = str(Path(__file__).parents[3] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

import published_image_ignore as pii  # type: ignore[import]  # noqa: E402

IMAGE = "ghcr.io/finos/open-resource-broker"
DIGEST_A = '{"manifests": ["a"]}'  # stands in for a raw manifest
DIGEST_B = '{"manifests": ["b"]}'
D = datetime.date


def entry(vuln_id, purls=None, expired=None, **extra):
    e = {"id": vuln_id, **({"purls": purls} if purls else {}), **extra}
    if expired:
        e["expired_at"] = expired
    return e


class FakeRun:
    """Answers git and docker invocations from tables; records every call."""

    def __init__(self, tags=(), digests=None, tag_file=None, fail_docker=False, fetch_fails=False):
        self.tags = tags
        self.digests = digests or {}
        self.tag_file = tag_file
        self.fail_docker = fail_docker
        self.fetch_fails = fetch_fails
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        out = ""
        if argv[:2] == ["git", "ls-remote"]:
            out = "".join(f"{'0' * 40}\trefs/tags/{t}\n" for t in self.tags)
        elif argv[:3] == ["docker", "buildx", "imagetools"]:
            if self.fail_docker:
                raise FileNotFoundError("docker")
            ref = argv[-1]
            if ref not in self.digests:
                raise subprocess.CalledProcessError(1, argv, "", "not found")
            out = self.digests[ref]
        elif argv[:2] == ["git", "fetch"]:
            if self.fetch_fails:
                raise subprocess.CalledProcessError(1, argv, "", "no tag")
        elif argv[:2] == ["git", "show"]:
            if self.tag_file is None:
                raise subprocess.CalledProcessError(128, argv, "", "missing")
            out = self.tag_file
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.mark.unit
class TestMerge:
    def test_union_keeps_entries_from_both(self):
        merged = pii.merge_documents(
            [
                {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"])]},
                {"vulnerabilities": [entry("CVE-2", ["pkg:deb/debian/b"])]},
            ]
        )
        assert [e["id"] for e in merged["vulnerabilities"]] == ["CVE-1", "CVE-2"]

    def test_dedupes_on_id_and_normalised_purls(self):
        merged = pii.merge_documents(
            [
                {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/b", "pkg:deb/debian/a"])]},
                {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a", "pkg:deb/debian/b"])]},
            ]
        )
        assert len(merged["vulnerabilities"]) == 1

    def test_same_id_with_different_purls_is_kept_twice(self):
        merged = pii.merge_documents(
            [
                {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"])]},
                {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/b"])]},
            ]
        )
        assert len(merged["vulnerabilities"]) == 2

    def test_later_expiry_wins(self):
        for first, second in [(D(2027, 1, 1), D(2027, 6, 1)), (D(2027, 6, 1), D(2027, 1, 1))]:
            merged = pii.merge_documents(
                [
                    {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"], first)]},
                    {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"], second)]},
                ]
            )
            assert merged["vulnerabilities"][0]["expired_at"] == D(2027, 6, 1)

    def test_entry_without_expiry_wins(self):
        merged = pii.merge_documents(
            [
                {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"], D(2027, 1, 1))]},
                {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"])]},
            ]
        )
        assert "expired_at" not in merged["vulnerabilities"][0]

    def test_every_section_is_merged_including_secrets(self):
        main = {
            "vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"])],
            "secrets": [{"id": "private-key", "paths": ["b", "a"]}],
        }
        release = {
            "vulnerabilities": [],
            "secrets": [
                {"id": "private-key", "paths": ["a", "b"]},
                {"id": "other-key", "paths": ["z"]},
            ],
        }
        merged = pii.merge_documents([main, release])
        assert [e["id"] for e in merged["secrets"]] == ["private-key", "other-key"]
        assert len(merged["vulnerabilities"]) == 1

    def test_section_only_in_release_is_kept(self):
        merged = pii.merge_documents([{"vulnerabilities": []}, {"secrets": [{"id": "k"}]}])
        assert merged["secrets"] == [{"id": "k"}]

    def test_inputs_are_not_mutated(self):
        a = {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"], D(2027, 1, 1))]}
        b = {"vulnerabilities": [entry("CVE-1", ["pkg:deb/debian/a"], D(2027, 6, 1))]}
        pii.merge_documents([a, b])
        assert a["vulnerabilities"][0]["expired_at"] == D(2027, 1, 1)

    def test_malformed_entry_raises(self):
        with pytest.raises(ValueError):
            pii.merge_documents([{"vulnerabilities": ["not a mapping"]}])

    def test_output_round_trips_through_yaml(self):
        merged = pii.merge_documents(
            [
                {
                    "vulnerabilities": [
                        entry("CVE-1", ["pkg:deb/debian/a"], D(2027, 1, 1), statement="x\ny")
                    ]
                }
            ]
        )
        assert yaml.safe_load(yaml.safe_dump(merged, sort_keys=False)) == merged


@pytest.mark.unit
class TestTagDerivation:
    def test_newest_plain_semver_tags_only(self):
        listing = "".join(
            f"{'0' * 40}\trefs/tags/{t}\n"
            for t in [
                "v1.9.0",
                "v1.10.0",
                "v1.9.2",
                "v2.0.0-rc.1",
                "v1.9.3-alpha.1",
                "vfoo",
                "v1.2",
                "release-1",
                "v1.9.2.1",
            ]  # fmt: skip
        )
        assert pii.newest_semver_tags(listing) == ["1.10.0", "1.9.2", "1.9.0"]

    def test_candidate_count_is_limited(self):
        listing = "".join(f"{'0' * 40}\trefs/tags/v1.0.{n}\n" for n in range(20))
        assert pii.newest_semver_tags(listing) == [f"1.0.{n}" for n in range(19, 14, -1)]

    def digests(self, **by_tag):
        d = {f"{IMAGE}:{t}": v for t, v in by_tag.items()}
        return d

    def test_exactly_one_match(self):
        run = FakeRun(
            tags=["v1.9.1", "v1.9.2"],
            digests={
                f"{IMAGE}:latest": DIGEST_A,
                f"{IMAGE}:1.9.2": DIGEST_A,
                f"{IMAGE}:1.9.1": DIGEST_B,
            },
        )
        assert pii.resolve_release(run, IMAGE) == ("1.9.2", None)

    def test_git_tag_without_a_published_image_is_skipped(self):
        run = FakeRun(
            tags=["v1.9.1", "v1.9.2"],
            digests={f"{IMAGE}:latest": DIGEST_A, f"{IMAGE}:1.9.2": DIGEST_A},
        )
        assert pii.resolve_release(run, IMAGE) == ("1.9.2", None)

    def test_no_match_falls_back_with_warning(self):
        run = FakeRun(
            tags=["v1.9.2"], digests={f"{IMAGE}:latest": DIGEST_A, f"{IMAGE}:1.9.2": DIGEST_B}
        )
        version, warning = pii.resolve_release(run, IMAGE)
        assert version is None and "0" in warning

    def test_two_matches_fall_back(self):
        run = FakeRun(
            tags=["v1.9.1", "v1.9.2"],
            digests={
                f"{IMAGE}:latest": DIGEST_A,
                f"{IMAGE}:1.9.2": DIGEST_A,
                f"{IMAGE}:1.9.1": DIGEST_A,
            },
        )
        version, warning = pii.resolve_release(run, IMAGE)
        assert version is None and "2" in warning

    def test_prerelease_latest_is_ignored(self):
        run = FakeRun(
            tags=["v1.9.2", "v2.0.0-rc.1"],
            digests={
                f"{IMAGE}:latest": DIGEST_B,
                f"{IMAGE}:1.9.2": DIGEST_A,
                f"{IMAGE}:2.0.0-rc.1": DIGEST_B,
            },
        )
        assert pii.resolve_release(run, IMAGE)[0] is None
        assert not any(c[-1].endswith("rc.1") for c in run.calls if c[0] == "docker")

    def test_missing_latest_digest_falls_back(self):
        run = FakeRun(tags=["v1.9.2"], digests={f"{IMAGE}:1.9.2": DIGEST_A})
        assert pii.resolve_release(run, IMAGE)[0] is None

    def test_no_tags_falls_back(self):
        run = FakeRun(tags=[], digests={f"{IMAGE}:latest": DIGEST_A})
        assert pii.resolve_release(run, IMAGE)[0] is None

    def test_docker_unavailable_falls_back(self):
        run = FakeRun(tags=["v1.9.2"], fail_docker=True)
        version, warning = pii.resolve_release(run, IMAGE)
        assert version is None and "FileNotFoundError" in warning

    def test_malformed_digest_is_not_trusted(self):
        run = FakeRun(
            tags=["v1.9.2"],
            digests={f"{IMAGE}:latest": "", f"{IMAGE}:1.9.2": ""},
        )
        assert pii.resolve_release(run, IMAGE)[0] is None

    def test_raw_manifest_is_used_not_the_formatted_view(self):
        run = FakeRun(
            tags=["v1.9.2"], digests={f"{IMAGE}:latest": DIGEST_A, f"{IMAGE}:1.9.2": DIGEST_A}
        )
        pii.resolve_release(run, IMAGE)
        inspects = [c for c in run.calls if c[0] == "docker"]
        assert inspects and all("--raw" in c and "--format" not in c for c in inspects)

    def test_tool_output_in_the_warning_is_sanitised(self):
        def run(argv):
            raise subprocess.CalledProcessError(
                1, argv, "", "ERROR: x\x1b[31m ::set-output name=a::b\x00"
            )

        _, warning = pii.resolve_release(run, IMAGE)
        assert "::set" not in warning and "\x1b" not in warning and "\x00" not in warning

    def test_failed_command_is_named_in_the_warning(self):
        run = FakeRun(tags=["v1.9.2"], digests={})
        _, warning = pii.resolve_release(run, IMAGE)
        assert "docker buildx imagetools" in warning


@pytest.mark.unit
class TestBuild:
    MAIN = "vulnerabilities:\n  - id: CVE-2\n    purls:\n      - pkg:deb/debian/b\n"
    TAG = "vulnerabilities:\n  - id: CVE-1\n    purls:\n      - pkg:deb/debian/a\n"

    def healthy(self, tag_file):
        return FakeRun(
            tags=["v1.9.2"],
            digests={f"{IMAGE}:latest": DIGEST_A, f"{IMAGE}:1.9.2": DIGEST_A},
            tag_file=tag_file,
        )

    def test_union_is_written_when_release_resolves(self, tmp_path):
        main, out = tmp_path / "main.yaml", tmp_path / "out.yaml"
        main.write_text(self.MAIN)
        version, warning = pii.build(main, out, IMAGE, self.healthy(self.TAG))
        assert (version, warning) == ("1.9.2", None)
        assert {e["id"] for e in yaml.safe_load(out.read_text())["vulnerabilities"]} == {
            "CVE-1",
            "CVE-2",
        }

    def test_removal_on_main_still_suppressed_by_release_entries(self, tmp_path):
        main, out = tmp_path / "main.yaml", tmp_path / "out.yaml"
        main.write_text("vulnerabilities: []\n")
        pii.build(main, out, IMAGE, self.healthy(self.TAG))
        assert [e["id"] for e in yaml.safe_load(out.read_text())["vulnerabilities"]] == ["CVE-1"]

    @pytest.mark.parametrize(
        "tag_file", [None, "- just\n- a list\n", ": : not yaml : [", "vulnerabilities: [1, 2]\n"]
    )
    def test_missing_or_unusable_tag_file_falls_back_to_main_verbatim(self, tmp_path, tag_file):
        main, out = tmp_path / "main.yaml", tmp_path / "out.yaml"
        main.write_text(self.MAIN)
        version, warning = pii.build(main, out, IMAGE, self.healthy(tag_file))
        assert version is None and warning
        assert out.read_text() == self.MAIN

    def test_unresolved_release_falls_back_with_warning(self, tmp_path):
        main, out = tmp_path / "main.yaml", tmp_path / "out.yaml"
        main.write_text(self.MAIN)
        version, warning = pii.build(main, out, IMAGE, FakeRun(tags=[]))
        assert version is None and warning
        assert out.read_text() == self.MAIN

    def test_tag_content_is_only_read_never_executed(self, tmp_path):
        main, out = tmp_path / "main.yaml", tmp_path / "out.yaml"
        main.write_text(self.MAIN)
        run = self.healthy(self.TAG)
        pii.build(main, out, IMAGE, run)
        assert {c[0] for c in run.calls} <= {"git", "docker"}
        assert all(c[1] in {"ls-remote", "fetch", "show", "buildx"} for c in run.calls)

    def test_hostile_version_never_reaches_git(self):
        assert pii.read_release_ignore(FakeRun(), "1.0.0; rm -rf /") is None


@pytest.mark.unit
class TestReleaseNeeded:
    def report(self):
        def vuln(vuln_id, fixed="2.0", pkg="zlib1g"):
            return {
                "VulnerabilityID": vuln_id,
                "PkgName": pkg,
                "InstalledVersion": "1.0",
                "FixedVersion": fixed,
                "Severity": "HIGH",
            }

        return {
            "Results": [
                {
                    "Vulnerabilities": [
                        vuln("CVE-1"),
                        vuln("CVE-2"),
                        vuln("CVE-3", fixed=""),
                        vuln("CVE-4", pkg="`evil`\n@org/team"),
                    ]
                }
            ]
        }

    def test_lists_fixable_findings_suppressed_only_by_the_release_file(self):
        main = {"vulnerabilities": [entry("CVE-2", ["pkg:deb/debian/a"])]}
        release = {
            "vulnerabilities": [
                entry("CVE-1", ["pkg:deb/debian/a"]),
                entry("CVE-2", ["pkg:deb/debian/a"]),
                entry("CVE-3", ["pkg:deb/debian/a"]),
                entry("CVE-4", ["pkg:deb/debian/a"]),
            ]
        }
        rows = pii.release_needed(self.report(), main, release)
        assert [r[0] for r in rows] == ["CVE-1", "CVE-4"]

    def test_hostile_values_are_not_echoed(self):
        release = {"vulnerabilities": [entry("CVE-4", ["pkg:deb/debian/a"])]}
        text = pii.summary_markdown(pii.release_needed(self.report(), {}, release))
        assert "evil" not in text and "@org" not in text and "(unavailable)" in text

    def test_empty_summary(self):
        assert "No findings" in pii.summary_markdown([])

    def test_table_is_capped_and_omissions_are_counted(self):
        rows = [(f"CVE-1-{n}", "zlib1g", "1.0", "2.0", "HIGH") for n in range(130)]
        text = pii.summary_markdown(rows)
        assert text.count("| CVE-1-") == 100
        assert "30 more finding(s) omitted" in text
        assert "CVE-1-100 " not in text

    def test_table_at_the_cap_has_no_omission_note(self):
        rows = [(f"CVE-1-{n}", "zlib1g", "1.0", "2.0", "HIGH") for n in range(100)]
        assert "omitted" not in pii.summary_markdown(rows)

    def test_summary_states_release_needed(self):
        rows = [("CVE-1", "zlib1g", "1.0", "2.0", "HIGH")]
        assert "release needed" in pii.summary_markdown(rows)


@pytest.mark.unit
class TestIssue:
    def gh(self, existing):
        calls = []

        def run(argv):
            calls.append(list(argv))
            if argv[:3] == ["gh", "issue", "list"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps(existing), "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        return run, calls

    def bot_issue(self, number=3):
        return {
            "number": number,
            "title": pii.ISSUE_TITLE,
            "author": {"login": "app/github-actions"},
        }

    ROWS = [("CVE-1", "zlib1g", "1.0", "2.0", "HIGH")]

    def test_creates_when_missing(self):
        run, calls = self.gh([])
        pii.manage_issue("finos/open-resource-broker", self.ROWS, "body", run)
        assert calls[-1][:3] == ["gh", "issue", "create"]

    def test_updates_the_single_existing_issue(self):
        run, calls = self.gh([self.bot_issue(3)])
        pii.manage_issue("finos/open-resource-broker", self.ROWS, "body", run)
        assert calls[-1][:4] == ["gh", "issue", "edit", "3"]

    def test_closes_when_nothing_is_pending(self):
        run, calls = self.gh([self.bot_issue(3)])
        pii.manage_issue("finos/open-resource-broker", [], "body", run)
        assert calls[-1][:4] == ["gh", "issue", "close", "3"]

    def test_nothing_to_do_without_issue_or_rows(self):
        run, calls = self.gh([])
        pii.manage_issue("finos/open-resource-broker", [], "body", run)
        assert len(calls) == 1

    def test_user_created_issue_with_same_title_is_not_touched(self):
        foreign = {**self.bot_issue(9), "author": {"login": "someone"}}
        run, calls = self.gh([foreign])
        pii.manage_issue("finos/open-resource-broker", self.ROWS, "body", run)
        assert calls[-1][:3] == ["gh", "issue", "create"]


@pytest.mark.unit
class TestSummarizeCommand:
    """The summarize subcommand must never touch the issue when it cannot compute the delta."""

    def invoke(self, monkeypatch, tmp_path, release_doc, release="1.9.2", report=True):
        main = tmp_path / "main.yaml"
        main.write_text("vulnerabilities: []\n")
        rep = tmp_path / "report.json"
        if report:
            rep.write_text(json.dumps({"Results": []}))
        calls = []
        monkeypatch.setattr(pii, "read_release_ignore", lambda run, version: release_doc)
        monkeypatch.setattr(pii, "manage_issue", lambda *a, **k: calls.append(a))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        argv = [
            "x",
            "summarize",
            "--report",
            str(rep),
            "--main-file",
            str(main),
            "--release",
            release,
        ]
        monkeypatch.setattr(sys, "argv", [*argv, "--manage-issue"])
        return pii.main(), calls

    def test_unreadable_release_file_leaves_the_issue_alone(self, monkeypatch, tmp_path, capsys):
        code, calls = self.invoke(monkeypatch, tmp_path, None)
        assert code == 0 and calls == []
        assert "::warning::" in capsys.readouterr().out

    def test_unresolved_release_leaves_the_issue_alone(self, monkeypatch, tmp_path):
        code, calls = self.invoke(monkeypatch, tmp_path, {"vulnerabilities": []}, release="")
        assert code == 0 and calls == []

    def test_missing_report_leaves_the_issue_alone(self, monkeypatch, tmp_path):
        code, calls = self.invoke(monkeypatch, tmp_path, {"vulnerabilities": []}, report=False)
        assert code == 0 and calls == []

    def test_readable_release_file_manages_the_issue(self, monkeypatch, tmp_path):
        code, calls = self.invoke(monkeypatch, tmp_path, {"vulnerabilities": []})
        assert code == 0 and len(calls) == 1 and calls[0][1] == []
