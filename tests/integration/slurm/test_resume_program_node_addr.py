"""resumeProgram.sh registers NodeAddr only after the resume request is fulfilled.

The script runs for real under /bin/bash with `orb` and `scontrol` replaced by
stubs on PATH. `scontrol` calls are recorded to a file.
"""

import json
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parents[3]
    / "src"
    / "orb"
    / "infrastructure"
    / "scheduler"
    / "slurm"
    / "scripts"
    / "resumeProgram.sh"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required"),
]

_FAKE_SCONTROL = """#!/bin/bash
if [ "$1" = "show" ] && [ "$2" = "hostnames" ]; then
    echo "$3" | tr ' ' '\\n'
    exit 0
fi
echo "$*" >> "$SCONTROL_CALLS"
"""

_FAKE_ORB = """#!/bin/bash
if [ "$1" = "machines" ] && [ "$2" = "request" ]; then
    echo '{"request_id": "req-1", "status": "pending", "message": "Request submitted"}'
elif [ "$1" = "requests" ] && [ "$2" = "status" ]; then
    cat "$ORB_STATUS_FILE"
    case "$(jq -r '.requests[0].status' "$ORB_STATUS_FILE")" in
        failed|cancelled|timeout|partial) exit 1 ;;
    esac
fi
"""


def _write_fake(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(
    tmp_path: Path,
    status: dict,
    node: str = "compute-001",
    timeout: str = "2",
    orb: str = _FAKE_ORB,
    mode: str = "cli",
):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _write_fake(fakebin / "scontrol", _FAKE_SCONTROL)
    _write_fake(fakebin / "orb", orb)
    _write_fake(fakebin / "curl", "#!/bin/bash\nprintf '{}\\n200\\n'\n")
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps(status))
    calls = tmp_path / "scontrol_calls.txt"
    jq_dir = str(Path(shutil.which("jq") or "").parent)
    env = {
        "PATH": f"{fakebin}:{jq_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "SLURM_ORB_LOG_DIR": str(tmp_path / "logs"),
        "ORB_ROOT_DIR": str(tmp_path),
        "SLURM_ORB_MODE": mode,
        "SLURM_ORB_ADDR_POLL_TIMEOUT": timeout,
        "SLURM_ORB_ADDR_POLL_INTERVAL": "1",
        "ORB_STATUS_FILE": str(status_file),
        "SCONTROL_CALLS": str(calls),
    }
    start = time.monotonic()
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT_PATH), node],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result.elapsed = time.monotonic() - start  # type: ignore[attr-defined]
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result, recorded


def _status(machines: list[dict], state: str = "complete") -> dict:
    return {"requests": [{"request_id": "req-1", "status": state, "machines": machines}]}


def test_ip_available_registers_node_addr(tmp_path):
    status = _status([{"name": "compute-001", "private_ip_address": "10.0.0.5"}])
    result, calls = _run(tmp_path, status)
    assert result.returncode == 0, result.stderr
    assert calls == ["update NodeName=compute-001 NodeAddr=10.0.0.5"]


def test_node_name_key_is_accepted(tmp_path):
    status = _status([{"node_name": "compute-001", "private_ip_address": "10.0.0.6"}])
    result, calls = _run(tmp_path, status)
    assert result.returncode == 0, result.stderr
    assert calls == ["update NodeName=compute-001 NodeAddr=10.0.0.6"]


def test_no_ip_makes_no_scontrol_call(tmp_path):
    status = _status([{"name": "compute-001", "private_ip_address": None}], state="failed")
    result, calls = _run(tmp_path, status, timeout="30")
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert result.elapsed < 10  # type: ignore[attr-defined]


def test_poll_timeout_exits_zero(tmp_path):
    status = _status([{"name": "compute-001", "private_ip_address": ""}], state="running")
    result, calls = _run(tmp_path, status, timeout="1")
    assert result.returncode == 0, result.stderr
    assert calls == []
    log = (tmp_path / "logs" / "resume_program.log").read_text()
    assert "Timed out" in log


@pytest.mark.parametrize("state", ["failed", "cancelled", "timeout", "partial"])
def test_nonzero_status_exit_still_registers_and_stops(tmp_path, state):
    status = _status([{"name": "compute-001", "private_ip_address": "10.0.0.7"}], state=state)
    result, calls = _run(tmp_path, status, timeout="30")
    assert result.returncode == 0, result.stderr
    assert calls == ["update NodeName=compute-001 NodeAddr=10.0.0.7"]
    assert result.elapsed < 10  # type: ignore[attr-defined]


def test_complete_with_fewer_ips_than_nodes_stops_polling(tmp_path):
    status = _status([{"name": "compute-001", "private_ip_address": "10.0.0.8"}])
    result, calls = _run(tmp_path, status, node="compute-001 compute-002", timeout="30")
    assert result.returncode == 0, result.stderr
    assert calls == ["update NodeName=compute-001 NodeAddr=10.0.0.8"]
    assert result.elapsed < 10  # type: ignore[attr-defined]


def test_last_good_body_survives_failed_poll(tmp_path):
    status = _status(
        [{"name": "compute-001", "private_ip_address": "10.0.0.9"}], state="in_progress"
    )
    # First poll returns the body (not terminal, 2 nodes wanted), later polls return nothing.
    _write_orb = _FAKE_ORB.replace(
        'cat "$ORB_STATUS_FILE"',
        'if [ -e "$ORB_STATUS_FILE.seen" ]; then exit 1; fi; touch "$ORB_STATUS_FILE.seen"; cat "$ORB_STATUS_FILE"',
    )
    result, calls = _run(
        tmp_path, status, node="compute-001 compute-002", timeout="2", orb=_write_orb
    )
    assert result.returncode == 0, result.stderr
    assert calls == ["update NodeName=compute-001 NodeAddr=10.0.0.9"]


def test_api_mode_skips_polling(tmp_path):
    status = _status([{"name": "compute-001", "private_ip_address": "10.0.0.5"}])
    result, calls = _run(tmp_path, status, mode="api")
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert "API mode" in (tmp_path / "logs" / "resume_program.log").read_text()
