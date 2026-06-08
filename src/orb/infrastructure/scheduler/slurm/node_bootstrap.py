"""SLURM node bootstrap — post-provisioning setup and scontrol registration."""

import logging
import re
import subprocess
import time

_logger = logging.getLogger(__name__)
_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_]+$")
_IP_RE = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")


class SlurmNodeBootstrap:
    """Handles post-provisioning node registration and readiness verification."""

    def __init__(self, scontrol_path: str = "scontrol", timeout: int = 30) -> None:
        self._scontrol = scontrol_path
        self._timeout = timeout

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
                _logger.info("Registered node %s with addr %s", node_name, ip_address)
                return True
            _logger.warning(
                "scontrol update failed for %s (rc=%d): %s",
                node_name,
                result.returncode,
                result.stderr.strip(),
            )
            return False
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            _logger.warning("scontrol update failed for %s: %s", node_name, e)
            return False

    def verify_node_ready(self, node_name: str, timeout: int = 300) -> bool:
        """Poll node state until it leaves POWERING_UP (or timeout).

        Returns True if node is ready, False on timeout.
        """
        self._validate_node_name(node_name)
        deadline = time.time() + timeout
        interval = 5

        while time.time() < deadline:
            try:
                result = subprocess.run(
                    [self._scontrol, "show", "node", node_name],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    shell=False,
                    check=False,
                )
                if result.returncode == 0 and "POWERING_UP" not in result.stdout:
                    _logger.info("Node %s is ready (no longer POWERING_UP)", node_name)
                    return True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            time.sleep(interval)

        _logger.warning("Node %s still POWERING_UP after %ds timeout", node_name, timeout)
        return False

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

        return f"""#!/bin/bash
# ORB-generated cloud-init script for SLURM elastic node
set -euo pipefail

# Set hostname to match SLURM node name
hostnamectl set-hostname {node_name}

# Ensure slurm.conf has correct SlurmctldHost
sed -i 's/^SlurmctldHost=.*/SlurmctldHost={slurmctld_host}/' {slurm_conf_path}

# Set NodeName in slurmd config
echo "NodeName={node_name}" > /etc/slurm/node_name.conf

# Start slurmd
systemctl enable slurmd
systemctl start slurmd
"""
