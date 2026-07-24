"""In-cluster Kubernetes config loader.

Thin wrapper around ``kubernetes.config.load_incluster_config`` used when ORB
runs inside the target cluster (or a management cluster with RBAC to the
target).  Detection uses the ``/var/run/secrets/kubernetes.io`` sentinel,
matching the upstream kubernetes client behaviour.

The wrapper exists so the ``kubernetes`` SDK import stays confined to this
package (enforced by the architecture test) and so callers can mock the
sentinel and ``load_incluster_config`` independently in unit tests.

Token refresh
-------------
In-cluster service-account tokens are projected credentials with a finite
lifetime (default 3600 seconds per the projected volume spec).  After the
token rotates on the filesystem the running process must reload it from
disk before the old token is rejected.

:class:`InClusterAuthAdapter` tracks when the config was last loaded and
exposes :meth:`refresh_if_stale` so callers (e.g. :class:`K8sClient`) can
proactively refresh before the TTL expires.  ``K8sClient`` also calls
``refresh_if_stale`` on every ``ApiException(status=401)`` response.

HTTP proxy
----------
After loading, the module reads ``HTTPS_PROXY`` / ``https_proxy`` (preferred
for apiserver TLS traffic) falling back to ``HTTP_PROXY`` / ``http_proxy``,
and wires the resolved URL into ``kubernetes.client.Configuration.proxy``.
``NO_PROXY`` / ``no_proxy`` is similarly honoured via
``Configuration.no_proxy``.  A DEBUG log is emitted when a proxy is applied.
"""

from __future__ import annotations

import os
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthError

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort

_IN_CLUSTER_SENTINEL = Path("/var/run/secrets/kubernetes.io")

# Default refresh interval: 55 minutes.  Kubernetes rotates projected tokens
# at 80 % of their lifetime (which is 3600 s by default), so we refresh at
# 55 minutes to give a generous margin before the old token is rejected.
_DEFAULT_TOKEN_REFRESH_SECONDS: int = 55 * 60


