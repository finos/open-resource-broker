"""Guard the trust boundary of the container build workflow.

Images may only be published for commits whose Unit Tests push run passed, and
nothing privileged may run on a pull request. These tests parse the workflow
YAML so a later edit cannot silently reintroduce a ``workflow_run`` trigger, an
ungated publish job, or write permissions on pull-request builds.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

PUBLISH_CONDITION = (
    "(github.event_name == 'push' && (github.ref == 'refs/heads/main' || github.ref == 'refs/heads/develop')) || "
    "(github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main')"
)
WRITE_PERMISSIONS = {"packages", "id-token", "attestations"}


def _load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    # PyYAML parses the bare key ``on`` as boolean True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _transitive_needs(jobs: dict, name: str) -> set[str]:
    seen: set[str] = set()
    stack = _needs(jobs[name])
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(_needs(jobs[n]))
    return seen


def _normalize(expr: str) -> str:
    return " ".join(str(expr).split())


@pytest.fixture(scope="module")
def workflow() -> dict:
    return _load("dev-containers.yml")


@pytest.mark.unit
def test_no_workflow_run_trigger_or_always(workflow: dict) -> None:
    assert "workflow_run" not in workflow["on"]
    assert "always()" not in (WORKFLOWS / "dev-containers.yml").read_text()


@pytest.mark.unit
def test_tests_gate_is_unconditional_and_bounded(workflow: dict) -> None:
    gate = workflow["jobs"]["tests-gate"]
    assert "if" not in gate
    # Poll budget is at most 150 x 30s = 75 minutes.
    assert gate["timeout-minutes"] == 90
    step_env = next(s for s in gate["steps"] if "env" in s)["env"]
    assert int(step_env["MAX_POLLS"]) * int(step_env["POLL_INTERVAL"]) <= 150 * 30
    assert int(step_env["MAX_MISSING_POLLS"]) * int(step_env["POLL_INTERVAL"]) == 300


@pytest.mark.unit
def test_every_job_is_downstream_of_the_gate(workflow: dict) -> None:
    jobs = workflow["jobs"]
    for name in jobs:
        if name in ("tests-gate", "config"):
            continue
        assert "tests-gate" in _transitive_needs(jobs, name), name


@pytest.mark.unit
def test_publish_condition_and_never_on_pull_request(workflow: dict) -> None:
    publish = workflow["jobs"]["container-publish"]
    assert _normalize(publish["if"]) == PUBLISH_CONDITION
    assert "pull_request" not in publish["if"]
    for name in ("container-manifest", "container-validation"):
        assert "container-publish" in _needs(workflow["jobs"][name])


@pytest.mark.unit
def test_write_permissions_only_on_publish(workflow: dict) -> None:
    for name, job in workflow["jobs"].items():
        perms = job.get("permissions") or {}
        if not isinstance(perms, dict):
            continue
        writes = {k for k, v in perms.items() if v == "write"}
        if writes & WRITE_PERMISSIONS:
            assert name in ("container-publish", "container-manifest"), (
                f"{name} has registry write access"
            )
            assert "pull_request" not in job.get("if", "") or "!= 'pull_request'" in job["if"], name
            assert "container-publish" in _needs(job) or name == "container-publish", name
    assert not {k for k, v in workflow["permissions"].items() if v == "write"}


@pytest.mark.unit
def test_pull_request_build_job_is_read_only(workflow: dict) -> None:
    build = workflow["jobs"]["container-build"]
    writes = {k for k, v in build["permissions"].items() if v == "write"}
    # SARIF upload is the only write a pull-request build needs.
    assert writes <= {"security-events"}
    steps = build["steps"]
    assert not any("docker/login-action" in s.get("uses", "") for s in steps)
    build_step = next(s for s in steps if s.get("uses") == "./.github/actions/container-build")
    assert build_step["with"]["push"] == "false"
    assert build_step["with"]["cache-to"] == "false"


@pytest.mark.unit
def test_composite_action_passes_context_via_env_not_run_body() -> None:
    action = yaml.safe_load(
        (ROOT / ".github" / "actions" / "container-build" / "action.yml").read_text()
    )
    for step in action["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], step["name"]
    wf = (WORKFLOWS / "dev-containers.yml").read_text()
    gate = _load("dev-containers.yml")["jobs"]["tests-gate"]
    for step in gate["steps"]:
        assert "${{" not in step.get("run", "")
    assert (
        "--cache-to"
        in (ROOT / ".github" / "actions" / "container-build" / "action.yml").read_text()
    )
    assert "--cache-to" not in wf


@pytest.mark.unit
def test_container_push_paths_are_covered_by_unit_tests_push_paths(workflow: dict) -> None:
    tests = _load("ci-tests.yml")
    assert set(workflow["on"]["push"]["branches"]) <= set(tests["on"]["push"]["branches"])
    assert set(workflow["on"]["push"]["paths"]) <= set(tests["on"]["push"]["paths"])
