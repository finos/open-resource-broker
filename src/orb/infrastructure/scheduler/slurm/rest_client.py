"""slurmrestd REST API client for node and partition queries."""

import ipaddress
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_]+$")

if TYPE_CHECKING:
    from orb.domain.base.ports.logging_port import LoggingPort


def _is_loopback_host(hostname: str | None) -> bool:
    """True if hostname is "localhost" or a loopback IP literal (IPv4 or IPv6).

    ``urlparse(...).hostname`` already lowercases the host and strips the
    brackets from an IPv6 literal (e.g. ``[::1]`` -> ``::1``), so this only
    needs an exact "localhost" check plus ``ipaddress`` for IP literals.
    A hostname like ``localhost.example.com`` is intentionally NOT treated
    as loopback: it is an arbitrary name that could resolve anywhere.
    """
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class SlurmRestClientError(Exception):
    """Raised on slurmrestd API errors."""


class SlurmRestClient:
    """Client for communicating with slurmrestd (SLURM REST API daemon).

    Supports node and partition read endpoints only — ORB acts as a resource
    provider, not a job scheduler.

    ``base_url`` must point to a trusted slurmrestd endpoint — typically the
    same cluster's slurmctld host, reachable only from the ORB control plane's
    private network. ORB sends the JWT auth token to whatever host this URL
    resolves to, so pointing it at an untrusted or attacker-controlled host
    would leak that token.

    slurmrestd itself has no built-in TLS — per SchedMD's REST API docs, "Only
    unencrypted and uncompressed HTTP communications are supported" and sites
    that need encryption are told to "use a proxy to wrap all communications
    with TLS" — so a stock slurmrestd install normally only listens on
    loopback or a UNIX socket. Plain ``http://`` is therefore allowed without
    any opt-in when the host is loopback (``localhost``, ``127.0.0.0/8``,
    ``::1``). Plain ``http://`` to any other host is rejected by default,
    since it would send the JWT token and all node/partition data
    unencrypted; pass ``allow_insecure_http=True`` (or set
    ``SLURM_ORB_RESTD_ALLOW_HTTP=1`` at the strategy level) to opt in for a
    network you already trust, or put a TLS-terminating proxy in front of
    slurmrestd and use ``https://`` instead.
    """

    def __init__(
        self,
        base_url: str,
        api_version: str = "v0.0.44",
        token: str | None = None,
        timeout: int = 30,
        verify_ssl: bool = True,
        logger: "LoggingPort | None" = None,
        allow_insecure_http: bool = False,
    ) -> None:
        self._logger = logger
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"base_url must start with http:// or https://, got: {base_url}")
        if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
            if not allow_insecure_http:
                raise ValueError(
                    f"Refusing plain http:// slurmrestd URL '{base_url}' to a "
                    "non-loopback host: slurmrestd has no built-in TLS, so this "
                    "would send the JWT auth token and all node/partition data "
                    "unencrypted. Put a TLS-terminating proxy in front of "
                    "slurmrestd and use https://, or set "
                    "SLURM_ORB_RESTD_ALLOW_HTTP=1 to opt in for a network you "
                    "already trust."
                )
            self._log.warning(
                "slurmrestd base_url '%s' uses plain http:// to a non-loopback "
                "host with SLURM_ORB_RESTD_ALLOW_HTTP=1 set; the JWT token and "
                "all node/partition data will be sent unencrypted.",
                base_url,
            )
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._token = token
        self._timeout = timeout
        self._verify_ssl = verify_ssl

    @property
    def _log(self) -> Any:
        """Injected LoggingPort, falling back to the module logger when not supplied."""
        if self._logger is None:
            from orb.infrastructure.logging.logger import get_logger

            return get_logger(__name__)
        return self._logger

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
                self._log.error(
                    "slurmrestd %s returned HTTP %d: %s", url, resp.status_code, resp.text
                )
                raise SlurmRestClientError(f"slurmrestd HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()  # type: ignore[no-any-return]
        except requests.ConnectionError as e:
            self._log.error("slurmrestd connection failed for %s: %s", url, e)
            return {}
        except requests.Timeout as e:
            self._log.error("slurmrestd timeout for %s: %s", url, e)
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
