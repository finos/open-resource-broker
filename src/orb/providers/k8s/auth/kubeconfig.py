"""kubeconfig-based Kubernetes config loader.

Thin wrapper around ``kubernetes.config.load_kube_config`` for the
out-of-cluster case.  Keeps the ``kubernetes`` SDK import confined to this
package and exposes a small, unit-testable seam.

Security hardening
------------------
Before delegating to the SDK, this module parses the kubeconfig with a
plain YAML load and inspects every user entry for ``exec:`` blocks.  An
``exec`` credential plugin executes an arbitrary binary on the local
machine; unknown binaries are blocked unless the operator has explicitly
set ``ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN=1``.  Well-known cloud-provider
auth plugins (``aws``, ``aws-iam-authenticator``, ``gke-gcloud-auth-plugin``,
``kubelogin``, ``azure-cli``) are allowed unconditionally.

Error messages emitted from the fallback ``except`` block are sanitised so
that raw SDK exception text (which may embed file contents) is never
forwarded to callers.  Only the config_file path and a coarse error
category are included.

HTTP proxy
----------
After loading, the module reads ``HTTPS_PROXY`` / ``https_proxy`` (preferred
for apiserver TLS traffic) falling back to ``HTTP_PROXY`` / ``http_proxy``,
and wires the resolved URL into ``kubernetes.client.Configuration.proxy``.
``NO_PROXY`` / ``no_proxy`` is similarly honoured via
``Configuration.no_proxy``.  When a proxy is applied a DEBUG log is emitted
so operators can confirm their environment is wired correctly.
"""

from __future__ import annotations

import contextlib
import os
import sys
import urllib.parse
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Optional

from orb.providers.k8s.exceptions.k8s_exceptions import K8sAuthError

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort


# Exec plugin commands that are unconditionally permitted.  Operators who
# run a different binary must set ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN=1.
_ALLOWED_EXEC_COMMANDS: frozenset[str] = frozenset(
    {
        "aws",
        "aws-iam-authenticator",
        "gke-gcloud-auth-plugin",
        "kubelogin",
        "azure-cli",
    }
)

_ENV_ALLOW_UNKNOWN = "ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN"


# ---------------------------------------------------------------------------
# Non-interactive exec-plugin guard
# ---------------------------------------------------------------------------
#
# WHY: the kubernetes SDK's exec-credential path (kubernetes/config/exec_provider.py)
# decides whether to run the credential plugin (e.g. ``aws eks get-token``)
# interactively *solely* by inspecting ``sys.stdout.isatty()`` — it does NOT
# consult the kubeconfig ``interactiveMode`` field.  When ORB is attached to a
# real terminal that check returns True, so the SDK runs the plugin with the
# terminal's stdin/stderr inherited.  For an EKS kubeconfig whose exec uses an
# AWS profile backed by an interactive ``credential_process``, that interactive
# invocation causes the minted bearer token to not be attached to the API
# request — the SDK sends an empty Authorization header and the apiserver
# returns 401.  The same plugin run non-interactively (stdout redirected)
# attaches the token and succeeds.
#
# ORB is a broker/CLI/server that must authenticate to Kubernetes identically
# whether or not it happens to be attached to a terminal, so it always drives
# the exec plugin non-interactively.  This guard temporarily makes
# ``sys.stdout.isatty()`` report False for the duration of the SDK entry points
# ORB calls (config load + the lazy on-request token-refresh hook), forcing the
# exec provider onto its non-interactive branch.  The guard is a no-op for the
# in-cluster service-account path and for non-exec auth (bearer/cert), which
# never consult ``isatty()``.


class _NonTtyStdoutProxy:
    """Delegates every attribute to the wrapped stdout except ``isatty``.

    Only ``isatty()`` is overridden (to always return ``False``); reads,
    writes, ``fileno``, ``flush`` and everything else pass straight through to
    the real stream so nothing else about stdout's behaviour changes.
    """

    __slots__ = ("_wrapped",)

    def __init__(self, wrapped: Any) -> None:
        object.__setattr__(self, "_wrapped", wrapped)

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_wrapped"), name)