def _redact_proxy_url(url: str) -> str:
    """Return *url* with the userinfo (user:password) component replaced by ``***``.

    ``HTTPS_PROXY`` values often take the form ``http://user:pass@proxy:port``.
    Logging the raw URL at DEBUG level would expose credentials in log files.
    This helper strips the userinfo from the netloc so the host/port are still
    visible for diagnostics without leaking secrets.

    ``urllib.parse`` only populates ``username``/``password`` when it finds a
    scheme and network-location component.  A scheme-less but credentialed
    value such as ``user:pass@proxy:3128`` parses entirely into ``path`` with
    ``username`` unset, so we additionally detect a userinfo ``@`` segment in
    the authority portion by hand and redact it.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.username or parsed.password:
            # Replace netloc so host:port stays visible, credentials do not.
            redacted_netloc = parsed.hostname or ""
            if parsed.port:
                redacted_netloc = f"{redacted_netloc}:{parsed.port}"
            redacted_netloc = f"***@{redacted_netloc}"
            parsed = parsed._replace(netloc=redacted_netloc)
            return urllib.parse.urlunparse(parsed)
        return _redact_schemeless_userinfo(url)
    except Exception:  # pragma: no cover — malformed URLs passed through
        pass
    return url


def _redact_schemeless_userinfo(url: str) -> str:
    """Redact a ``user:pass@host`` userinfo segment that ``urlparse`` missed.

    Handles scheme-less values (``user:pass@proxy:3128``) as well as values
    whose scheme was recognised but whose userinfo urllib still declined to
    split.  Only the authority portion (up to the first ``/``) is inspected so
    an ``@`` inside a path is never mistaken for credentials.
    """
    scheme_sep = "://"
    prefix = ""
    remainder = url
    sep_idx = url.find(scheme_sep)
    if sep_idx != -1:
        prefix = url[: sep_idx + len(scheme_sep)]
        remainder = url[sep_idx + len(scheme_sep) :]

    authority, sep, rest = remainder.partition("/")
    at_idx = authority.rfind("@")
    if at_idx == -1:
        return url
    host_part = authority[at_idx + 1 :]
    return f"{prefix}***@{host_part}{sep}{rest}"


# ---------------------------------------------------------------------------
# HTTP proxy helpers
# ---------------------------------------------------------------------------


def _resolve_proxy_url(config_proxy_url: Optional[str] = None) -> Optional[str]:
    """Return the proxy URL to use for apiserver connections, or ``None``.

    When *config_proxy_url* is supplied (from :class:`K8sProviderConfig`) it
    takes precedence over the environment — explicit provider configuration
    always wins over ambient env vars.

    Otherwise the preference order is ``HTTPS_PROXY`` → ``https_proxy`` →
    ``HTTP_PROXY`` → ``http_proxy``.  HTTPS variants are checked first because
    the Kubernetes apiserver always serves TLS.
    """
    if config_proxy_url is not None:
        stripped = config_proxy_url.strip()
        if stripped:
            return stripped
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def _resolve_no_proxy(config_no_proxy: Optional[str] = None) -> Optional[str]:
    """Return the ``NO_PROXY`` exclusion list, or ``None`` when unset.

    A *config_no_proxy* value from :class:`K8sProviderConfig` takes precedence
    over the ``NO_PROXY`` / ``no_proxy`` environment variables.
    """
    if config_no_proxy is not None:
        stripped = config_no_proxy.strip()
        if stripped:
            return stripped
    for var in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def _apply_proxy_to_default_configuration(
    logger: Optional[LoggingPort],
    config_proxy_url: Optional[str] = None,
    config_no_proxy: Optional[str] = None,
) -> None:
    """Patch the kubernetes global default Configuration with proxy settings.

    This is called *after* ``load_incluster_config`` so the loaded credentials
    are already in place.  We resolve the proxy settings (config values take
    precedence over env vars), apply them to a copy of the active default
    Configuration, then promote the patched copy back as the new default.

    When neither a config value nor a proxy env var is set this function is a
    no-op.

    Args:
        logger: Optional :class:`LoggingPort` for DEBUG messages.  When
            ``None`` proxy wiring is still applied silently.
        config_proxy_url: Explicit proxy URL from :class:`K8sProviderConfig`.
            Takes precedence over the proxy environment variables.
        config_no_proxy: Explicit ``no_proxy`` exclusion list from
            :class:`K8sProviderConfig`.  Takes precedence over the
            ``NO_PROXY`` / ``no_proxy`` environment variables.
    """
    try:
        from kubernetes.client import Configuration  # type: ignore[reportAttributeAccessIssue]
    except ImportError:  # pragma: no cover — kubernetes extra not installed
        return

    proxy_url = _resolve_proxy_url(config_proxy_url)
    no_proxy = _resolve_no_proxy(config_no_proxy)

    if proxy_url is None and no_proxy is None:
        return

    cfg = Configuration.get_default_copy()  # type: ignore[attr-defined]
    if proxy_url is not None:
        cfg.proxy = proxy_url  # type: ignore[attr-defined]
        if logger is not None:
            logger.debug(
                "K8s in-cluster: applying HTTP proxy from environment: %s",
                _redact_proxy_url(proxy_url),
            )
    if no_proxy is not None:
        cfg.no_proxy = no_proxy  # type: ignore[attr-defined]
        if logger is not None:
            logger.debug(
                "K8s in-cluster: NO_PROXY exclusion list from environment: %s",
                no_proxy,
            )
    Configuration.set_default(cfg)  # type: ignore[attr-defined]


def is_in_cluster(sentinel: Path | None = None) -> bool:
    """Return ``True`` when the in-cluster service-account secrets are present.

    Args:
        sentinel: Override path used in unit tests.  Defaults to the
            kubernetes-client canonical location.

    Returns:
        ``True`` if running inside a Kubernetes pod with a mounted service
        account, ``False`` otherwise.
    """
    path = sentinel if sentinel is not None else _IN_CLUSTER_SENTINEL
    try:
        return path.exists()
    except OSError:
        return False


def load_in_cluster_config(
    logger: Optional[LoggingPort] = None,
    proxy_url: Optional[str] = None,
    no_proxy: Optional[str] = None,
    client_configuration: Optional[object] = None,
) -> None:
    """Bootstrap ``kubernetes`` client config from in-cluster secrets.

    After loading the service-account credentials, the function wires an HTTP
    proxy into ``kubernetes.client.Configuration.proxy``.  When *proxy_url* is
    supplied (from :class:`K8sProviderConfig`) it takes precedence; otherwise
    the loader falls back to ``HTTPS_PROXY`` / ``https_proxy`` (preferred) or
    ``HTTP_PROXY`` / ``http_proxy``.  The exclusion list is taken from
    *no_proxy* (config) or the ``NO_PROXY`` / ``no_proxy`` environment
    variables and wired into ``Configuration.no_proxy``.  When neither a config
    value nor a proxy env var is set this step is a no-op.

    Args:
        logger: Optional :class:`LoggingPort` for DEBUG messages about proxy
            wiring.  Proxy is applied regardless of whether a logger is
            provided.
        proxy_url: Explicit proxy URL from :class:`K8sProviderConfig`.  Takes
            precedence over the proxy environment variables.  ``None`` falls
            back to the environment.
        no_proxy: Explicit ``no_proxy`` exclusion list from
            :class:`K8sProviderConfig`.  Takes precedence over the ``NO_PROXY``
            / ``no_proxy`` environment variables.  ``None`` falls back to the
            environment.
        client_configuration: Optional ``kubernetes.client.Configuration`` to
            load the credentials *into* instead of the global default.  When
            supplied the freshly-read service-account token is written directly
            into this object so a *live* ``ApiClient`` picks up the rotated
            token.  Used by the 401-recovery path.  When ``None`` (the default)
            the global default Configuration is populated, preserving the
            original behaviour.

    Raises:
        K8sAuthError: If the kubernetes SDK is not installed, or the
            in-cluster config cannot be loaded (e.g. missing service-account
            token).
    """
    try:
        from kubernetes import config as _k8s_config
    except ImportError as exc:  # pragma: no cover — extra not installed
        raise K8sAuthError(
            "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
        ) from exc

    try:
        if client_configuration is not None:
            _k8s_config.load_incluster_config(client_configuration=client_configuration)  # type: ignore[arg-type]
        else:
            _k8s_config.load_incluster_config()
    except Exception as exc:
        raise K8sAuthError(f"Failed to load in-cluster config: {exc}") from exc

    # Wire HTTP proxy (config value or environment) into the loaded
    # configuration.  Only the global-default path re-applies proxy; a targeted
    # client_configuration keeps the proxy it already carries.
    if client_configuration is None:
        _apply_proxy_to_default_configuration(logger, proxy_url, no_proxy)


class InClusterAuthAdapter:
    """Stateful wrapper around in-cluster config loading with token-refresh support.

    The adapter records the timestamp of the last successful
    ``load_incluster_config`` call and exposes :meth:`refresh_if_stale`
    so callers can proactively reload the service-account token before the
    current one expires.

    Args:
        token_refresh_seconds: How many seconds after the last load to
            trigger a refresh.  Defaults to 55 minutes (3300 s), which
            provides a comfortable margin before the token is rotated at
            80 % of its default 3600-second lifetime.
        logger: Optional :class:`LoggingPort` forwarded to
            ``load_in_cluster_config`` for proxy-wiring DEBUG messages.
        proxy_url: Explicit proxy URL from :class:`K8sProviderConfig`.  Carried
            across every (re)load so token refreshes re-apply the same proxy.
        no_proxy: Explicit ``no_proxy`` exclusion list from
            :class:`K8sProviderConfig`, carried across every (re)load.
    """

    def __init__(
        self,
        token_refresh_seconds: int = _DEFAULT_TOKEN_REFRESH_SECONDS,
        logger: Optional[LoggingPort] = None,
        proxy_url: Optional[str] = None,
        no_proxy: Optional[str] = None,
    ) -> None:
        self._token_refresh_seconds = token_refresh_seconds
        self._logger = logger
        self._proxy_url = proxy_url
        self._no_proxy = no_proxy
        self._last_loaded_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Initial load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Bootstrap the global kubernetes client config and record the timestamp."""
        load_in_cluster_config(
            logger=self._logger,
            proxy_url=self._proxy_url,
            no_proxy=self._no_proxy,
        )
        self._last_loaded_at = time.monotonic()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh_if_stale(self) -> bool:
        """Re-invoke ``load_incluster_config`` when the token has aged past TTL.

        Returns:
            ``True`` if a refresh was performed, ``False`` when the token
            is still within its valid window or was never loaded (no-op
            in that case — the caller must call :meth:`load` first).

        Raises:
            K8sAuthError: When the refresh fails (e.g. the token file is
                temporarily unavailable).
        """
        if self._last_loaded_at is None:
            return False

        age = time.monotonic() - self._last_loaded_at
        if age < self._token_refresh_seconds:
            return False

        load_in_cluster_config(
            logger=self._logger,
            proxy_url=self._proxy_url,
            no_proxy=self._no_proxy,
        )
        self._last_loaded_at = time.monotonic()
        return True

    # ------------------------------------------------------------------
    # Introspection helpers (for tests / health checks)
    # ------------------------------------------------------------------

    @property
    def last_loaded_at(self) -> Optional[float]:
        """Monotonic timestamp of the last successful load, or ``None``."""
        return self._last_loaded_at

    @property
    def token_age_seconds(self) -> Optional[float]:
        """Seconds since the last load, or ``None`` when never loaded."""
        if self._last_loaded_at is None:
            return None
        return time.monotonic() - self._last_loaded_at
