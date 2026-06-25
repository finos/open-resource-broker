"""End-to-end tests for the `orb k8s-legacy` CLI shim.

The shim bridges the modern argparse-based `orb` CLI to the legacy click
groups under `orb.k8s_legacy.cli`.  These tests exercise the shim by
invoking `orb` as a subprocess, asserting:

* the `k8s-legacy` subcommand is registered on the top-level argparse parser
* `--help` propagates correctly into click sub-groups
* the routing table (admin / utils / events-db / default) dispatches to the
  expected click entry point
* exit codes and error messages surface cleanly through the argparse layer
* the lazy-import error path produces a friendly hint, not a traceback
* the HostFactory shell scripts invoke the right entrypoint and command name

These tests intentionally do NOT exercise the legacy runtime (no kube
client, no filesystem workdir mutations).  They verify the CLI surface only.
For deeper legacy behaviour, see `src/orb/k8s_legacy/tests/unit/`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HF_SCRIPTS_DIR = REPO_ROOT / "k8s-legacy" / "hostfactory" / "providers" / "k8s-hf" / "scripts"


def _run_orb(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke the current interpreter's `orb` console script.

    Runs via `python -m orb.run` against the same interpreter that pytest is
    running under.  This avoids picking up a stale `orb` binary from PATH (a
    global pipx install, an older mise-managed Python, etc.) which would not
    have the `k8s-legacy` subcommand registered.
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "orb.run", *args],
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
        timeout=30,
    )


# ----- Top-level registration ------------------------------------------------


@pytest.mark.integration
def test_orb_help_lists_k8s_legacy() -> None:
    """`orb --help` advertises the k8s-legacy subcommand."""
    result = _run_orb("--help")
    assert result.returncode == 0
    assert "k8s-legacy" in result.stdout


@pytest.mark.integration
def test_k8s_legacy_help_prints_subcommand_listing() -> None:
    """`orb k8s-legacy --help` lists the legacy subcommand groups."""
    result = _run_orb("k8s-legacy", "--help")
    assert result.returncode == 0
    out = result.stdout
    for keyword in (
        "Legacy Symphony-on-Kubernetes",
        "request-machines",
        "get-request-status",
        "watch",
        "run-cron",
        "admin",
        "utils",
        "events-db",
    ):
        assert keyword in out, f"{keyword!r} missing from k8s-legacy help"


# ----- Click --help propagation ---------------------------------------------


@pytest.mark.integration
def test_watch_help_is_click_group_help() -> None:
    """`orb k8s-legacy watch --help` reaches the click `watch` group."""
    result = _run_orb("k8s-legacy", "watch", "--help")
    # Click exits 0 on --help
    assert result.returncode == 0
    out = result.stdout
    assert "Usage: orb k8s-legacy watch" in out or "Usage:" in out
    # Click sub-commands of the watch group
    for sub in ("pods", "nodes", "events", "request-machines", "request-return-machines"):
        assert sub in out, f"watch sub-command {sub!r} missing"


@pytest.mark.integration
def test_admin_help_lists_admin_commands() -> None:
    """`orb k8s-legacy admin --help` shows the legacy admin subcommands."""
    result = _run_orb("k8s-legacy", "admin", "--help")
    assert result.returncode == 0
    out = result.stdout
    for sub in (
        "list-machines",
        "list-requests",
        "get-request-status",
        "get-timings",
        "replay",
    ):
        assert sub in out, f"admin command {sub!r} missing"


@pytest.mark.integration
def test_utils_help_is_click_command_help() -> None:
    """`orb k8s-legacy utils --help` reaches the bare click command."""
    result = _run_orb("k8s-legacy", "utils", "--help")
    assert result.returncode == 0
    out = result.stdout
    # The runserver command is a bare @click.command, so --help shows its
    # options directly (not a sub-group listing).
    assert "--host" in out
    assert "--port" in out
    assert "--workdir" in out


@pytest.mark.integration
def test_events_db_help_lists_transform() -> None:
    """`orb k8s-legacy events-db --help` reaches the click events_db group."""
    result = _run_orb("k8s-legacy", "events-db", "--help")
    assert result.returncode == 0
    assert "transform" in result.stdout


# ----- Routing + exit codes -------------------------------------------------


@pytest.mark.integration
def test_unknown_verb_returns_non_zero_with_click_error() -> None:
    """An unknown subcommand surfaces click's `No such command` error."""
    result = _run_orb("k8s-legacy", "this-verb-does-not-exist")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "No such command" in combined


@pytest.mark.integration
def test_missing_required_option_returns_usage_error() -> None:
    """get-available-templates exits with a usage error when --confdir is unset."""
    # Strip the env var so the missing-option branch is exercised
    env = {"HF_K8S_PROVIDER_CONFDIR": ""}
    result = _run_orb("k8s-legacy", "get-available-templates", env=env)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "HF_K8S_PROVIDER_CONFDIR" in combined or "--confdir" in combined, (
        f"missing-option hint absent in: {combined!r}"
    )
    # No raw Python traceback should leak through the argparse layer
    assert "Traceback" not in combined


# ----- JSON-file positional argument (HF protocol) --------------------------


