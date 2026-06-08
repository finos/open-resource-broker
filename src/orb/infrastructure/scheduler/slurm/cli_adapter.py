"""SLURM CLI adapter — fallback for environments without slurmrestd."""

import logging
import re
import subprocess

_logger = logging.getLogger(__name__)
_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_]+$")


class SlurmCliAdapter:
    """Queries SLURM via sinfo/scontrol CLI commands.

    Provides the same interface as SlurmRestClient for node/partition/health
    methods so the strategy can use either interchangeably.
    """

    def __init__(
        self,
        sinfo_path: str = "sinfo",
        scontrol_path: str = "scontrol",
        timeout: int = 30,
    ) -> None:
        self._sinfo = sinfo_path
        self._scontrol = scontrol_path
        self._timeout = timeout

    @staticmethod
    def _validate_name(value: str, label: str) -> None:
        if not value or not _NAME_RE.match(value):
            raise ValueError(f"Invalid {label}: must be alphanumeric, hyphens, underscores only")

    def _run_command(self, cmd: list[str]) -> str:
        """Execute a command securely (no shell=True) with timeout."""
        _logger.debug("Running command: %s", cmd)
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=self._timeout, shell=False
        )
        if result.returncode != 0:
            _logger.error("Command %s failed (rc=%d): %s", cmd, result.returncode, result.stderr)
            raise RuntimeError(f"Command failed (rc={result.returncode}): {result.stderr.strip()}")
        return result.stdout

    @staticmethod
    def _parse_scontrol_output(output: str) -> dict[str, str]:
        """Parse SLURM scontrol key=value output into a dict."""
        result: dict[str, str] = {}
        for token in output.replace("\n", " ").split():
            if "=" in token:
                key, _, value = token.partition("=")
                result[key] = value
        return result

    # --- Node endpoints ---

    def get_nodes(self) -> dict:
        """List all nodes via sinfo."""
        try:
            output = self._run_command(
                [self._sinfo, "-N", "-h", "-o", "%N %T %P %c %m"]
            )
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            _logger.error("get_nodes failed: %s", e)
            return {}

        nodes = []
        for line in output.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5:
                nodes.append({
                    "node_name": parts[0],
                    "state": parts[1],
                    "partition": parts[2],
                    "cpus": parts[3],
                    "memory": parts[4],
                })
        return {"nodes": nodes}

    def get_node(self, node_name: str) -> dict:
        """Get single node details via scontrol."""
        self._validate_name(node_name, "node_name")
        try:
            output = self._run_command([self._scontrol, "show", "node", node_name])
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            _logger.error("get_node(%s) failed: %s", node_name, e)
            return {}
        return self._parse_scontrol_output(output)

    # --- Partition endpoints ---

    def get_partitions(self) -> dict:
        """List all partitions via sinfo."""
        try:
            output = self._run_command(
                [self._sinfo, "-h", "-o", "%P %a %l %D %C"]
            )
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            _logger.error("get_partitions failed: %s", e)
            return {}

        partitions = []
        for line in output.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5:
                partitions.append({
                    "partition_name": parts[0].rstrip("*"),
                    "availability": parts[1],
                    "time_limit": parts[2],
                    "nodes": parts[3],
                    "cpus": parts[4],
                })
        return {"partitions": partitions}

    def get_partition(self, partition_name: str) -> dict:
        """Get single partition details via scontrol."""
        self._validate_name(partition_name, "partition_name")
        try:
            output = self._run_command([self._scontrol, "show", "partition", partition_name])
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            _logger.error("get_partition(%s) failed: %s", partition_name, e)
            return {}
        return self._parse_scontrol_output(output)

    # --- Health check ---

    def ping(self) -> bool:
        """Check if SLURM controller is responsive via scontrol ping."""
        try:
            output = self._run_command([self._scontrol, "ping"])
            return "is UP" in output
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def is_available(self) -> bool:
        """Check if SLURM CLI tools are reachable. Returns False on any error."""
        try:
            return self.ping()
        except Exception:
            return False
