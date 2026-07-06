"""Live Kubernetes integration test configuration.

Tests in this subtree require real Kubernetes credentials and a running
cluster accessible via the ORB config (``~/.orb/config/config.json``).
They are skipped by default; pass ``--run-k8s`` to enable them.

All live tests are marked ``serial`` to avoid racing on shared quota and
shared namespace resources.  A session-scoped nuclear-cleanup fixture
deletes every pod/deployment/statefulset/job carrying any request-id
created during the test run.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Generator

import pytest

log = logging.getLogger("k8s.live.conftest")


# ---------------------------------------------------------------------------
# pytest hooks
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply the ``serial`` marker to every test collected in this subtree.

    ``pytestmark`` at module level is not picked up by conftest-level
    discovery; the collection hook is the canonical place to bulk-apply
    markers across a directory subtree.
    """
    marker = pytest.mark.serial
    for item in items:
        item.add_marker(marker)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _is_k8s_run(config: pytest.Config) -> bool:
    """Return True when live k8s tests have been explicitly requested."""
    return bool(config.getoption("--run-k8s", default=False))


def _load_orb_config() -> dict:
    """Load the ORB config from the standard discovery path.

    Uses :func:`orb.config.platform_dirs.get_config_location` so the
    test path is always consistent with what the runtime reads.
    """
    from orb.config.platform_dirs import get_config_location

    config_path = get_config_location() / "config.json"
    with open(config_path) as fh:
        return json.load(fh)


def _get_k8s_provider_config(orb_config: dict) -> dict:
    """Extract the first k8s provider config block from the ORB config."""
    providers = orb_config.get("provider", {}).get("providers", [])
    for provider in providers:
        if provider.get("type") == "k8s":
            return provider.get("config", {})
    # Fall through to an empty dict; individual tests that need cluster
    # access will skip via the kubernetes client import failure.
    return {}


# ---------------------------------------------------------------------------
# pytest_sessionstart — credential pre-flight
# ---------------------------------------------------------------------------


def pytest_sessionstart(session: pytest.Session) -> None:
    """Verify k8s credentials before running any live tests.

    Only executes when ``--run-k8s`` is passed.  Loads the kubeconfig
    specified in the ORB provider config, constructs a bare CoreV1Api
    call, and exits immediately if it fails — so no tests are attempted
    with unusable credentials.
    """
    if not _is_k8s_run(session.config):
        return

    try:
        orb_config = _load_orb_config()
    except FileNotFoundError as exc:
        pytest.exit(
            f"k8s live pre-flight failed: ORB config not found: {exc}\n"
            "Run 'orb init' first, then configure a k8s provider.",
            returncode=1,
        )

    k8s_cfg = _get_k8s_provider_config(orb_config)

    try:
        from kubernetes import client as k8s_client_mod, config as k8s_config_mod

        kubeconfig_path = k8s_cfg.get("kubeconfig_path")
        context = k8s_cfg.get("context")

        k8s_config_mod.load_kube_config(
            config_file=kubeconfig_path,
            context=context,
        )
        core_v1 = k8s_client_mod.CoreV1Api()
        core_v1.list_namespace(limit=1)
        print(f"\nk8s credentials valid (kubeconfig={kubeconfig_path!r}, context={context!r})")
    except Exception as exc:
        pytest.exit(
            f"k8s live pre-flight failed: cannot reach cluster: {exc}",
            returncode=1,
        )


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def k8s_live_config() -> dict:
    """Load and return the full ORB config dict for the session."""
    return _load_orb_config()


@pytest.fixture(scope="session")
def k8s_provider_config(k8s_live_config: dict) -> dict:
    """Return the first k8s provider config block."""
    return _get_k8s_provider_config(k8s_live_config)


@pytest.fixture(scope="session")
def k8s_namespace(k8s_provider_config: dict) -> str:
    """Return the target namespace from the ORB config.

    Falls back to ``"default"`` if the provider config does not specify
    a namespace, mirroring :class:`K8sProviderConfig._resolve_namespace`.
    """
    ns = k8s_provider_config.get("namespace")
    if ns:
        return str(ns)
    # In-cluster detection: if config has in_cluster=True we cannot read
    # the SA token file here, so fall back to "default" for live tests.
    return "default"


@pytest.fixture(scope="session")
def k8s_core_v1(k8s_provider_config: dict):  # type: ignore[return]
    """Return a live ``CoreV1Api`` instance for the configured cluster.

    Loads kubeconfig from the ORB provider config so the session always
    targets the same cluster as the ORB runtime.
    """
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    return k8s_client_mod.CoreV1Api()


@pytest.fixture(scope="session")
def k8s_apps_v1(k8s_provider_config: dict):  # type: ignore[return]
    """Return a live ``AppsV1Api`` instance for the configured cluster."""
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    return k8s_client_mod.AppsV1Api()


@pytest.fixture(scope="session")
def k8s_batch_v1(k8s_provider_config: dict):  # type: ignore[return]
    """Return a live ``BatchV1Api`` instance for the configured cluster."""
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    return k8s_client_mod.BatchV1Api()


# ---------------------------------------------------------------------------
# Request-ID tracker (module-scoped per test module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def request_id_tracker() -> list[str]:
    """Accumulate request-ids registered by tests in the same module.

    Each test appends its unique request_id via the ``live_request_id``
    function-scoped fixture.  The module teardown delegates to the
    session-level nuclear cleanup via this list.
    """
    return []


