"""slurmrestd REST API client for node and partition queries."""

import logging
import re

import requests

_logger = logging.getLogger(__name__)
_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_]+$")


class SlurmRestClientError(Exception):
    """Raised on slurmrestd API errors."""


class SlurmRestClient:
    """Client for communicating with slurmrestd (SLURM REST API daemon).

    Supports node and partition read endpoints only — ORB acts as a resource
    provider, not a job scheduler.
    """

    def __init__(
        self,
        base_url: str,
        api_version: str = "v0.0.44",
        token: str | None = None,
        timeout: int = 30,
        verify_ssl: bool = True,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must start with http:// or https://, got: {base_url}")
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._token = token
        self._timeout = timeout
        self._verify_ssl = verify_ssl

    def set_token(self, token: str) -> None:
        """Set or update the JWT authentication token."""
        self._token = token

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["X-SLURM-USER-TOKEN"] = self._token
        return headers

    @staticmethod
    def _validate_name(value: str, label: str) -> None:
        if not value or not _NAME_RE.match(value):
            raise ValueError(f"Invalid {label}: must be alphanumeric, hyphens, underscores only")

    def _url(self, path: str) -> str:
        return f"{self._base_url}/slurm/{self._api_version}/{path}"

    def _get(self, path: str) -> dict:
        url = self._url(path)
        try:
            resp = requests.get(
                url, headers=self._get_headers(), timeout=self._timeout, verify=self._verify_ssl
            )
            if resp.status_code >= 400:
                _logger.error(
                    "slurmrestd %s returned HTTP %d: %s", url, resp.status_code, resp.text
                )
                raise SlurmRestClientError(f"slurmrestd HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()  # type: ignore[no-any-return]
        except requests.ConnectionError as e:
            _logger.error("slurmrestd connection failed for %s: %s", url, e)
            return {}
        except requests.Timeout as e:
            _logger.error("slurmrestd timeout for %s: %s", url, e)
            return {}

    # --- Node endpoints ---

    def get_nodes(self) -> dict:
        """GET /slurm/{version}/nodes — list all nodes."""
        return self._get("nodes")

    def get_node(self, node_name: str) -> dict:
        """GET /slurm/{version}/node/{node_name} — single node details."""
        self._validate_name(node_name, "node_name")
        return self._get(f"node/{node_name}")

    # --- Partition endpoints ---

    def get_partitions(self) -> dict:
        """GET /slurm/{version}/partitions — list all partitions."""
        return self._get("partitions")

    def get_partition(self, partition_name: str) -> dict:
        """GET /slurm/{version}/partition/{partition_name} — single partition."""
        self._validate_name(partition_name, "partition_name")
        return self._get(f"partition/{partition_name}")

    # --- Health check ---

    def ping(self) -> bool:
        """GET /slurm/{version}/diag — returns True if slurmrestd responds."""
        try:
            resp = requests.get(
                self._url("diag"),
                headers=self._get_headers(),
                timeout=min(self._timeout, 5),
                verify=self._verify_ssl,
            )
            return resp.status_code < 400
        except (requests.ConnectionError, requests.Timeout):
            return False

    def is_available(self) -> bool:
        """Check if slurmrestd is reachable. Returns False on any error."""
        try:
            return self.ping()
        except Exception:
            return False