@contextlib.contextmanager
def _force_non_interactive_exec() -> Iterator[None]:
    """Temporarily force the k8s SDK exec plugin onto its non-interactive branch.

    Replaces ``sys.stdout`` with a thin proxy whose ``isatty()`` returns
    ``False`` for the duration of the block, then restores the original stream.
    The kubernetes ``ExecProvider`` keys interactivity off ``sys.stdout.isatty()``
    alone, so this deterministically selects the non-interactive branch that
    correctly attaches the exec-minted bearer token.

    When ``sys.stdout`` is already non-interactive (redirected output, no TTY)
    the proxy changes nothing observable, so wrapping is always safe.

    Concurrency note: ``sys.stdout`` is process-global, so this swap is visible
    to every thread for its (deliberately tiny) duration.  It is only ever
    entered around the single SDK call that may spawn the exec plugin — the
    initial ``load_kube_config`` and the lazy ``refresh_api_key_hook`` re-mint —
    never around surrounding work and never across a blocking ``watch.stream()``
    read (the auth header, and therefore the hook, is built when the stream is
    opened, before the read blocks).  Keeping the region this short bounds the
    window in which a concurrent thread could observe the non-tty proxy to the
    duration of one credential mint.
    """
    original = sys.stdout
    if isinstance(original, _NonTtyStdoutProxy):
        # Already guarded (re-entrant call) — do not double-wrap.
        yield
        return
    sys.stdout = _NonTtyStdoutProxy(original)  # type: ignore[assignment]
    try:
        yield
    finally:
        # Restore the stream we actually replaced.  A nested guard short-circuits
        # above without swapping, so only the outermost guard restores, and it
        # restores the exact object it captured (not a stale global read).
        sys.stdout = original


# Sentinel attribute marking a ``refresh_api_key_hook`` already wrapped by
# :func:`_install_non_interactive_refresh_hook`, so repeated installs (every
# ``load_kubeconfig`` / ``force_token_refresh``) never double-wrap.
_ORB_HOOK_WRAPPED_ATTR = "_orb_non_interactive_wrapped"


def _install_non_interactive_refresh_hook(client_configuration: Any) -> None:
    """Wrap a Configuration's ``refresh_api_key_hook`` in the non-interactive guard.

    The kubernetes SDK attaches a ``refresh_api_key_hook`` to the
    ``Configuration`` whenever a kubeconfig user carries an exec-minted (or
    otherwise expiring) bearer token.  That hook is invoked *lazily* by the SDK
    inside ``get_api_key_with_prefix`` — i.e. while building the Authorization
    header of **every** authenticated request the client makes (resource verbs,
    watch-stream opens, status polls).  When the embedded token expiry lapses
    the hook re-runs the exec credential plugin, and that plugin's interactivity
    is decided solely by ``sys.stdout.isatty()``.

    Wrapping the hook means every one of those request-path re-mints takes the
    non-interactive branch, with the ``sys.stdout`` swap scoped to just the hook
    body (the credential mint) rather than the whole request or the client's
    lifetime.  This is the single seam that covers the steady-state request path
    without holding a global stdout swap open across concurrent work or blocking
    watch reads.

    The wrap is a no-op when:

    * no hook is set (non-exec auth: static bearer token, client certificate) —
      those paths never consult ``isatty()``; or
    * the hook is already wrapped (idempotent across repeated loads).
    """
    hook = getattr(client_configuration, "refresh_api_key_hook", None)
    if hook is None or getattr(hook, _ORB_HOOK_WRAPPED_ATTR, False):
        return

    def _wrapped(cfg: Any) -> Any:
        with _force_non_interactive_exec():
            return hook(cfg)

    setattr(_wrapped, _ORB_HOOK_WRAPPED_ATTR, True)
    client_configuration.refresh_api_key_hook = _wrapped


