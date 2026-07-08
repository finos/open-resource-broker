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
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from orb.providers.k8s.exceptions.k8s_errors import K8sAuthError

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
    logger: Optional["LoggingPort"],
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
    import pathlib  # noqa: PLC0415 — stdlib, deferred to avoid top-level cost

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
        import yaml  # noqa: PLC0415 — optional; skip check if absent
    except ImportError:  # pragma: no cover — yaml not installed
        return

    try:
        raw = pathlib.Path(resolved).read_bytes()
        kubeconfig_data = yaml.safe_load(raw)
    except Exception:  # noqa: BLE001 — parse failure; let the SDK report it
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
    logger: Optional["LoggingPort"] = None,
) -> None:
    """Bootstrap the global ``kubernetes`` client config from a kubeconfig file.

    Before delegating to the kubernetes SDK, this function:

    1. Parses the kubeconfig with ``yaml.safe_load`` and inspects every
       user entry for ``exec:`` blocks.  Unknown exec plugin commands are
       blocked unless ``ORB_K8S_ALLOW_UNKNOWN_EXEC_PLUGIN=1`` is set.
    2. Sanitises error messages from the SDK so that raw exception text
       (which may embed file contents) is not forwarded to callers.

    Args:
        config_file: Path to the kubeconfig file.  When ``None`` the
            kubernetes client falls back to the ``KUBECONFIG`` env var and
            then the default ``~/.kube/config`` location.
        context: Name of the context to activate.  When ``None`` the
            current context from the kubeconfig is used.
        logger: Optional :class:`LoggingPort` for WARNING-level messages
            about allowed-but-unknown exec plugins.

    Raises:
        K8sAuthError: If the kubernetes SDK is not installed, an unknown
            exec plugin is found and the opt-out env var is unset, or the
            kubeconfig cannot be loaded.
    """
    # Step 1 — exec plugin allowlist check (before SDK import).
    _check_exec_plugins(config_file, logger)

    try:
        from kubernetes import config as _k8s_config  # noqa: PLC0415 — confined to this module
    except ImportError as exc:  # pragma: no cover — extra not installed
        raise K8sAuthError(
            "kubernetes SDK is not installed; install with `pip install orb-py[k8s]`"
        ) from exc

    # Step 2 — load with sanitised error surface.
    try:
        _k8s_config.load_kube_config(config_file=config_file, context=context)
    except K8sAuthError:
        raise
    except Exception as exc:
        raise K8sAuthError(_sanitise_load_error(exc, config_file)) from exc
