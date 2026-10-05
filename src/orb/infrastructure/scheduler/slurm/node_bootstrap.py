"""SLURM node bootstrap — post-provisioning setup for ephemeral cloud nodes.

Handles post-provisioning setup for ephemeral cloud nodes. Each resume cycle
provisions fresh instances — no state is preserved between cycles.
"""

import re
import shlex
import subprocess
from typing import TYPE_CHECKING, Any

_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_]+$")
_IP_RE = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")
# Absolute path, no shell metacharacters, spaces, or traversal segments —
# this value is interpolated into a root-run cloud-init `sed -i` command.
_SAFE_ABS_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")

if TYPE_CHECKING:
    from orb.domain.base.ports.logging_port import LoggingPort


class SlurmNodeBootstrap:
    """Handles post-provisioning node registration for ephemeral cloud nodes."""

    def __init__(
        self,
        scontrol_path: str = "scontrol",
        timeout: int = 30,
        logger: "LoggingPort | None" = None,
    ) -> None:
        self._scontrol = scontrol_path
        self._timeout = timeout
        self._logger = logger

    @property
    def _log(self) -> Any:
        """Injected LoggingPort, falling back to the module logger when not supplied."""
        if self._logger is None:
            from orb.infrastructure.logging.logger import get_logger

            return get_logger(__name__)
        return self._logger

    @staticmethod
    def _validate_node_name(value: str) -> None:
        if not value or not _NAME_RE.match(value):
            raise ValueError(
                f"Invalid node name '{value}': alphanumeric, hyphens, underscores only"
            )

    @staticmethod
    def _validate_ip(value: str) -> None:
        if not value or not _IP_RE.match(value):
            raise ValueError(f"Invalid IP address '{value}'")

    def register_node_address(
        self, node_name: str, ip_address: str, hostname: str | None = None
    ) -> bool:
        """Register a provisioned node's address with slurmctld via scontrol update.

        Returns True on success, False on failure (non-fatal — slurmd will self-register).
        """
        self._validate_node_name(node_name)
        self._validate_ip(ip_address)
        if hostname:
            self._validate_node_name(hostname)

        cmd = [self._scontrol, "update", f"NodeName={node_name}", f"NodeAddr={ip_address}"]
        if hostname:
            cmd.append(f"NodeHostname={hostname}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self._timeout, shell=False, check=False
            )
            if result.returncode == 0:
                self._log.info("Registered node %s with addr %s", node_name, ip_address)
                return True
            self._log.warning(
                "scontrol update failed for %s (rc=%d): %s",
                node_name,
                result.returncode,
                result.stderr.strip(),
            )
            return False
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            self._log.warning("scontrol update failed for %s: %s", node_name, e)
            return False

    @staticmethod
    def _validate_slurm_conf_path(value: str) -> None:
        """Reject anything that isn't a clean absolute path.

        This value is interpolated into a `sed -i` command inside a root-run
        cloud-init script — shell metacharacters, spaces, or traversal
        segments here would be a command-injection risk if a caller ever
        passes an untrusted path.
        """
        if not value or not _SAFE_ABS_PATH_RE.match(value) or ".." in value.split("/"):
            raise ValueError(
                f"Invalid slurm_conf_path '{value}': must be a clean absolute path "
                "(alphanumeric, '.', '/', '_', '-' only, no '..' segments)"
            )

    @staticmethod
    def generate_user_data(
        node_name: str,
        slurmctld_host: str,
        slurm_conf_path: str = "/etc/slurm/slurm.conf",
    ) -> str:
        """Generate cloud-init user_data script that configures and starts slurmd.

        The provisioned AMI should have SLURM packages pre-installed.
        This script sets the node name, updates slurm.conf, and starts slurmd.
        """
        if not node_name or not _NAME_RE.match(node_name):
            raise ValueError(f"Invalid node name '{node_name}'")
        if not slurmctld_host or not _NAME_RE.match(slurmctld_host.split(".")[0]):
            raise ValueError(f"Invalid slurmctld host '{slurmctld_host}'")
        SlurmNodeBootstrap._validate_slurm_conf_path(slurm_conf_path)

        # Belt-and-suspenders: the allowlist regex above already rejects shell
        # metacharacters, but quote defensively in case the allowlist is ever
        # loosened.
        quoted_conf_path = shlex.quote(slurm_conf_path)

        return f"""#!/bin/bash
# ORB-generated cloud-init script for SLURM elastic node
set -euo pipefail

# Set hostname to match SLURM node name
hostnamectl set-hostname {node_name}

# Ensure slurm.conf has correct SlurmctldHost
sed -i 's/^SlurmctldHost=.*/SlurmctldHost={slurmctld_host}/' {quoted_conf_path}

# Set NodeName in slurmd config
echo "NodeName={node_name}" > /etc/slurm/node_name.conf

# Start slurmd
systemctl enable slurmd
systemctl start slurmd
"""