def _install_non_interactive_refresh_hook_on(client_configuration: Optional[object]) -> None:
    """Install the non-interactive refresh-hook wrap on the configuration just loaded.

    When *client_configuration* is supplied (the ``force_token_refresh`` path
    reloading into a live ``ApiClient``'s config), the hook is wrapped directly
    on that object.  When ``None`` (the initial global-default load), the SDK
    wrote the fresh config into ``Configuration._default``; wrap the hook there
    so ``ApiClient`` instances built afterwards — which deep-copy the default —
    inherit the wrapped hook (a plain function survives ``deepcopy`` by
    identity, so the copies share the same guarded callable).
    """
    if client_configuration is not None:
        _install_non_interactive_refresh_hook(client_configuration)
        return

    try:
        from kubernetes.client import Configuration  # type: ignore[reportAttributeAccessIssue]
    except ImportError:  # pragma: no cover — kubernetes extra not installed
        return

    default = getattr(Configuration, "_default", None)
    if default is not None:
        _install_non_interactive_refresh_hook(default)


def _redact_proxy_url(url: str) -> str:
    """Return *url* with the userinfo (user:password) component replaced by ``***``.

    ``HTTPS_PROXY`` values often take the form ``http://user:pass@proxy:port``.
    Logging the raw URL at DEBUG level would expose credentials in log files.

    ``urllib.parse`` only populates ``username``/``password`` when it finds a
    scheme and network-location component.  A scheme-less but credentialed
    value such as ``user:pass@proxy:3128`` parses entirely into ``path`` with
    ``username`` unset, so we additionally detect a userinfo ``@`` segment in
    the authority portion by hand and redact it.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.username or parsed.password:
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

    This is called *after* ``load_kube_config`` so the loaded credentials are
    already in place.  We resolve the proxy settings (config values take
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

    # Retrieve the current default, patch it, and promote it back.
    cfg = Configuration.get_default_copy()  # type: ignore[attr-defined]
    if proxy_url is not None:
        cfg.proxy = proxy_url  # type: ignore[attr-defined]
        if logger is not None:
            logger.debug(
                "K8s kubeconfig: applying HTTP proxy from environment: %s",
                _redact_proxy_url(proxy_url),
            )
    if no_proxy is not None:
        cfg.no_proxy = no_proxy  # type: ignore[attr-defined]
        if logger is not None:
            logger.debug(
                "K8s kubeconfig: NO_PROXY exclusion list from environment: %s",
                no_proxy,
            )
    Configuration.set_default(cfg)  # type: ignore[attr-defined]


def _sanitise_load_error(exc: Exception, config_file: Optional[str]) -> str:
    """Return a sanitised error message that does not embed raw SDK output.

    The SDK may include file fragments in its error string.  This function
    reduces the message to three pieces of information:

    * The config file path (under operator control — not secret).
    * The exception type name (category signal, no content).
    * A human-readable category string derived from the exception type.
    """
    exc_type = type(exc).__name__
    lower = exc_type.lower()

    if "permission" in lower or "access" in lower:
        category = "permission denied"
    elif "yaml" in lower or "scanner" in lower or "parser" in lower or "value" in lower:
        category = "invalid yaml"
    elif "configexception" in lower or "context" in lower or "notfound" in lower:
        category = "context not found"
    else:
        category = "other"

    return f"Failed to load kubeconfig (config_file={config_file!r}): {exc_type} — {category}"


def _check_exec_plugins(
    config_file: Optional[str],
    logger: Optional[LoggingPort],
) -> None:
    """Parse *config_file* and reject unknown exec credential plugins.

    When *config_file* is ``None`` the function resolves the path via the
    ``KUBECONFIG`` env var then ``~/.kube/config``.  If the resolved path
    does not exist the check is skipped (the SDK will surface the error
    on its own load attempt).

    Args:
        config_file: Path to the kubeconfig file, or ``None`` for default.
        logger: Optional :class:`LoggingPort` for WARNING messages.  When
            ``None`` the check runs silently (callers that have no logger
            available still benefit from the block; they just lose the
            diagnostic message).

    Raises:
        K8sAuthError: When an unknown exec plugin is found and the opt-out
            env var is not set.
    """
    import pathlib

    # Resolve path
    resolved: Optional[str] = config_file
    if resolved is None:
        env_kc = os.environ.get("KUBECONFIG")
        if env_kc:
            # KUBECONFIG may be a colon-separated list; inspect only the first.
            resolved = env_kc.split(os.pathsep)[0]
        else:
            resolved = str(pathlib.Path.home() / ".kube" / "config")

    if not pathlib.Path(resolved).exists():
        return

    try:
        import yaml
    except ImportError:  # pragma: no cover — yaml not installed
        return

    try:
        raw = pathlib.Path(resolved).read_bytes()
        kubeconfig_data = yaml.safe_load(raw)
    except Exception:
        return

    if not isinstance(kubeconfig_data, dict):
        return

    users = kubeconfig_data.get("users") or []
    allow_unknown = os.environ.get(_ENV_ALLOW_UNKNOWN, "").strip() == "1"

    for user_entry in users:
        if not isinstance(user_entry, dict):
            continue
        user_block = user_entry.get("user") or {}
        if not isinstance(user_block, dict):
            continue
        exec_block = user_block.get("exec")
        if not isinstance(exec_block, dict):
            continue

        command: Optional[str] = exec_block.get("command")
        if not command:
            continue

        # Only the basename is checked — a full path like
        # /usr/local/bin/aws-iam-authenticator must still resolve.
        # NOTE: this is a best-effort advisory guard, not a security
        # boundary.  A malicious kubeconfig can set command to
        # "/tmp/aws" (basename "aws") and bypass this check; the
        # assumption is that the kubeconfig file itself is trusted
        # (operator-supplied, not user-uploaded).  Operators who run
        # ORB in a higher-trust context should set
        # ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN=0 and restrict kubeconfig
        # file ownership at the OS level.
        command_base = pathlib.Path(command).name

        if command_base not in _ALLOWED_EXEC_COMMANDS:
            user_name = user_entry.get("name", "<unknown>")
            message = (
                f"kubeconfig exec plugin {command_base!r} is not on the ORB allowlist "
                f"(user={user_name!r}, config_file={resolved!r}).  "
                f"Set {_ENV_ALLOW_UNKNOWN}=1 to permit unknown exec plugins."
            )
            if allow_unknown:
                if logger is not None:
                    logger.warning(
                        "K8s kubeconfig: unknown exec plugin allowed via env override: %s",
                        message,
                    )
            else:
                raise K8sAuthError(message)


def load_kubeconfig(
    config_file: Optional[str] = None,
    context: Optional[str] = None,
    logger: Optional[LoggingPort] = None,
    proxy_url: Optional[str] = None,
    no_proxy: Optional[str] = None,
    client_configuration: Optional[object] = None,
) -> None:
    """Bootstrap ``kubernetes`` client config from a kubeconfig file.

    Before delegating to the kubernetes SDK, this function:

    1. Parses the kubeconfig with ``yaml.safe_load`` and inspects every
       user entry for ``exec:`` blocks.  Unknown exec plugin commands are
       blocked unless ``ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN=1`` is set.
    2. Sanitises error messages from the SDK so that raw exception text
       (which may embed file contents) is not forwarded to callers.
    3. Wires an HTTP proxy into ``kubernetes.client.Configuration.proxy``.
       When *proxy_url* is supplied (from :class:`K8sProviderConfig`) it takes
       precedence; otherwise the loader falls back to ``HTTPS_PROXY`` /
       ``https_proxy`` (preferred) or ``HTTP_PROXY`` / ``http_proxy``.  The
       exclusion list is taken from *no_proxy* (config) or the ``NO_PROXY`` /
       ``no_proxy`` environment variables and wired into
       ``Configuration.no_proxy``.

    Args:
        config_file: Path to the kubeconfig file.  When ``None`` the
            kubernetes client falls back to the ``KUBECONFIG`` env var and
            then the default ``~/.kube/config`` location.
        context: Name of the context to activate.  When ``None`` the
            current context from the kubeconfig is used.
        logger: Optional :class:`LoggingPort` for WARNING-level messages
            about allowed-but-unknown exec plugins and DEBUG-level messages
            about proxy wiring.
        proxy_url: Explicit proxy URL from :class:`K8sProviderConfig`.  Takes
            precedence over the proxy environment variables.  ``None`` falls
            back to the environment.
        no_proxy: Explicit ``no_proxy`` exclusion list from
            :class:`K8sProviderConfig`.  Takes precedence over the ``NO_PROXY``
            / ``no_proxy`` environment variables.  ``None`` falls back to the
            environment.
        client_configuration: Optional ``kubernetes.client.Configuration`` to
            load the credentials *into*.  When supplied, the SDK re-runs the
            exec credential plugin (re-minting an EKS/GKE/AKS token) and writes
            the fresh Bearer token plus the expiry-based
            ``refresh_api_key_hook`` directly into this object rather than the
            global default.  This is the 401-recovery path used by
            :meth:`K8sClient.force_token_refresh`: the pinned kubernetes SDK's
            ``ExecProvider`` performs no token caching — it spawns the plugin
            (e.g. ``aws eks get-token``) on *every* ``load_kube_config`` call —
            so re-running the load here re-mints a fresh token and writes it
            into this *live* ``ApiClient``'s configuration, and its
            already-built typed API clients pick up the new token without
            rebuilding the connection pool.  When ``None`` (the default) the
            global default Configuration is populated, preserving the original
            behaviour.  Proxy re-wiring is skipped in the targeted case because
            ``load_kube_config`` does not clear an existing
            ``Configuration.proxy`` — the value carried on the live config is
            retained.

    Raises:
        K8sAuthError: If the kubernetes SDK is not installed, an unknown
            exec plugin is found and the opt-out env var is unset, or the
            kubeconfig cannot be loaded.
    """
    # Step 1 — exec plugin allowlist check (before SDK import).
    _check_exec_plugins(config_file, logger)

    try:
        from kubernetes import config as _k8s_config
    except ImportError as exc:  # pragma: no cover — extra not installed
        raise K8sAuthError(
            "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
        ) from exc

    # Step 2 — load with sanitised error surface.  The load is wrapped in the
    # non-interactive guard because ``load_kube_config`` runs the exec
    # credential plugin synchronously to mint the initial bearer token; on a
    # TTY the SDK would otherwise run it interactively and fail to attach the
    # token (see ``_force_non_interactive_exec`` above).
    try:
        with _force_non_interactive_exec():
            if client_configuration is not None:
                _k8s_config.load_kube_config(
                    config_file=config_file,
                    context=context,
                    client_configuration=client_configuration,  # type: ignore[arg-type]
                )
            else:
                _k8s_config.load_kube_config(config_file=config_file, context=context)
    except K8sAuthError:
        raise
    except Exception as exc:
        raise K8sAuthError(_sanitise_load_error(exc, config_file)) from exc

    # Step 2b — wrap the lazy on-request token-refresh hook that the SDK just
    # installed so its exec re-mint also takes the non-interactive branch.  The
    # hook fires from inside ``get_api_key_with_prefix`` on every authenticated
    # request the client subsequently makes (resource verbs, watch-stream opens,
    # status polls); guarding it here covers the whole steady-state request path
    # with one seam.  When a specific live configuration was loaded into, wrap
    # that object; otherwise wrap the global default the SDK set.
    _install_non_interactive_refresh_hook_on(client_configuration)

    # Step 3 — wire HTTP proxy (config value or environment) into the loaded
    # configuration.  Only the global-default path re-applies proxy; a targeted
    # client_configuration keeps the proxy it was built with (load_kube_config
    # does not clear it).
    if client_configuration is None:
        _apply_proxy_to_default_configuration(logger, proxy_url, no_proxy)