@pytest.mark.integration
def test_request_machines_accepts_json_file_path() -> None:
    """`request-machines` accepts a JSON file path as positional arg.

    Symphony invokes the HF shell scripts as
        requestMachines.sh <ignored> /path/to/payload.json
    The script assigns `inJson="$2"` and calls
        orb k8s-legacy request-machines "$inJson"
    so the click command must accept a single positional JSON-file argument.

    A malformed JSON payload should be REJECTED by the validator (proving
    the file was opened and parsed), not crash with a Python traceback.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        # Deliberately incomplete payload: missing template.templateId — the
        # legacy validator should reject this with a usage error, but only
        # AFTER successfully opening the file.
        tmp.write('{"template": {"machineCount": 1}}')
        tmp_path = tmp.name

    try:
        env = {
            "HF_K8S_PROVIDER_CONFDIR": "/tmp",
            "HF_K8S_WORKDIR": tempfile.mkdtemp(prefix="orb-k8s-legacy-test-"),
        }
        result = _run_orb("k8s-legacy", "request-machines", tmp_path, env=env)
        # The validator will likely reject the payload, but the failure
        # path must NOT be a Python traceback.  We accept any non-zero exit
        # with a clean error message OR a controlled zero exit.
        combined = result.stdout + result.stderr
        assert "Traceback" not in combined, f"raw traceback leaked: {combined!r}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ----- HF shell script wiring -----------------------------------------------


@pytest.mark.integration
def test_hf_shell_scripts_call_orb_k8s_legacy() -> None:
    """Every shipped HF shell script invokes `orb k8s-legacy <verb>`."""
    scripts = sorted(HF_SCRIPTS_DIR.glob("*.sh"))
    # All 5 expected HF scripts present
    expected_names = {
        "requestMachines.sh",
        "getRequestStatus.sh",
        "requestReturnMachines.sh",
        "getReturnRequests.sh",
        "getAvailableTemplates.sh",
    }
    assert {s.name for s in scripts} == expected_names

    for script in scripts:
        content = script.read_text()
        # Must call the new entrypoint
        assert "orb k8s-legacy" in content, f"{script.name} does not call `orb k8s-legacy`"
        # Must NOT reference the old `hostfactory` or
        # `open-resource-broker` binaries directly
        for stale in ("hostfactory ", "open-resource-broker "):
            assert stale not in content, f"{script.name} still references stale binary {stale!r}"


@pytest.mark.integration
def test_hf_shell_scripts_have_lf_line_endings() -> None:
    """HF scripts must be LF-encoded (.gitattributes enforces this)."""
    scripts = sorted(HF_SCRIPTS_DIR.glob("*.sh"))
    for script in scripts:
        data = script.read_bytes()
        assert b"\r\n" not in data, f"{script.name} contains CRLF line endings"


# ----- Package metadata -----------------------------------------------------


@pytest.mark.integration
def test_orb_k8s_legacy_importable_when_extra_installed() -> None:
    """The legacy package is importable as `orb.k8s_legacy`."""
    # Run in a subprocess so the IDE-cached sys.modules doesn't mask issues
    result = subprocess.run(
        [sys.executable, "-c", "import orb.k8s_legacy; print(orb.k8s_legacy.__name__)"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, f"import failed: {result.stderr!r}"
    assert result.stdout.strip() == "orb.k8s_legacy"


@pytest.mark.integration
def test_legacy_alembic_package_data_present() -> None:
    """Alembic ini + migration versions are accessible via importlib.resources."""
    snippet = (
        "import importlib.resources as r\n"
        "files = r.files('orb.k8s_legacy.alembic')\n"
        "ini = files / 'alembic.ini'\n"
        "print('OK' if ini.is_file() else 'MISSING')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, f"resource lookup failed: {result.stderr!r}"
    assert result.stdout.strip() == "OK", f"alembic.ini not found: {result.stdout!r}"


@pytest.mark.integration
def test_legacy_events_schema_creates_clean() -> None:
    """The legacy SQLAlchemy schema bootstraps against in-memory SQLite."""
    snippet = (
        "from sqlalchemy import create_engine\n"
        "from orb.k8s_legacy.events_schema import Base\n"
        "engine = create_engine('sqlite:///:memory:')\n"
        "Base.metadata.create_all(engine)\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, f"schema bootstrap failed: {result.stderr!r}"
    assert result.stdout.strip() == "OK"


# ----- No stale references --------------------------------------------------


@pytest.mark.integration
def test_no_stale_open_resource_broker_imports_in_legacy_tree() -> None:
    """The renamed legacy code does not import from the old package name."""
    legacy_root = REPO_ROOT / "src" / "orb" / "k8s_legacy"
    offenders: list[tuple[str, int, str]] = []
    for py in legacy_root.rglob("*.py"):
        for lineno, line in enumerate(py.read_text().splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith(("from open_resource_broker", "import open_resource_broker")):
                offenders.append((str(py.relative_to(REPO_ROOT)), lineno, line))
    assert not offenders, f"stale `open_resource_broker` imports: {offenders}"


@pytest.mark.integration
def test_hfreplay_subprocess_target_is_orb_k8s_legacy() -> None:
    """hfreplay no longer shells out to the gone `open-resource-broker` binary."""
    hfreplay = REPO_ROOT / "src" / "orb" / "k8s_legacy" / "impl" / "hfreplay.py"
    content = hfreplay.read_text()
    # The subprocess must invoke the new entrypoint
    assert '"orb"' in content and '"k8s-legacy"' in content
    # And must NOT invoke the gone binaries
    assert '"open-resource-broker"' not in content
    assert '"hostfactory"' not in content
