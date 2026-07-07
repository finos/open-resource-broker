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
    """Extract the k8s provider config block for live tests.

    Preference order:

    * ``ORB_K8S_LIVE_PROVIDER_NAME`` env var overrides everything and targets a
      specific provider instance by exact name.
    * Config's ``provider.default_provider_instance`` when it names a k8s-type
      provider.
    * First provider of ``type == "k8s"`` in declaration order.

    Returns the provider's ``config`` block, or ``{}`` when no k8s provider is
    configured (tests then skip via kubernetes client import failure).
    """
    import os

    providers = orb_config.get("provider", {}).get("providers", [])
    override = os.environ.get("ORB_K8S_LIVE_PROVIDER_NAME")
    if override:
        for provider in providers:
            if provider.get("type") == "k8s" and provider.get("name") == override:
                return provider.get("config", {})
    default_instance = orb_config.get("provider", {}).get("default_provider_instance")
    if default_instance:
        for provider in providers:
            if provider.get("type") == "k8s" and provider.get("name") == default_instance:
                return provider.get("config", {})
    for provider in providers:
        if provider.get("type") == "k8s":
            return provider.get("config", {})
    return {}


# ---------------------------------------------------------------------------
# Kubeconfig parsing — provider-agnostic auth environment preparation
# ---------------------------------------------------------------------------


def _resolve_kubeconfig_path(kubeconfig_path: str | None) -> str:
    """Resolve the kubeconfig file the kubernetes SDK will load.

    Falls back through the same precedence order the SDK uses so the
    conftest reads exactly the file the runtime does: explicit path >
    ``KUBECONFIG`` env > ``~/.kube/config``.
    """
    import os

    if kubeconfig_path:
        return os.path.expanduser(kubeconfig_path)
    env_path = os.environ.get("KUBECONFIG")
    if env_path:
        return os.path.expanduser(env_path.split(":", 1)[0])
    return os.path.expanduser("~/.kube/config")


def _read_kubeconfig_yaml(path: str) -> dict:
    """Parse the kubeconfig file and return the raw dict.

    Uses ``yaml.safe_load`` — same parser the kubernetes SDK relies on.
    """
    import yaml  # noqa: PLC0415 — optional, only present when k8s extra installed

    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _find_context_user_exec_env(kubeconfig: dict, context_name: str | None) -> list[dict[str, str]]:
    """Return the ``exec.env`` block for the user of *context_name*.

    Walks kubeconfig ``contexts`` → matched user → ``users`` → ``user.exec.env``.
    Returns an empty list when the kubeconfig has no exec-based auth (e.g.
    bearer-token, client-cert, or basic-auth users) since those need no
    env-var injection to work.

    When ``context_name`` is ``None`` we look up ``current-context`` so the
    behaviour matches the SDK's default-context selection.
    """
    context_name = context_name or kubeconfig.get("current-context")
    if not context_name:
        return []
    user_name: str | None = None
    for ctx in kubeconfig.get("contexts") or []:
        if ctx.get("name") == context_name:
            user_name = (ctx.get("context") or {}).get("user")
            break
    if not user_name:
        return []
    for user_entry in kubeconfig.get("users") or []:
        if user_entry.get("name") != user_name:
            continue
        exec_block = (user_entry.get("user") or {}).get("exec") or {}
        env_block = exec_block.get("env") or []
        return [
            {"name": item.get("name"), "value": item.get("value")}
            for item in env_block
            if item.get("name")
        ]
    return []


def _strip_mocked_test_sentinels() -> None:
    """Remove env vars whose value is the fake-test sentinel ``"testing"``.

    Provider-agnostic scrub — clears any cloud-cred sentinel accidentally
    inherited from mocked-test scaffolding.  Values are compared exactly
    so real production credentials are never removed.
    """
    import os

    for key in list(os.environ):
        if os.environ[key] == "testing":
            os.environ.pop(key, None)


def _apply_kubeconfig_exec_env(env_block: list[dict[str, str]]) -> None:
    """Export each ``{name, value}`` entry from the exec ``env:`` block.

    Kubernetes exec plugins (``aws eks get-token``, ``gke-gcloud-auth-plugin``,
    ``kubelogin``, custom OIDC clients, ...) receive parent-process env
    verbatim.  If a cred-managing env var (e.g. ``AWS_ACCESS_KEY_ID``) is
    already present it can beat the kubeconfig's declared ``AWS_PROFILE``,
    silently authenticating as the wrong principal.  Exporting the block
    into ``os.environ`` before ``load_kube_config`` neutralises that
    precedence conflict and mirrors kubectl's own behaviour.

    Auth mechanisms that do not use exec plugins (bearer tokens, client
    certificates, HTTP basic, service-account files) leave the block empty
    so this function is a no-op — which is the correct outcome.
    """
    import os

    for entry in env_block:
        name = entry.get("name")
        value = entry.get("value")
        if name and value is not None:
            os.environ[name] = value


# ---------------------------------------------------------------------------
# pytest_sessionstart — credential pre-flight
# ---------------------------------------------------------------------------


