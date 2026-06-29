"""Interactive prompt helpers for ``orb init`` with the k8s provider.

Each function is pure I/O: it takes pre-fetched discovery data and a
:class:`~orb.domain.base.ports.console_port.ConsolePort`, performs
operator interaction, and returns the chosen value.  No kubernetes SDK
calls appear here so the functions can be unit-tested with a fake console
and no mock ``ApiClient``.

Phase C will replace the stub bodies with real prompt logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from orb.providers.k8s.services.discovery_models import (
    KubeContextInfo,
    NamespaceInfo,
    RBACProbeResult,
    ServiceAccountInfo,
)

if TYPE_CHECKING:
    from orb.domain.base.ports.console_port import ConsolePort


def prompt_context(
    contexts: list[KubeContextInfo],
    console: "ConsolePort",
) -> KubeContextInfo:
    """Prompt the operator to select a kubeconfig context.

    Returns the first context in the list (the current context when
    available, or the first entry when no current context is flagged).
    Phase C replaces this with an interactive numbered-list prompt.
    """
    current = next((c for c in contexts if c.is_current), None)
    if current is not None:
        return current
    if contexts:
        return contexts[0]
    # Fallback sentinel when no contexts are available.
    return KubeContextInfo(name="", cluster="", user="", namespace=None, is_current=False)


def prompt_namespace(
    namespaces: list[NamespaceInfo],
    sa_bound: Optional[str],
    console: "ConsolePort",
) -> str:
    """Prompt the operator to select a namespace.

    Returns the SA-bound namespace when provided, the first active
    namespace in the list, or ``"default"`` as a last resort.
    Phase C replaces this with an interactive numbered-list prompt.
    """
    if sa_bound:
        return sa_bound
    active = [n for n in namespaces if n.status == "Active"]
    if active:
        return active[0].name
    return "default"


def prompt_service_account(
    accounts: list[ServiceAccountInfo],
    console: "ConsolePort",
) -> Optional[str]:
    """Prompt the operator to select a ServiceAccount for the template default.

    Returns ``None`` (skip) when no accounts are available.  Phase C
    replaces this with an interactive numbered-list prompt.
    """
    if not accounts:
        return None
    return accounts[0].name


def prompt_image_pull_secret(
    secrets: list[str],
    console: "ConsolePort",
) -> Optional[str]:
    """Prompt the operator to select a default image pull secret.

    Returns ``None`` (no default) when the list is empty.  Phase C
    replaces this with an interactive numbered-list prompt.
    """
    if not secrets:
        return None
    return secrets[0]


def display_rbac_probe(
    result: RBACProbeResult,
    namespace: str,
    sa: Optional[str],
    console: "ConsolePort",
) -> bool:
    """Display the RBAC probe result and return whether to continue.

    Returns ``True`` (continue) unconditionally in the stub.  Phase C
    will surface the formatted ``kubectl create rolebinding`` remediation
    command and prompt the operator when permissions are missing.
    """
    return True
