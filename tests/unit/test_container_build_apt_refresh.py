"""Guard the OS-package refresh of the container image and its weekly scan.

The ``apt-get upgrade`` layer of the Dockerfile is cached across CI builds, so
without a changing build argument a cached build can ship packages that Debian
has since patched. Every CI build passes ``APT_REFRESH`` (the build date) to
bust that layer, and the release workflow uses one value for both its scan-gate
build and its push build so the pushed image reuses the apt layer that was
scanned. A weekly job scans the published ``:latest`` image.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
DOCKERFILE = ROOT / "Dockerfile"
ACTION = ROOT / ".github" / "actions" / "container-build" / "action.yml"
PROD_RELEASE = WORKFLOWS / "prod-release.yml"
SECURITY_CODE = WORKFLOWS / "security-code.yml"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    # PyYAML parses the bare key ``on`` as boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _buildx_commands(script: str) -> list[str]:
    """Return each ``docker buildx build`` invocation with its continuation lines."""
    commands = []
    lines = script.splitlines()
    for i, line in enumerate(lines):
        if "docker buildx build" not in line:
            continue
        cmd = [line]
        j = i
        while lines[j].rstrip().endswith("\\"):
            j += 1
            cmd.append(lines[j])
        commands.append("\n".join(cmd))
    return commands


@pytest.mark.unit
def test_dockerfile_apt_layer_depends_on_apt_refresh() -> None:
    lines = DOCKERFILE.read_text().splitlines()
    upgrade = next(i for i, line in enumerate(lines) if "apt-get upgrade" in line)
    run_start = max(i for i in range(upgrade + 1) if lines[i].startswith("RUN "))
    arg = next(i for i, line in enumerate(lines) if re.match(r"ARG APT_REFRESH\b", line))

    assert arg < run_start
    assert "APT_REFRESH" in "\n".join(lines[run_start : upgrade + 1])


@pytest.mark.unit
def test_container_build_action_passes_apt_refresh_to_every_build() -> None:
    action = _load(ACTION)
    scripts = [
        s["run"] for s in action["runs"]["steps"] if "docker buildx build" in s.get("run", "")
    ]
    assert scripts

    for script in scripts:
        assert re.search(r'^\s*APT_REFRESH="\$\(date -u \+%Y-%m-%d\)"\s*$', script, re.M)
        common_args = re.search(r"common_args=\((.*?)\n\s*\)", script, re.S)
        assert common_args and '--build-arg "APT_REFRESH=$APT_REFRESH"' in common_args.group(1)
        builds = _buildx_commands(script)
        assert builds
        for build in builds:
            assert '"${common_args[@]}"' in build


@pytest.mark.unit
def test_prod_release_uses_one_apt_refresh_for_scan_and_push() -> None:
    workflow = _load(PROD_RELEASE)
    for name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        build_idx = [i for i, s in enumerate(steps) if "docker buildx build" in s.get("run", "")]
        if not build_idx:
            continue

        setters = [
            i
            for i, s in enumerate(steps)
            if "APT_REFRESH=" in s.get("run", "") and "GITHUB_ENV" in s["run"]
        ]
        assert len(setters) == 1, f"{name}: APT_REFRESH must be set exactly once"
        setter = steps[setters[0]]
        assert "if" not in setter, f"{name}: the APT_REFRESH step must run for every matrix entry"
        assert setters[0] < min(build_idx)

        for i in build_idx:
            for build in _buildx_commands(steps[i]["run"]):
                assert '--build-arg "APT_REFRESH=${APT_REFRESH:?}"' in build, (
                    f"{name}: {steps[i]['name']}"
                )
            assert "APT_REFRESH=$(" not in steps[i]["run"]


@pytest.mark.unit
def test_published_image_is_scanned_weekly() -> None:
    workflow = _load(SECURITY_CODE)
    assert any("cron" in entry for entry in workflow["on"]["schedule"])

    jobs = [
        job
        for job in workflow["jobs"].values()
        if any(
            ":latest" in str(s.get("with", {}).get("image-ref", "")) for s in job.get("steps", [])
        )
    ]
    assert len(jobs) == 1
    job = jobs[0]

    assert "github.event_name == 'schedule'" in job["if"]
    assert job["permissions"] == {
        "contents": "read",
        "packages": "read",
        "security-events": "write",
    }

    steps = job["steps"]
    scan = next(s for s in steps if "trivy-action" in s.get("uses", ""))
    assert scan["with"]["trivyignores"] == ".trivyignore.yaml"
    assert str(scan["with"]["exit-code"]) == "1"
    assert scan["env"]["TRIVY_PLATFORM"] == "linux/amd64"

    upload = next(s for s in steps if "upload-sarif" in s.get("uses", ""))
    assert upload["if"] == "always()"
    assert upload["with"]["category"] == "published-image-latest"
    assert upload["with"]["sarif_file"] == scan["with"]["output"]

    checkout = next(s for s in steps if "actions/checkout" in s.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False
    assert not any("docker" in s.get("run", "") for s in steps)
