"""Unit tests for SlurmCliAdapter with subprocess mocked at the boundary.

Covers open-resource-broker-2706.4: no tests previously exercised the real
CLI adapter implementation (only mocks at a higher layer).
"""

import subprocess

import pytest

from orb.infrastructure.scheduler.slurm.cli_adapter import SlurmCliAdapter

from .conftest import FakeLoggingPort


@pytest.fixture
def adapter(fake_logger: FakeLoggingPort) -> SlurmCliAdapter:
    return SlurmCliAdapter(
        logger=fake_logger, sinfo_path="sinfo", scontrol_path="scontrol", timeout=5
    )


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# get_nodes
# ---------------------------------------------------------------------------


def test_get_nodes_happy_path(adapter, monkeypatch):
    output = "compute-001 IDLE compute 4 16000\ncompute-002 ALLOCATED compute 4 16000\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=output))

    result = adapter.get_nodes()

    assert result == {
        "nodes": [
            {
                "node_name": "compute-001",
                "state": "IDLE",
                "partition": "compute",
                "cpus": "4",
                "memory": "16000",
            },
            {
                "node_name": "compute-002",
                "state": "ALLOCATED",
                "partition": "compute",
                "cpus": "4",
                "memory": "16000",
            },
        ]
    }


def test_get_nodes_skips_malformed_lines(adapter, monkeypatch):
    """Lines with fewer than 5 whitespace-separated fields are dropped."""
    output = "compute-001 IDLE compute 4 16000\nmalformed-line\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=output))

    result = adapter.get_nodes()

    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["node_name"] == "compute-001"


def test_get_nodes_command_failure_returns_empty(adapter, monkeypatch):
    """Non-zero exit code from sinfo is caught and surfaced as an empty result."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="sinfo: error")
    )

    assert adapter.get_nodes() == {}


def test_get_nodes_timeout_returns_empty(adapter, monkeypatch):
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["sinfo"], timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)

    assert adapter.get_nodes() == {}


def test_get_nodes_binary_not_found_returns_empty(adapter, monkeypatch):
    def _raise(*args, **kwargs):
        raise FileNotFoundError("sinfo not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert adapter.get_nodes() == {}


def test_get_nodes_empty_output(adapter, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=""))

    assert adapter.get_nodes() == {"nodes": []}


# ---------------------------------------------------------------------------
# get_node
# ---------------------------------------------------------------------------


def test_get_node_happy_path(adapter, monkeypatch):
    output = "NodeName=compute-001 State=IDLE Partitions=compute CPUTot=4\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=output))

    result = adapter.get_node("compute-001")

    assert result == {
        "NodeName": "compute-001",
        "State": "IDLE",
        "Partitions": "compute",
        "CPUTot": "4",
    }


def test_get_node_invalid_name_raises(adapter):
    with pytest.raises(ValueError, match="Invalid node_name"):
        adapter.get_node("compute-001; rm -rf /")


def test_get_node_command_failure_returns_empty(adapter, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="not found")
    )

    assert adapter.get_node("compute-001") == {}


def test_get_node_timeout_returns_empty(adapter, monkeypatch):
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["scontrol"], timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)

    assert adapter.get_node("compute-001") == {}


# ---------------------------------------------------------------------------
# get_partitions / get_partition
# ---------------------------------------------------------------------------


def test_get_partitions_happy_path(adapter, monkeypatch):
    output = "compute* up infinite 10 10/0/0/10\ngpu up 1-00:00:00 4 4/0/0/4\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=output))

    result = adapter.get_partitions()

    assert result == {
        "partitions": [
            {
                "partition_name": "compute",
                "availability": "up",
                "time_limit": "infinite",
                "nodes": "10",
                "cpus": "10/0/0/10",
            },
            {
                "partition_name": "gpu",
                "availability": "up",
                "time_limit": "1-00:00:00",
                "nodes": "4",
                "cpus": "4/0/0/4",
            },
        ]
    }


def test_get_partitions_command_failure_returns_empty(adapter, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="boom"))

    assert adapter.get_partitions() == {}


def test_get_partition_invalid_name_raises(adapter):
    with pytest.raises(ValueError, match="Invalid partition_name"):
        adapter.get_partition("gpu$(whoami)")


def test_get_partition_happy_path(adapter, monkeypatch):
    output = "PartitionName=gpu State=UP TotalNodes=4\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=output))

    result = adapter.get_partition("gpu")

    assert result == {"PartitionName": "gpu", "State": "UP", "TotalNodes": "4"}


# ---------------------------------------------------------------------------
# ping / is_available
# ---------------------------------------------------------------------------


def test_ping_true_when_controller_up(adapter, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _completed(stdout="Slurmctld(primary) at host is UP\n")
    )

    assert adapter.ping() is True


def test_ping_false_when_controller_down(adapter, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout="DOWN\n"))

    assert adapter.ping() is False


def test_ping_false_on_command_failure(adapter, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="x"))

    assert adapter.ping() is False


def test_is_available_true(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "ping", lambda: True)

    assert adapter.is_available() is True


def test_is_available_false_on_unexpected_error(adapter, monkeypatch):
    def _raise():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(adapter, "ping", _raise)

    assert adapter.is_available() is False


# ---------------------------------------------------------------------------
# _run_command / command construction
# ---------------------------------------------------------------------------


def test_run_command_never_uses_shell(adapter, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _completed(stdout="ok\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    adapter._run_command(["sinfo", "-N"])

    assert captured["kwargs"]["shell"] is False
    assert captured["cmd"] == ["sinfo", "-N"]


def test_run_command_raises_runtime_error_with_stderr_on_nonzero_exit(adapter, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _completed(returncode=2, stderr="permission denied")
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        adapter._run_command(["scontrol", "show", "node", "x"])


# ---------------------------------------------------------------------------
# Logging — the LoggingPort is a required constructor argument
# ---------------------------------------------------------------------------


def test_uses_injected_logger(fake_logger: FakeLoggingPort, monkeypatch):
    adapter = SlurmCliAdapter(logger=fake_logger)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="boom"))

    adapter.get_nodes()

    assert any(call[0] == "error" for call in fake_logger.calls)
