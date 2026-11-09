"""Validation and classification of security exceptions that have a published fix.

Package and version strings come from OSV records, so they are checked before
they reach an issue body, a commit or a pull request. Anything that fails is a
manual case with a fixed reason code and none of the record text is echoed.
"""

import re
import sys
from pathlib import Path

import pytest

_DEV_TOOLS_PATH = str(Path(__file__).parents[3] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

import check_security_exceptions as audit  # type: ignore[import]  # noqa: E402

DEB = ["pkg:deb/debian/libfoo1"]
HOSTILE_NAME = "`evil`\n@org/team"
HOSTILE_VERSION = "1.0\n- [click](http://x)"
ALLOWED_BULLET = re.compile(
    r"- (`[a-z0-9+.\-]+` fixed in `[0-9][A-Za-z0-9.+~:\-]*`"
    r"|remove \.trivyignore\.yaml entry CVE-\d+-\d+ \(deb: [a-z0-9+., \-]+\)"
    r"|manual: [A-Z_]+"
    r"|\(package or version details withheld: failed validation\))"
)


def osv_debian_record(*fixes: tuple[str, str]) -> dict:
    return {
        "affected": [
            {
                "package": {"ecosystem": "Debian:13", "name": name},
                "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": ver}]}],
            }
            for name, ver in fixes
        ]
    }


@pytest.mark.unit
class TestFullMatchAndLengthCap:
    @pytest.mark.parametrize("vuln_id", ["CVE-2026-1234\n", "GHSA-8mgp-746c-j5xp\n", "CVE-2026-1 "])
    def test_vuln_id_rejects_trailing_newline_and_space(self, vuln_id):
        assert audit.VULN_ID_RE.match(vuln_id) is None
        assert not audit.is_valid_vuln_id(vuln_id)

    def test_vuln_id_length_cap(self):
        assert not audit.is_valid_vuln_id("CVE-2026-" + "1" * 200)
        assert audit.is_valid_vuln_id("CVE-2026-1234")

    @pytest.mark.parametrize("name", ["libfoo1", "libstdc++6", "zlib1g", "a"])
    def test_deb_package_accepts(self, name):
        assert audit.valid_deb_package(name)

    @pytest.mark.parametrize(
        "name", ["libfoo1\n", "Lib", "`x`", "@org/team", "", "-x", "a" * 101, 5, None]
    )
    def test_deb_package_rejects(self, name):
        assert not audit.valid_deb_package(name)

    @pytest.mark.parametrize("version", ["1:1.3.dfsg+really1.3.1-1+deb13u1", "2.41-12+deb13u4"])
    def test_deb_version_accepts(self, version):
        assert audit.valid_deb_version(version)

    @pytest.mark.parametrize("version", ["1.0\n", "v1", "1.0 2", "1" * 101, "", "1.0`"])
    def test_deb_version_rejects(self, version):
        assert not audit.valid_deb_version(version)

    def test_pypi_validators(self):
        assert audit.valid_pypi_package("Foo_Bar")
        assert not audit.valid_pypi_package("foo\n")
        assert not audit.valid_pypi_package("`foo`")
        assert audit.valid_pypi_version("1.2.3")
        assert not audit.valid_pypi_version("1.2.3\n")
        assert not audit.valid_pypi_version("v1.2.3 beta")
        assert not audit.valid_pypi_version("1" * 101)


@pytest.mark.unit
class TestClassification:
    def test_debian_cve_is_mechanical(self):
        p = audit.classify_exception("CVE-2026-1", DEB, [("foo", "1.2-3")])
        assert p.mechanical and p.reason is None
        assert p.deb_packages == ("libfoo1",)

    def test_ghsa_allowlist_is_manual(self):
        p = audit.classify_exception("GHSA-8mgp-746c-j5xp", [], [("x", "1")], in_trivyignore=False)
        assert p.reason is audit.ManualReason.GHSA_ALLOWLIST

    def test_pypi_is_manual(self):
        p = audit.classify_exception("CVE-2026-1", ["pkg:pypi/filelock"], [("filelock", "4.2.1")])
        assert p.reason is audit.ManualReason.NON_DEB_ECOSYSTEM
        assert p.fixes == (("filelock", "4.2.1"),)

    def test_mixed_ecosystems_are_manual(self):
        p = audit.classify_exception("CVE-2026-1", [*DEB, "pkg:pypi/x"], [("foo", "1")])
        assert not p.mechanical

    def test_no_purl_is_manual(self):
        assert (
            audit.classify_exception("CVE-2026-1", [], [("foo", "1")]).reason
            is audit.ManualReason.NO_PURL
        )

    def test_invalid_package_and_version(self):
        assert (
            audit.classify_exception("CVE-2026-1", DEB, [(HOSTILE_NAME, "1.0")]).reason
            is audit.ManualReason.INVALID_PACKAGE
        )
        assert (
            audit.classify_exception("CVE-2026-1", DEB, [("foo", HOSTILE_VERSION)]).reason
            is audit.ManualReason.INVALID_VERSION
        )
        assert (
            audit.classify_exception("CVE-2026-1", ["pkg:pypi/Bad Name"], [("`x`", "1")]).reason
            is audit.ManualReason.INVALID_PACKAGE
        )

    def test_invalid_purl_package_is_manual(self):
        p = audit.classify_exception("CVE-2026-1", ["pkg:deb/debian/Bad%0A"], [("foo", "1")])
        assert p.reason is audit.ManualReason.INVALID_PACKAGE

    def test_invalid_vulnerability_id_is_refused(self):
        with pytest.raises(ValueError):
            audit.classify_exception("CVE-2026-1\n", DEB, [("foo", "1")])
        with pytest.raises(ValueError):
            audit.classify_exception("`x`", DEB, [("foo", "1")])

    def test_reasons_are_a_fixed_enum(self):
        assert {r.value for r in audit.ManualReason} == {
            "GHSA_ALLOWLIST",
            "NON_DEB_ECOSYSTEM",
            "INVALID_PACKAGE",
            "INVALID_VERSION",
            "NO_PURL",
        }