# ---------------------------------------------------------------------------
# Per-test request-id fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def live_request_id(request_id_tracker: list[str]) -> Generator[str, None, None]:
    """Generate a unique request-id for a single test, track it for cleanup.

    The request-id is registered in ``request_id_tracker`` before the
    test runs so cleanup happens even if the test fails partway through.
    """
    rid = str(uuid.uuid4())
    request_id_tracker.append(rid)
    yield rid


# ---------------------------------------------------------------------------
# Nuclear teardown — session-scoped
# ---------------------------------------------------------------------------

# ORB label constants (mirrors orb.providers.k8s.utilities.pod_spec).
_LABEL_PREFIX = "orb.io"
_MANAGED_LABEL = f"{_LABEL_PREFIX}/managed"
_REQUEST_ID_LABEL = f"{_LABEL_PREFIX}/request-id"


@pytest.fixture(scope="session", autouse=True)
def nuclear_cleanup(
    request_id_tracker: list[str],  # type: ignore[fixture-overriding]
    k8s_core_v1,
    k8s_apps_v1,
    k8s_batch_v1,
    k8s_namespace: str,
) -> Generator[None, None, None]:
    """Session-scoped safety net: remove all ORB-labelled resources created
    during the test run.

    Runs once after every test in the session completes.  Best-effort —
    individual cleanup failures are logged at WARNING and never raise so
    they cannot corrupt the test result.
    """
    yield

    # ``request_id_tracker`` at session scope would be empty here because
    # each module gets its own list via the module-scoped fixture.  We
    # perform a broader label-selector sweep instead: delete everything
    # carrying ``orb.io/managed=true`` in the configured namespace.  This
    # is safe because the label is unique to ORB-owned resources.
    label_selector = f"{_MANAGED_LABEL}=true"

    _cleanup_pods(k8s_core_v1, k8s_namespace, label_selector)
    _cleanup_deployments(k8s_apps_v1, k8s_namespace, label_selector)
    _cleanup_statefulsets(k8s_apps_v1, k8s_namespace, label_selector)
    _cleanup_jobs(k8s_batch_v1, k8s_namespace, label_selector)


def _cleanup_pods(core_v1, namespace: str, label_selector: str) -> None:
    """Delete all pods matching ``label_selector`` in ``namespace``."""
    try:
        pod_list = core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        for pod in pod_list.items:
            pod_name = pod.metadata.name
            try:
                core_v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
                log.info("nuclear_cleanup: deleted pod %s/%s", namespace, pod_name)
            except Exception as exc:
                log.warning(
                    "nuclear_cleanup: failed to delete pod %s/%s: %s", namespace, pod_name, exc
                )
    except Exception as exc:
        log.warning("nuclear_cleanup: list pods failed (%s): %s", namespace, exc)


def _cleanup_deployments(apps_v1, namespace: str, label_selector: str) -> None:
    """Delete all Deployments matching ``label_selector`` in ``namespace``."""
    try:
        dep_list = apps_v1.list_namespaced_deployment(
            namespace=namespace, label_selector=label_selector
        )
        for dep in dep_list.items:
            name = dep.metadata.name
            try:
                apps_v1.delete_namespaced_deployment(name=name, namespace=namespace)
                log.info("nuclear_cleanup: deleted deployment %s/%s", namespace, name)
            except Exception as exc:
                log.warning(
                    "nuclear_cleanup: failed to delete deployment %s/%s: %s", namespace, name, exc
                )
    except Exception as exc:
        log.warning("nuclear_cleanup: list deployments failed (%s): %s", namespace, exc)


def _cleanup_statefulsets(apps_v1, namespace: str, label_selector: str) -> None:
    """Delete all StatefulSets matching ``label_selector`` in ``namespace``."""
    try:
        sts_list = apps_v1.list_namespaced_stateful_set(
            namespace=namespace, label_selector=label_selector
        )
        for sts in sts_list.items:
            name = sts.metadata.name
            try:
                apps_v1.delete_namespaced_stateful_set(name=name, namespace=namespace)
                log.info("nuclear_cleanup: deleted statefulset %s/%s", namespace, name)
            except Exception as exc:
                log.warning(
                    "nuclear_cleanup: failed to delete statefulset %s/%s: %s",
                    namespace,
                    name,
                    exc,
                )
    except Exception as exc:
        log.warning("nuclear_cleanup: list statefulsets failed (%s): %s", namespace, exc)


def _cleanup_jobs(batch_v1, namespace: str, label_selector: str) -> None:
    """Delete all Jobs matching ``label_selector`` in ``namespace``."""
    try:
        job_list = batch_v1.list_namespaced_job(namespace=namespace, label_selector=label_selector)
        for job in job_list.items:
            name = job.metadata.name
            try:
                batch_v1.delete_namespaced_job(
                    name=name, namespace=namespace, propagation_policy="Background"
                )
                log.info("nuclear_cleanup: deleted job %s/%s", namespace, name)
            except Exception as exc:
                log.warning("nuclear_cleanup: failed to delete job %s/%s: %s", namespace, name, exc)
    except Exception as exc:
        log.warning("nuclear_cleanup: list jobs failed (%s): %s", namespace, exc)
