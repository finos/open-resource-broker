"""Nothing is published unless the release smoke passed.

Reads the workflow files, so a job added to the release train without deciding
whether it publishes, or an edit that detaches a publishing job from the smoke,
fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "prod-release.yml"
ACTION = ROOT / ".github" / "actions" / "build-release-wheel" / "action.yml"

SMOKE = "release-smoke"

# Jobs that publish an artefact, directly or as a dependant of one. Each must be
# downstream of the smoke.
PUBLISHING = {
    "prod-pypi",
    "prod-containers",
    "github-assets",
    "prod-manifests",
    "sdk-publish",
    "sdk-assets",
}
# Jobs that publish nothing from the release wheel, with the reason.
EXEMPT = {
    "prod-docs": "publishes source documentation; depends only on the configuration",
    "sdk-bootstrap": "dispatch-only seed of the SDK registries",
    "sdk-spec-conformance": "checks the SDK spec and publishes nothing",
}
PIPELINE = {"config", "build-wheel", SMOKE, "release-complete"}


def _needs(job: dict) -> set[str]:
    needs = job.get("needs") or []
    return {needs} if isinstance(needs, str) else set(needs)


@pytest.fixture(scope="module")
def jobs() -> dict[str, dict]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _upstream(jobs: dict[str, dict], name: str) -> set[str]:
    seen: set[str] = set()
    stack = [name]
    while stack:
        for parent in _needs(jobs[stack.pop()]):
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def test_every_job_is_classified(jobs):
    classified = PUBLISHING | set(EXEMPT) | PIPELINE
    assert set(jobs) == classified, (
        f"classify new jobs as publishing, exempt or pipeline: {sorted(set(jobs) ^ classified)}"
    )


@pytest.mark.parametrize("name", sorted(PUBLISHING))
def test_publishing_job_is_downstream_of_the_smoke(jobs, name):
    assert SMOKE in _upstream(jobs, name), f"{name} can run without the release smoke passing"


@pytest.mark.parametrize("name", ["prod-pypi", "prod-containers", "github-assets"])
def test_artefact_jobs_wait_for_the_smoke_directly(jobs, name):
    assert SMOKE in _needs(jobs[name])


STATUS_FUNCTION = re.compile(r"\b(always|cancelled|failure|success)\s*\(")
WHEEL_ARTIFACT = "release-wheel-${{ needs.config.outputs.package-version }}"


@pytest.mark.parametrize("name", sorted(PUBLISHING))
def test_publishing_job_condition_cannot_bypass_its_needs(jobs, name):
    """A status function in ``if:`` lets a job run although a job it needs failed."""
    assert not STATUS_FUNCTION.search(str(jobs[name].get("if", ""))), jobs[name]["if"]


@pytest.mark.parametrize("name", ["prod-pypi", "prod-containers", "github-assets"])
def test_artefact_jobs_publish_the_wheel_the_smoke_checked(jobs, name):
    downloads = [
        step.get("with", {}).get("name")
        for step in jobs[name].get("steps", [])
        if "download-artifact" in str(step.get("uses"))
    ]
    assert WHEEL_ARTIFACT in downloads, downloads


def test_smoke_runs_on_the_built_wheel(jobs):
    assert "build-wheel" in _needs(jobs[SMOKE])
    assert jobs[SMOKE]["uses"].endswith("release-smoke.yml")


def test_release_outcome_cannot_hide_a_failed_smoke(jobs):
    complete = jobs["release-complete"]
    assert {"build-wheel", SMOKE} <= _needs(complete)
    skips = {s.strip() for s in str(complete["steps"][-1]["with"]["allowed-skips"]).split(",")}
    assert not ({"build-wheel", SMOKE} & skips)


def test_wheel_checksum_stays_out_of_the_published_artifact(jobs):
    action = yaml.safe_load(ACTION.read_text())
    uploads = [s for s in action["runs"]["steps"] if "upload-artifact" in str(s.get("uses"))]
    by_name = {s["with"]["name"]: s["with"]["path"] for s in uploads}
    assert by_name["release-wheel-${{ inputs.version }}"] == "dist/"
    sha_artifacts = [n for n in by_name if n.endswith("-sha256")]
    assert sha_artifacts, "the checksum has no artifact of its own"
    for job in jobs.values():
        for step in job.get("steps", []):
            assert "-sha256" not in str(step.get("with", {}).get("name", "")), (
                "a publishing job downloads the checksum artifact"
            )