@pytest.mark.unit
class TestHostileOsvRecord:
    """A hostile record must not reach the issue text, the PR text or the commit."""

    @pytest.fixture
    def proposals(self, monkeypatch):
        records = {
            "DEBIAN-CVE-2026-1": osv_debian_record((HOSTILE_NAME, "1.0")),
            "DEBIAN-CVE-2026-2": osv_debian_record(("foo", HOSTILE_VERSION)),
            "DEBIAN-CVE-2026-3": osv_debian_record(("x" * 10_000, "1.0")),
            "DEBIAN-CVE-2026-4": osv_debian_record(("libfoo1\n", "1.0")),
            "DEBIAN-CVE-2026-5": osv_debian_record(("good", "1.0-1"), (HOSTILE_NAME, "2")),
        }
        monkeypatch.setattr(audit, "query_osv", lambda vuln_id: records.get(vuln_id))
        purls = {f"CVE-2026-{n}": DEB for n in range(1, 6)}
        return audit.plan_exceptions(purls, set(purls))

    def test_hostile_entries_are_manual_with_enum_reasons(self, proposals):
        by_id = {p.vuln_id: p for p in proposals}
        assert by_id["CVE-2026-1"].reason is audit.ManualReason.INVALID_PACKAGE
        assert by_id["CVE-2026-2"].reason is audit.ManualReason.INVALID_VERSION
        assert by_id["CVE-2026-3"].reason is audit.ManualReason.INVALID_PACKAGE
        assert by_id["CVE-2026-4"].reason is audit.ManualReason.INVALID_PACKAGE
        assert by_id["CVE-2026-5"].reason is audit.ManualReason.INVALID_PACKAGE

    def test_issue_bodies_contain_only_validated_tokens(self, proposals):
        for proposal in proposals:
            body = audit.issue_body(proposal)
            for needle in ("@org", "evil", "click", "http", "xxxx", "libfoo1\n"):
                assert needle not in body
            for line in body.splitlines():
                if line.startswith("- "):
                    assert ALLOWED_BULLET.fullmatch(line), line

    def test_dry_run_issue_output_has_no_hostile_text(self, proposals, capsys):
        for proposal in proposals:
            audit.ensure_issue(proposal, dry_run=True)
        out = capsys.readouterr().out
        assert "@org" not in out and "evil" not in out and "xxxx" not in out
        assert "INVALID_PACKAGE" in out

    def test_valid_fix_survives_next_to_a_hostile_one(self, proposals):
        p = next(p for p in proposals if p.vuln_id == "CVE-2026-5")
        assert p.fixes == (("good", "1.0-1"),)


@pytest.mark.unit
class TestIssueBody:
    def test_mechanical_body_has_proposed_change_and_footer(self):
        body = audit.issue_body(audit.classify_exception("CVE-2026-1", DEB, [("foo", "1.2-3")]))
        assert "- remove .trivyignore.yaml entry CVE-2026-1 (deb: libfoo1)" in body
        assert "`make security-exception-fixes`" in body
        assert "`make security-exception-fixes APPLY=1`" in body

    def test_manual_body_names_the_reason_only(self):
        body = audit.issue_body(
            audit.classify_exception("CVE-2026-1", ["pkg:pypi/filelock"], [("filelock", "4.2.1")])
        )
        assert "- manual: NON_DEB_ECOSYSTEM" in body
        assert "make security-exception-fixes" not in body