def pytest_sessionstart(session: pytest.Session) -> None:
    """Verify k8s credentials before running any live tests.

    Only executes when ``--run-k8s`` is passed.  Loads the kubeconfig
    specified in the ORB provider config, constructs a bare CoreV1Api
    call, and exits immediately if it fails — so no tests are attempted
    with unusable credentials.

    Provider-agnostic auth preparation runs first:

    * Fake ``"testing"`` credential sentinels inherited from mocked-test
      scaffolding are scrubbed.
    * When the resolved kubeconfig context uses an exec-plugin auth
      (EKS, GKE, AKS, OIDC login, ...) its declared ``env:`` block is
      exported into the process env so precedence conflicts with parent
      env vars are neutralised.  Auth methods that carry credentials
      inline in the kubeconfig (bearer token, client cert, HTTP basic)
      or use an in-cluster service account leave the exec block empty,
      so this step is a no-op for them.
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
    kubeconfig_path = k8s_cfg.get("kubeconfig_path")
    context = k8s_cfg.get("context")

    # Provider-agnostic env preparation — must happen before load_kube_config
    # so the exec plugin subprocess inherits the right env.
    _strip_mocked_test_sentinels()
    try:
        kubeconfig_dict = _read_kubeconfig_yaml(_resolve_kubeconfig_path(kubeconfig_path))
        exec_env = _find_context_user_exec_env(kubeconfig_dict, context)
        _apply_kubeconfig_exec_env(exec_env)
    except FileNotFoundError as exc:
        pytest.exit(
            f"k8s live pre-flight failed: kubeconfig not found: {exc}\n"
            "Set kubeconfig_path in the ORB k8s provider config or export KUBECONFIG.",
            returncode=1,
        )
    except Exception as exc:
        # Kubeconfig parse failure is non-fatal on its own — some
        # kubeconfigs use YAML anchors the SDK handles that a plain
        # safe_load can't.  Fall through to load_kube_config so the SDK's
        # own error surfaces below rather than a misleading one here.
        log.debug("kubeconfig pre-parse skipped: %s", exc)

    # Kubeconfig exec plugins cache tokens on disk with short TTLs.  A
    # cached-but-expired token gives 401 on first call — retry once after
    # clearing the cache so operators don't need to manually prime the
    # exec plugin between runs.
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            from kubernetes import client as k8s_client_mod, config as k8s_config_mod

            k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
            core_v1 = k8s_client_mod.CoreV1Api()
            core_v1.list_namespace(limit=1)
            print(f"\nk8s credentials valid (kubeconfig={kubeconfig_path!r}, context={context!r})")
            return
        except Exception as exc:
            last_exc = exc
            if attempt == 1 and "401" in str(exc):
                _refresh_exec_plugin_cache()
                continue
            break
    pytest.exit(
        f"k8s live pre-flight failed: cannot reach cluster: {last_exc}",
        returncode=1,
    )


def _refresh_exec_plugin_cache() -> None:
    """Force kubeconfig exec plugins to reissue their auth token.

    Deletes the shared kubernetes token cache (``~/.kube/cache/token/``)
    so any exec plugin (``aws eks get-token``, ``gke-gcloud-auth-plugin``,
    ``kubelogin``, ...) re-executes on the next SDK call.  Best-effort:
    missing directories are silently ignored — the subsequent SDK call
    will surface any real auth failure.
    """
    import os
    import shutil
    from pathlib import Path

    token_cache = Path(os.path.expanduser("~/.kube/cache/token"))
    if token_cache.exists():
        shutil.rmtree(token_cache, ignore_errors=True)


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


@pytest.fixture(scope="session")
def request_id_prefix(k8s_live_config: dict) -> str:
    """Config-driven request-id prefix (default 'req-').

    Reads ``naming.prefixes.request`` from the ORB config so tests honour
    whatever prefix operators have configured; the ``RequestId`` domain
    value object validates against ``naming.patterns.request_id``.
    """
    return k8s_live_config.get("naming", {}).get("prefixes", {}).get("request", "req-")


@pytest.fixture
def live_request_id(
    request_id_tracker: list[str], request_id_prefix: str
) -> Generator[str, None, None]:
    """Generate a unique request-id for a single test, track it for cleanup.

    The request-id is registered in ``request_id_tracker`` before the
    test runs so cleanup happens even if the test fails partway through.
    """
    rid = f"{request_id_prefix}{uuid.uuid4()}"
    request_id_tracker.append(rid)
    yield rid


# ---------------------------------------------------------------------------
# Nuclear teardown — session-scoped
# ---------------------------------------------------------------------------

# ORB label constants (mirrors orb.providers.k8s.utilities.pod_spec).
_LABEL_PREFIX = "orb.io"
_MANAGED_LABEL = f"{_LABEL_PREFIX}/managed"


@pytest.fixture(scope="session", autouse=True)
def nuclear_cleanup(
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

    # Broad label-selector sweep: delete everything carrying
    # ``orb.io/managed=true`` in the configured namespace.  This is safe
    # because the label is unique to ORB-owned resources.  We can't rely on
    # request-id tracking here because ``request_id_tracker`` is
    # module-scoped so each module gets a fresh list; a session-scoped
    # fixture cannot compose them.
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
