"""Interactive prompt helpers for ``orb init`` with the k8s provider.

Each function is pure I/O: it takes pre-fetched discovery data and a
:class:`~orb.domain.base.ports.console_port.ConsolePort`, performs
operator interaction, and returns the chosen value.  No kubernetes SDK
calls appear here so the functions can be unit-tested with a fake console
and no mock ``ApiClient``.

All user prompts call the built-in :func:`input` function directly,
following the same pattern used by ``init_command_handler.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from orb.providers.k8s.exceptions.k8s_errors import K8sError
from orb.providers.k8s.services.discovery_models import (
    KubeContextInfo,
    NamespaceInfo,
    RBACProbeResult,
    ServiceAccountInfo,
)

if TYPE_CHECKING:
    from orb.domain.base.ports.console_port import ConsolePort


def confirm_in_cluster(console: ConsolePort, detected: bool) -> bool:
    """Confirm whether ORB is running inside the cluster.

    Displays the auto-detected result and asks the operator to confirm.
    An empty answer (pressing Enter) accepts the detected value.

    Args:
        console: Console port for display output.
        detected: Result of the automatic in-cluster detection.

    Returns:
        Operator-confirmed boolean for the in-cluster flag.
    """
    detection_str = "yes" if detected else "no"
    console.info(f"  Running inside a Kubernetes pod (auto-detected: {detection_str}).")
    answer = input("  Confirm? [Y/n]: ").strip().lower()
    if answer == "":
        return detected
    return answer not in ("n", "no")


def pick_context(
    console: ConsolePort,
    contexts: list[KubeContextInfo],
    current: Optional[KubeContextInfo],
) -> str:
    """Prompt the operator to select a kubeconfig context.

    Displays a numbered list; the current context (when present) is
    pre-selected as the default.

    Args:
        console: Console port for display output.
        contexts: All available kubeconfig contexts.
        current: The active context, or ``None`` when none is set.

    Returns:
        The name of the chosen kubeconfig context.

    Raises:
        K8sError: When ``contexts`` is empty (no kubeconfig contexts
            available).
    """
    if not contexts:
        raise K8sError(
            "No kubeconfig contexts available; running outside in-cluster mode"
            " requires KUBECONFIG to be set"
        )

    console.info("")
    console.info("  Available kubeconfig contexts:")
    default_index = 1
    for i, ctx in enumerate(contexts, 1):
        marker = "  [current]" if ctx.is_current else ""
        console.info(f"    ({i}) {ctx.name}{marker}")
        if ctx.is_current:
            default_index = i

    answer = input(f"  Pick a kubeconfig context [{default_index}]: ").strip()
    if answer == "":
        return contexts[default_index - 1].name

    try:
        idx = int(answer) - 1
        if 0 <= idx < len(contexts):
            return contexts[idx].name
    except ValueError:
        return contexts[default_index - 1].name

    return contexts[default_index - 1].name


def pick_namespace(
    console: ConsolePort,
    namespaces: list[NamespaceInfo],
    default: str,
) -> str:
    """Prompt the operator to select a namespace.

    Displays a numbered list.  The ``default`` value (usually the SA-bound
    namespace) is pre-selected.  When ``namespaces`` is empty the ``default``
    is returned immediately without prompting (the caller already auto-selected
    from the SA-bound file).

    Args:
        console: Console port for display output.
        namespaces: Available namespace objects.
        default: Pre-selected namespace name.

    Returns:
        The chosen namespace name.
    """
    if not namespaces:
        return default

    console.info("")
    console.info("  Available namespaces:")
    default_index = 1
    for i, ns in enumerate(namespaces, 1):
        marker = "  [selected]" if ns.name == default else ""
        console.info(f"    ({i}) {ns.name}{marker}")
        if ns.name == default:
            default_index = i

    answer = input(f"  Pick a namespace [{default_index}]: ").strip()
    if answer == "":
        return namespaces[default_index - 1].name

    try:
        idx = int(answer) - 1
        if 0 <= idx < len(namespaces):
            return namespaces[idx].name
    except ValueError:
        return namespaces[default_index - 1].name

    return namespaces[default_index - 1].name


def pick_service_account(
    console: ConsolePort,
    sas: list[ServiceAccountInfo],
    default: str = "default",
) -> str:
    """Prompt the operator to select a ServiceAccount for the template default.

    The prompt is skippable: pressing Enter with an empty answer returns an
    empty string (caller interprets as "no SA default").

    Args:
        console: Console port for display output.
        sas: Available ServiceAccount objects.
        default: The SA name to pre-select.

    Returns:
        The chosen SA name, or ``""`` when skipped.
    """
    if not sas:
        return ""

    console.info("")
    console.info("  Available ServiceAccounts:")
    default_index = 0  # 0 = no pre-selection by number (skip is default)
    for i, sa in enumerate(sas, 1):
        marker = "  [current]" if sa.name == default else ""
        console.info(f"    ({i}) {sa.name}{marker}")
        if sa.name == default:
            default_index = i

    prompt_default = str(default_index) if default_index else "skip"
    answer = input(f"  Pick a ServiceAccount [{prompt_default}]: ").strip()
    if answer == "":
        # Empty = skip (no service account default)
        return ""

    try:
        idx = int(answer) - 1
        if 0 <= idx < len(sas):
            return sas[idx].name
    except ValueError:
        return ""

    return ""


def pick_image_pull_secret(
    console: ConsolePort,
    secrets: list[str],
) -> Optional[str]:
    """Prompt the operator to select a default image pull secret.

    Lists all docker-registry secret names plus a ``none`` option.  Empty
    secrets list skips the prompt and returns ``None``.

    Args:
        console: Console port for display output.
        secrets: Available image pull secret names.

    Returns:
        The chosen secret name, or ``None`` when none is selected.
    """
    if not secrets:
        return None

    console.info("")
    console.info("  Available image pull secrets:")
    none_index = len(secrets) + 1
    for i, name in enumerate(secrets, 1):
        console.info(f"    ({i}) {name}")
    console.info(f"    ({none_index}) none")

    answer = input("  Pick an image pull secret [none]: ").strip()
    if answer == "" or answer == str(none_index):
        return None

    try:
        idx = int(answer) - 1
        if 0 <= idx < len(secrets):
            return secrets[idx]
    except ValueError:
        return None

    return None


def display_rbac_probe(
    console: ConsolePort,
    results: RBACProbeResult,
    namespace: Optional[str] = None,
    sa: Optional[str] = None,
) -> bool:
    """Display the RBAC probe result table and return whether to continue.

    Renders a table of verb → allowed/denied.  When any verb is denied, a
    pre-formatted ``kubectl create rolebinding`` remediation command is shown
    and the operator is asked whether to continue with degraded permissions.

    Args:
        console: Console port for display output.
        results: The :class:`RBACProbeResult` from the discovery service.
        namespace: The namespace that was probed (used in the remediation
            command).
        sa: The ServiceAccount name (used in the remediation command).

    Returns:
        ``True`` when the operator chooses to continue (or all permissions
        are present); ``False`` to abort ``orb init``.
    """
    _ns = namespace or results.namespace
    _sa = sa or "orb-runner"

    def _tick(ok: bool) -> str:
        return "granted" if ok else "DENIED "

    console.info("")
    console.info("  Probing required permissions...")
    console.info("")
    console.info(f"    create pods   {_tick(results.can_create_pods)}")
    console.info(f"    watch pods    {_tick(results.can_watch_pods)}")
    console.info(f"    delete pods   {_tick(results.can_delete_pods)}")
    console.info("")

    if results.all_granted:
        console.success("  All required permissions are present.")
        return True

    # Build remediation command.
    remediation = (
        f"kubectl create rolebinding orb-runner-pods"
        f" \\\n"
        f"      --clusterrole=orb-pod-manager"
        f" \\\n"
        f"      --serviceaccount={_ns}:{_sa}"
        f" \\\n"
        f"      --namespace={_ns}"
    )

    console.warning(f"  Missing required permissions in namespace '{_ns}'.")
    console.info("  To grant them, run:")
    console.command(f"    {remediation}")
    console.info("")

    answer = input("  Continue with degraded permissions? [y/N]: ").strip().lower()
    return answer in ("y", "yes")
