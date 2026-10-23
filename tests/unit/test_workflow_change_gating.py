"""Guard the change-detection gating in workflows that skip work on unrelated PRs.

A job listing ``changes`` in ``needs`` is skipped whenever ``changes`` is
skipped, unless its ``if:`` contains a status-check function such as
``!cancelled()``. These tests keep ``changes`` running on every event and keep
dependent jobs from being skipped on push, schedule and manual runs.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
GATED_WORKFLOWS = ["security-code.yml", "sdk.yml"]


def _jobs(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text())["jobs"]


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


@pytest.mark.unit
@pytest.mark.parametrize("workflow", GATED_WORKFLOWS)
def test_changes_job_runs_on_every_event(workflow: str) -> None:
    changes = _jobs(workflow)["changes"]
    assert "if" not in changes, "changes must not be restricted to specific events"
    # Outside pull_request the job has no diff to inspect and must report relevant.
    relevant = changes["outputs"]["relevant"]
    assert "github.event_name != 'pull_request'" in relevant
    assert "'true'" in relevant


@pytest.mark.unit
@pytest.mark.parametrize("workflow", GATED_WORKFLOWS)
def test_jobs_needing_changes_survive_non_pr_events(workflow: str) -> None:
    jobs = _jobs(workflow)
    dependents = {
        name: job
        for name, job in jobs.items()
        if "changes" in _needs(job) and name != "sdk-ci-passed"
    }
    assert dependents, f"{workflow} has no jobs depending on changes"
    for name, job in dependents.items():
        condition = str(job.get("if", ""))
        assert "!cancelled()" in condition, f"{workflow}:{name} if: lacks !cancelled()"
        enabled = str(job.get("with", {}).get("enabled", ""))
        if enabled:
            # Required-check callers always expand and gate their steps via
            # `enabled`, which must fail closed when change detection failed.
            assert "needs.changes.result != 'success'" in enabled, (
                f"{workflow}:{name} enabled must scan when change detection failed"
            )
        else:
            assert "needs.changes.result == 'success'" in condition, (
                f"{workflow}:{name} must require change detection to have succeeded"
            )


@pytest.mark.unit
def test_sdk_gate_tolerates_skips_only_after_successful_not_relevant_detection() -> None:
    gate = _jobs("sdk.yml")["sdk-ci-passed"]
    script = next(s["run"] for s in gate["steps"] if "run" in s)
    env = next(s["env"] for s in gate["steps"] if "env" in s)
    assert "changes" not in env["ALLOWED_SKIPS"].split()
    assert '"$changes_result" = "success"' in script
    assert '"$relevant" = "false"' in script
    assert '"$skips_ok" -eq 1' in script


@pytest.mark.unit
def test_codeql_runs_on_push_to_main_schedule_and_relevant_prs() -> None:
    job = _jobs("security-code.yml")["codeql-analysis"]
    # The caller always expands so the required per-language checks report;
    # `enabled` decides whether the analysis steps run.
    assert "relevant" not in str(job["if"])
    condition = str(job["with"]["enabled"])
    assert "github.event_name == 'schedule'" in condition
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in condition
    assert "github.event_name == 'pull_request'" in condition
    assert "needs.changes.outputs.relevant == 'true'" in condition


@pytest.mark.unit
@pytest.mark.parametrize("scan_type", ["security-scan", "codeql"])
def test_reusable_security_steps_all_honour_enabled(scan_type: str) -> None:
    job = _jobs("reusable-security.yml")[scan_type]
    for step in job["steps"]:
        assert "inputs.enabled" in str(step.get("if", "")), (
            f"reusable-security.yml:{scan_type} step {step['name']!r} ignores inputs.enabled"
        )


@pytest.mark.unit
def test_codeql_relevance_filter_covers_codeql_inputs() -> None:
    steps = _jobs("security-code.yml")["changes"]["steps"]
    filters = next(s for s in steps if s.get("id") == "filter")["with"]["filters"]
    assert "'.github/workflows/**'" in filters
    assert "'.github/codeql/**'" in filters
    for path in (
        "'.github/actions/**'",
        "'Makefile'",
        "'makefiles/**'",
        "'tests/**'",
        "'dev-tools/**'",
    ):
        assert path in filters


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text())


@pytest.mark.unit
@pytest.mark.parametrize("job_name", ["security-pip-audit", "codeql-analysis"])
def test_required_check_callers_always_expand(job_name: str) -> None:
    # A skipped caller never expands its children, so the required child
    # check names would stay unreported. Only `enabled` may depend on changes.
    condition = str(_jobs("security-code.yml")[job_name]["if"])
    assert "relevant" not in condition
    assert "needs.changes" not in condition


@pytest.mark.unit
def test_reusable_security_enabled_defaults_to_true() -> None:
    # `on` parses as the boolean True key under YAML 1.1.
    workflow = _workflow("reusable-security.yml")
    inputs = (workflow.get("on") or workflow[True])["workflow_call"]["inputs"]
    assert inputs["enabled"]["type"] == "boolean"
    assert inputs["enabled"]["default"] is True


@pytest.mark.unit
def test_pip_audit_enabled_is_relevance_gated_and_fails_closed() -> None:
    enabled = str(_jobs("security-code.yml")["security-pip-audit"]["with"]["enabled"])
    assert "relevant == 'true'" in enabled
    assert "result != 'success'" in enabled


@pytest.mark.unit
def test_push_paths_match_change_filter_paths() -> None:
    workflow = _workflow("security-code.yml")
    push_paths = (workflow.get("on") or workflow[True])["push"]["paths"]
    steps = workflow["jobs"]["changes"]["steps"]
    filters = next(s for s in steps if s.get("id") == "filter")["with"]["filters"]
    filter_paths = yaml.safe_load(filters)["relevant"]
    assert push_paths == filter_paths
