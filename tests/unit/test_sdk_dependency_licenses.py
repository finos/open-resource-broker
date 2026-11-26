"""Tests for the Java, Kotlin and C# SDK runtime licence gate.

The stub registry below holds the licence strings the real POMs and nuspecs
carry for every runtime coordinate in the committed lockfiles, so the real
lockfiles can be run through the gate without network access.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

_DEV_TOOLS_PATH = str(Path(__file__).parents[2] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

import sdk_dependency_licenses as gate  # type: ignore[import]
from check_runtime_licenses import SPDX_ALLOW, is_allowed  # type: ignore[import]

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "security-code.yml"
JAVA_LOCK = ROOT / "sdk" / "java" / "gradle.lockfile"
KOTLIN_LOCK = ROOT / "sdk" / "kotlin" / "gradle.lockfile"
CSHARP_LOCK = ROOT / "sdk" / "csharp" / "src" / "FINOS.OpenResourceBroker" / "packages.lock.json"

APACHE = "The Apache Software License, Version 2.0"
APACHE_COMMA = "Apache License, Version 2.0"
_AWS = "software.amazon.awssdk"
_JACKSON = "com.fasterxml.jackson"

# group:artifact -> (<license><name> values, parent group:artifact), as published.
POMS: dict[str, tuple[list[str], str | None]] = {
    f"{_JACKSON}.core:jackson-annotations": ([APACHE], f"{_JACKSON}:jackson-parent"),
    f"{_JACKSON}.core:jackson-core": ([APACHE], f"{_JACKSON}:jackson-base"),
    f"{_JACKSON}.core:jackson-databind": ([APACHE], f"{_JACKSON}:jackson-base"),
    f"{_JACKSON}.datatype:jackson-datatype-jsr310": (
        [],
        f"{_JACKSON}.module:jackson-modules-java8",
    ),
    f"{_JACKSON}.module:jackson-modules-java8": ([], f"{_JACKSON}:jackson-base"),
    f"{_JACKSON}:jackson-base": ([APACHE], f"{_JACKSON}:jackson-bom"),
    f"{_JACKSON}:jackson-bom": ([APACHE_COMMA], f"{_JACKSON}:jackson-parent"),
    "com.google.code.findbugs:jsr305": ([APACHE], "org.sonatype.oss:oss-parent"),
    "com.google.code.gson:gson": (["Apache-2.0"], "com.google.code.gson:gson-parent"),
    "com.google.errorprone:error_prone_annotations": (
        ["Apache 2.0"],
        "com.google.errorprone:error_prone_parent",
    ),
    "com.squareup.okhttp3:logging-interceptor": ([APACHE], None),
    "com.squareup.okhttp3:okhttp": ([APACHE], None),
    "com.squareup.okio:okio": ([APACHE], None),
    "com.squareup.okio:okio-jvm": ([APACHE], None),
    "jakarta.annotation:jakarta.annotation-api": (
        ["EPL 2.0", "GPL2 w/ CPE"],
        "org.eclipse.ee4j:project",
    ),
    "javax.annotation:javax.annotation-api": (
        ["CDDL + GPLv2 with classpath exception"],
        "net.java:jvnet-parent",
    ),
    "org.jetbrains.kotlin:kotlin-stdlib": (["Apache-2.0"], None),
    "org.jetbrains.kotlin:kotlin-stdlib-jdk7": (["The Apache License, Version 2.0"], None),
    "org.jetbrains.kotlin:kotlin-stdlib-jdk8": (["The Apache License, Version 2.0"], None),
    "org.jetbrains.kotlinx:kotlinx-coroutines-bom": (["Apache-2.0"], None),
    "org.jetbrains.kotlinx:kotlinx-coroutines-core": (["Apache-2.0"], None),
    "org.jetbrains.kotlinx:kotlinx-coroutines-core-jvm": (["Apache-2.0"], None),
    "org.jetbrains:annotations": ([APACHE], None),
    "org.openapitools:jackson-databind-nullable": (
        ["Apache License 2.0"],
        f"{_JACKSON}:jackson-base",
    ),
    "org.reactivestreams:reactive-streams": (["MIT-0"], None),
    "org.slf4j:slf4j-api": ([], "org.slf4j:slf4j-parent"),
    "org.slf4j:slf4j-parent": (["MIT License"], None),
    f"{_AWS}:aws-sdk-java-pom": ([APACHE_COMMA], None),
    f"{_AWS}:core": ([], f"{_AWS}:aws-sdk-java-pom"),
    f"{_AWS}:third-party": ([], f"{_AWS}:aws-sdk-java-pom"),
    f"{_AWS}:http-client-spi": ([], f"{_AWS}:aws-sdk-java-pom"),
    f"{_AWS}:utils": ([], f"{_AWS}:aws-sdk-java-pom"),
    f"{_AWS}:third-party-jackson-core": ([], f"{_AWS}:third-party"),
}
for _artifact in (
    "annotations auth checksums checksums-spi endpoints-spi eventstream http-auth http-auth-aws "
    "http-auth-aws-eventstream http-auth-spi identity-spi json-utils metrics-spi profiles regions "
    "retries retries-spi sdk-core"
).split():
    POMS[f"{_AWS}:{_artifact}"] = ([], f"{_AWS}:core")

# NuGet package id (lower case) -> <license type="expression"> value.
NUSPEC_LICENSES = {
    "awssdk.core": "Apache-2.0",
    "awssdk.extensions.numerics": "Apache-2.0",
    "system.buffers": "MIT",
    "system.io.hashing": "MIT",
    "system.io.pipelines": "MIT",
    "system.memory": "MIT",
    "system.text.encodings.web": "MIT",
    "system.text.json": "MIT",
}


def _pom_xml(names: list[str], parent: str | None) -> bytes:
    licenses = "".join(f"<license><name>{n}</name></license>" for n in names)
    block = f"<licenses>{licenses}</licenses>" if names else ""
    par = ""
    if parent:
        group, artifact = parent.split(":")
        par = (
            f"<parent><groupId>{group}</groupId><artifactId>{artifact}</artifactId>"
            "<version>1</version></parent>"
        )
    return f'<project xmlns="http://maven.apache.org/POM/4.0.0">{par}{block}</project>'.encode()


def stub_fetch(url: str) -> bytes:
    if url.startswith(gate.MAVEN_CENTRAL):
        parts = url[len(gate.MAVEN_CENTRAL) + 1 :].split("/")
        group, artifact = ".".join(parts[:-3]), parts[-3]
        if f"{group}:{artifact}" in POMS:
            return _pom_xml(*POMS[f"{group}:{artifact}"])
    elif url.startswith(gate.NUGET_FLAT):
        package = url.split("/")[-3]
        if package in NUSPEC_LICENSES:
            return (
                '<package xmlns="http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd">'
                f'<metadata><license type="expression">{NUSPEC_LICENSES[package]}</license>'
                "</metadata></package>"
            ).encode()
    raise urllib.error.HTTPError(url, 404, "not found", None, None)  # type: ignore[arg-type]


@pytest.mark.unit
class TestRealLockfiles:
    def test_every_runtime_coordinate_is_allowed(self, tmp_path):
        summary = tmp_path / "summary.md"
        sdks = {
            "Java": ("maven", JAVA_LOCK),
            "Kotlin": ("maven", KOTLIN_LOCK),
            "C#": ("nuget", CSHARP_LOCK),
        }
        assert gate.run(sdks, stub_fetch, summary) == 0
        text = summary.read_text()
        for sdk in sdks:
            assert f"### {sdk} runtime dependency licenses" in text
        assert text.count("0 violation(s)") == 3

    def test_summary_lists_name_version_and_license(self, tmp_path):
        summary = tmp_path / "summary.md"
        gate.run({"Java": ("maven", JAVA_LOCK)}, stub_fetch, summary)
        text = summary.read_text()
        assert "| javax.annotation:javax.annotation-api | 1.3.2 |" in text
        assert "CDDL-1.1 OR GPL-2.0-only WITH Classpath-exception-2.0" in text
        assert "| org.slf4j:slf4j-api | 1.7.36 | MIT | yes |" in text

    def test_dual_licensed_and_bom_entries_resolve(self):
        java = dict(gate.parse_gradle_lockfile(JAVA_LOCK))
        kotlin = dict(gate.parse_gradle_lockfile(KOTLIN_LOCK))
        for packages, name in (
            (java, "jakarta.annotation:jakarta.annotation-api"),
            (java, "javax.annotation:javax.annotation-api"),
            (java, "org.reactivestreams:reactive-streams"),
            (java, f"{_JACKSON}:jackson-bom"),
            (kotlin, "org.jetbrains.kotlinx:kotlinx-coroutines-bom"),
        ):
            license_str = gate.maven_license(name, packages[name], stub_fetch)
            assert license_str != gate.UNRESOLVED
            assert is_allowed(license_str), (name, license_str)

    def test_epl_2_is_allowlisted(self):
        assert "EPL-2.0" in SPDX_ALLOW
        assert is_allowed("EPL-2.0")

    def test_gpl_exception_alone_is_not_allowed(self):
        assert not is_allowed("GPL-2.0-only WITH Classpath-exception-2.0")


@pytest.mark.unit
class TestLockfileParsing:
    def test_gradle_keeps_only_runtime_classpath(self, tmp_path):
        lock = tmp_path / "gradle.lockfile"
        lock.write_text(
            "# comment\n"
            "a:runtime:1=compileClasspath,runtimeClasspath,testRuntimeClasspath\n"
            "a:testonly:1=testCompileClasspath,testRuntimeClasspath\n"
            "a:buildtool:1=kotlinCompilerClasspath\n"
            "a:compileonly:1=compileClasspath\n"
            "empty=annotationProcessor\n"
        )
        assert gate.parse_gradle_lockfile(lock) == [("a:runtime", "1")]

    def test_real_gradle_lockfiles_exclude_test_dependencies(self):
        java = {n for n, _ in gate.parse_gradle_lockfile(JAVA_LOCK)}
        assert "junit:junit" not in java
        assert not any(n.startswith("org.junit") for n in java)
        kotlin = {n for n, _ in gate.parse_gradle_lockfile(KOTLIN_LOCK)}
        assert not any(n.startswith("io.mockk") for n in kotlin)
        assert not any(n.startswith("org.bouncycastle") for n in kotlin)

    def test_nuget_keeps_runtime_types_and_skips_projects(self, tmp_path):
        lock = tmp_path / "packages.lock.json"
        lock.write_text(
            json.dumps(
                {
                    "version": 1,
                    "dependencies": {
                        "net8.0": {
                            "Direct.Pkg": {"type": "Direct", "resolved": "1.0.0"},
                            "Trans.Pkg": {"type": "Transitive", "resolved": "2.0.0"},
                            "Central.Pkg": {"type": "CentralTransitive", "resolved": "3.0.0"},
                            "My.Project": {"type": "Project"},
                        }
                    },
                }
            )
        )
        assert gate.parse_nuget_lockfile(lock) == [
            ("Central.Pkg", "3.0.0"),
            ("Direct.Pkg", "1.0.0"),
            ("Trans.Pkg", "2.0.0"),
        ]


@pytest.mark.unit
class TestLicenseResolution:
    def test_multiple_pom_licenses_mean_or(self):
        assert gate.to_expression(["MIT", "GPL-3.0-only"]) == "(MIT) OR (GPL-3.0-only)"
        assert is_allowed(gate.to_expression(["MIT", "GPL-3.0-only"]))

    def test_all_disallowed_alternatives_fail(self):
        assert not is_allowed(gate.to_expression(["GPL-3.0-only", "AGPL-3.0-only"]))

    def test_unmapped_name_fails_closed(self):
        assert not is_allowed(gate.to_expression(["Some Bespoke License"]))

    def test_unmapped_name_cannot_inject_expression_syntax(self):
        # A POM name is not trusted to carry expression syntax or parentheses.
        assert not is_allowed(gate.to_expression(["MIT) OR (MIT"]))
        assert not is_allowed(gate.to_expression(["GPL-3.0-only OR MIT"]))

    def test_no_license_is_unresolved(self):
        assert gate.to_expression([]) == gate.UNRESOLVED
        assert not is_allowed(gate.UNRESOLVED)

    def test_parent_walk_finds_inherited_license(self):
        names = gate.pom_license_names("org.slf4j", "slf4j-api", "1.7.36", stub_fetch)
        assert names == ["MIT License"]

    def test_parent_cycle_terminates(self):
        def cyclic(url: str) -> bytes:
            return _pom_xml([], "g:a")

        assert gate.pom_license_names("g", "a", "1", cyclic) == []

    def test_property_version_stops_the_walk(self):
        assert gate.pom_license_names("g", "a", "${revision}", stub_fetch) == []

    def test_missing_pom_is_unresolved(self):
        assert gate.maven_license("no.such:pkg", "1", stub_fetch) == gate.UNRESOLVED

    def test_nuspec_without_expression_is_unresolved(self):
        def file_license(url: str) -> bytes:
            return b'<package><metadata><license type="file">LICENSE.txt</license></metadata></package>'

        assert gate.nuget_license("Some.Pkg", "1.0.0", file_license) == gate.UNRESOLVED


@pytest.mark.unit
class TestFailClosed:
    def test_empty_runtime_set_is_an_error(self, tmp_path):
        lock = tmp_path / "gradle.lockfile"
        lock.write_text("a:b:1=testRuntimeClasspath\n")
        assert gate.run({"Java": ("maven", lock)}, stub_fetch, None) == 1

    def test_disallowed_license_fails_the_run(self, tmp_path):
        lock = tmp_path / "gradle.lockfile"
        lock.write_text("x.y:gpl-lib:1=runtimeClasspath\n")

        def gpl(url: str) -> bytes:
            return _pom_xml(["GPL-3.0-only"], None)

        assert gate.run({"Java": ("maven", lock)}, gpl, None) == 1

    def test_unreachable_registry_fails_the_run(self, tmp_path):
        lock = tmp_path / "gradle.lockfile"
        lock.write_text("x.y:lib:1=runtimeClasspath\n")

        def down(url: str) -> bytes:
            raise RuntimeError("offline")

        assert gate.run({"Java": ("maven", lock)}, down, None) == 1

    @pytest.mark.parametrize(
        "url",
        [
            "http://repo1.maven.org/maven2/a/b.pom",
            "https://evil.example.com/a.pom",
            "https://repo1.maven.org.evil.example.com/a.pom",
            "file:///etc/passwd",
            "https://evil.example.com\\@repo1.maven.org/x",
            "https://repo1.maven.org:8443/x",
            "https://user@repo1.maven.org/x",
        ],
    )
    def test_fetch_refuses_other_hosts_and_schemes(self, url):
        with pytest.raises(ValueError):
            gate.fetch_url(url)


class _FakeResponse:
    def __init__(self, data: bytes = b"<project/>") -> None:
        self._data = data

    def read(self, _n: int = -1) -> bytes:
        return self._data

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.mark.unit
class TestRedirectsAndXml:
    @pytest.mark.parametrize(
        "target",
        ["https://evil.example.com/a.pom", "http://repo1.maven.org/a.pom", "file:///etc/passwd"],
    )
    def test_redirect_to_disallowed_target_is_refused(self, target):
        handler = gate._AllowedRedirects()
        request = urllib.request.Request("https://repo1.maven.org/a.pom")
        with pytest.raises(ValueError):
            handler.redirect_request(request, None, 302, "Found", {}, target)

    def test_redirect_to_allowed_host_is_followed(self):
        handler = gate._AllowedRedirects()
        request = urllib.request.Request("https://repo1.maven.org/a.pom")
        new = handler.redirect_request(
            request, None, 302, "Found", {}, "https://api.nuget.org/b.nuspec"
        )
        assert new is not None
        assert new.full_url == "https://api.nuget.org/b.nuspec"

    def test_fetch_url_uses_the_redirect_checking_opener(self, monkeypatch):
        seen = []

        def fake_open(url, timeout):  # type: ignore[no-untyped-def]
            seen.append(url)
            return _FakeResponse()

        monkeypatch.setattr(gate._OPENER, "open", fake_open)
        assert gate.fetch_url("https://repo1.maven.org/a.pom") == b"<project/>"
        assert seen == ["https://repo1.maven.org/a.pom"]

    def test_doctype_is_refused(self):
        with pytest.raises(ValueError):
            gate.parse_xml(b'<!DOCTYPE project SYSTEM "http://evil.example.com/x.dtd"><project/>')

    def test_billion_laughs_is_refused(self):
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
            b"<project>&lol3;</project>"
        )
        with pytest.raises(ValueError):
            gate.parse_xml(bomb)

    def test_utf16_encoded_doctype_is_refused(self):
        payload = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE p [<!ENTITY a "b">]><p/>'
        with pytest.raises(ValueError):
            gate.parse_xml(payload.encode("utf-16"))

    def test_bomb_in_a_pom_is_an_unresolved_violation(self):
        def bomb(url: str) -> bytes:
            return b'<!DOCTYPE p [<!ENTITY a "b">]><project/>'

        assert gate.maven_license("g:a", "1", bomb) == gate.UNRESOLVED

    def test_many_newlines_parse_in_bounded_time(self):
        started = time.monotonic()
        root = gate.parse_xml(b"<p>" + b"\n" * 1_000_000 + b"</p>")
        assert time.monotonic() - started < 5
        assert len(root.text or "") == 1_000_000

    def test_unknown_encoding_is_a_value_error(self):
        with pytest.raises(ValueError):
            gate.parse_xml(b'<?xml version="1.0" encoding="x-bogus"?><p/>')

    def test_unknown_encoding_marks_the_package_unresolved(self):
        def bogus(url: str) -> bytes:
            return b'<?xml version="1.0" encoding="x-bogus"?><project/>'

        assert gate.maven_license("g:a", "1", bogus) == gate.UNRESOLVED
        assert gate.nuget_license("P", "1", bogus) == gate.UNRESOLVED

    def test_plain_pom_still_parses(self):
        assert gate.parse_xml(_pom_xml(["MIT"], None)).tag.endswith("project")


@pytest.mark.unit
class TestWorkflowWiring:
    def _job(self):
        return yaml.safe_load(WORKFLOW.read_text())["jobs"]["sdk-license-gate"]

    def test_job_is_advisory_and_least_privilege(self):
        job = self._job()
        assert job["continue-on-error"] is True
        assert job["permissions"] == {"contents": "read"}

    def test_checkout_is_sha_pinned_without_persisted_credentials(self):
        checkout = self._job()["steps"][0]
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
        assert checkout["with"]["persist-credentials"] is False

    def test_job_runs_the_script_on_all_three_lockfiles(self):
        run = "\n".join(s.get("run", "") for s in self._job()["steps"])
        for fragment in (
            "sdk_dependency_licenses.py",
            "sdk/java/gradle.lockfile",
            "sdk/kotlin/gradle.lockfile",
            "packages.lock.json",
        ):
            assert fragment in run
        assert "github.event" not in run

    def test_defusedxml_install_is_hash_locked(self):
        run = "\n".join(s.get("run", "") for s in self._job()["steps"])
        assert "--require-hashes --no-deps -r .github/requirements/defusedxml.txt" in run
        lock = (ROOT / ".github/requirements/defusedxml.txt").read_text()
        assert "defusedxml==" in lock
        assert "--hash=sha256:" in lock

    def test_script_is_a_trigger_path(self):
        text = WORKFLOW.read_text()
        assert text.count("'dev-tools/ci/sdk_dependency_licenses.py'") == 2
