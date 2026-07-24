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
import os
import time
import uuid
from pathlib import Path
from typing import Generator

import pytest

log = logging.getLogger("k8s.live.conftest")


# ---------------------------------------------------------------------------
# pytest hooks
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply the ``serial`` marker to tests collected in this subtree.

    The hook is scoped to items whose node-id starts with this directory so
    that it does not accidentally mark tests from other directories (e.g.
    unit/, mocked/, contract/) as serial when pytest collects multiple paths
    in a single invocation.  Without the path guard every test in the session
    would receive the serial marker, causing -m "not serial" to deselect the
    entire suite and producing a 0-item run.

    ``pytestmark`` at module level is not picked up by conftest-level
    discovery; the collection hook is the canonical place to bulk-apply
    markers across a directory subtree.

    ``items`` is the FULL collected list across the pytest session, not
    just this subtree — filter to items whose path lives under this
    conftest's directory so we do NOT accidentally mark
    unit/mocked/contract tests as serial when the parent
    ``tests/providers/k8s`` directory is collected as a whole.
    """
    subtree = str(Path(__file__).resolve().parent)
    marker = pytest.mark.serial
    for item in items:
        if str(item.path).startswith(subtree):
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
    import yaml

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

    ok, last_exc = _preflight_probe_cluster(kubeconfig_path, context)
    if not ok:
        pytest.exit(
            f"k8s live pre-flight failed: cannot reach cluster: {last_exc}",
            returncode=1,
        )
    print(f"\nk8s credentials valid (kubeconfig={kubeconfig_path!r}, context={context!r})")

    # Controller-side pre-run reclamation.  A prior run whose worker was
    # hard-killed (SIGKILL / OOM / CI-timeout process-tree kill) before its
    # teardown ran can leave a real Graviton node billing behind a stranded
    # NodePool.  Sweep any leftover test-labelled resources before provisioning
    # so such a node is reclaimed at the start of the next run rather than
    # billing indefinitely.  Guarded to the controller (no ``workerinput``) so
    # it fires exactly once, never per xdist worker.
    if not _is_xdist_worker(session.config):
        _crash_failsafe_sweep(k8s_cfg, reason="pre-run")


def _preflight_probe_cluster(
    kubeconfig_path: str | None, context: str | None
) -> tuple[bool, Exception | None]:
    """Probe the cluster with a bare ``list_namespace`` call, recovering from a stale-token 401.

    A kubeconfig exec-plugin token (``aws eks get-token``, ``gke-gcloud-auth-plugin``,
    ``kubelogin``, ...) can be *present but rejected* by the apiserver (expired,
    or minted for a prior identity/session), yielding a 401.

    Recovery strategy: the pinned kubernetes SDK's ``ExecProvider`` does no token
    caching — it re-execs the plugin on every ``load_kube_config`` call — so a
    genuinely fresh ``load_kube_config`` + brand-new ``CoreV1Api`` re-mint the
    token rather than reusing one held on an already-built client.  On a 401 we
    therefore simply retry the fresh load once.  (No disk-cache manipulation:
    the SDK never reads/writes ``~/.kube/cache/token`` — that is kubectl's
    cache — so clearing it would only perturb a co-located kubectl.)

    Returns:
        ``(True, None)`` on success, ``(False, last_exception)`` on failure.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            _fresh_load_and_list_namespaces(kubeconfig_path, context)
            return True, None
        except Exception as exc:
            last_exc = exc
            if attempt == 1 and "401" in str(exc):
                continue
            break
    return False, last_exc


def _fresh_load_and_list_namespaces(kubeconfig_path: str | None, context: str | None) -> None:
    """Load kubeconfig fresh and issue a bare ``list_namespace`` via a NEW client.

    A fresh ``load_kube_config`` rebuilds the SDK's ``KubeConfigLoader`` (a new
    ``ExecProvider``) and a newly-constructed ``CoreV1Api`` reads the freshly
    loaded credentials, so the exec plugin's re-minted token is actually used
    rather than a token held on a previously-built client.
    """
    from kubernetes import client as k8s_client_mod

    from orb.providers.k8s.auth.kubeconfig import (
        _force_non_interactive_exec,
        load_kubeconfig,
    )

    # Route through ORB's loader so the exec plugin is minted non-interactively.
    # When the preflight runs from a real login-shell TTY the SDK would
    # otherwise run the credential plugin interactively and fail to attach the
    # bearer token (empty Authorization header -> 401); the guard forces the
    # non-interactive branch.  The list call is wrapped in the same guard
    # because the lazy refresh-on-request hook re-execs the plugin and keys off
    # isatty() too.
    load_kubeconfig(config_file=kubeconfig_path, context=context)
    core_v1 = k8s_client_mod.CoreV1Api()
    with _force_non_interactive_exec():
        core_v1.list_namespace(limit=1)


# ---------------------------------------------------------------------------
# Crash-proof label-based failsafe
# ---------------------------------------------------------------------------
#
# The cross-worker NodePool/namespace coordination relies on a refcount file
# that only reaches zero (triggering delete) if every worker runs its teardown.
# A hard-killed worker (SIGKILL / OOM-killer / CI-timeout process-tree kill /
# segfault) strands its +1 in the counter forever, so the refcount teardown
# never fires and a launched arm64 (Graviton) node keeps billing.
#
# The sweep below is the failsafe net: it deletes resources purely by their
# ``orb.io/test`` / ``orb.io/managed`` labels, INDEPENDENT of the refcount, so
# a leak survives a worker crash for at most the span of the run.  It runs on
# the xdist CONTROLLER (which outlives crashed workers) at both session start
# (reclaim a prior run's leak) and session finish (reclaim this run's leak).


def _is_xdist_worker(config: pytest.Config) -> bool:
    """Return True when running inside an xdist worker rather than the controller.

    xdist injects a ``workerinput`` attribute onto the config only in worker
    processes; the controller (and a non-xdist run) has none.  Controller-side
    hooks guard on this so they fire exactly once for the whole session instead
    of once per worker.
    """
    return hasattr(config, "workerinput")


def _sweep_test_nodepools(reason: str) -> None:
    """Delete every Karpenter NodePool carrying ``orb.io/test=true``.

    Idempotent and tolerant: a missing NodePool CRD (cluster without Karpenter)
    or a per-item delete failure is logged and swallowed so the sweep never
    raises out of a session hook.  Deleting the pool makes Karpenter reclaim any
    node it launched, so a stranded Graviton node stops billing.
    """
    try:
        from kubernetes import client as k8s_client_mod
    except Exception as exc:  # noqa: BLE001 — SDK absent means nothing to sweep
        log.debug("crash failsafe (%s): kubernetes SDK unavailable: %s", reason, exc)
        return
    custom = k8s_client_mod.CustomObjectsApi()
    try:
        pools = custom.list_cluster_custom_object(
            group=_KARPENTER_GROUP,
            version=_KARPENTER_VERSION,
            plural=_KARPENTER_PLURAL,
            label_selector=f"{_TEST_LABEL}=true",
        )
    except Exception as exc:  # noqa: BLE001 — no Karpenter CRD / list failure
        log.debug("crash failsafe (%s): NodePool list skipped: %s", reason, exc)
        return
    for pool in pools.get("items", []):
        name = (pool.get("metadata") or {}).get("name")
        if not name:
            continue
        _delete_nodepool(custom, name)
        log.info("crash failsafe (%s): swept test NodePool %s", reason, name)


def _sweep_managed_pods(namespace: str, reason: str) -> None:
    """Delete every ``orb.io/managed=true`` pod in ``namespace``.

    The nuclear-cleanup equivalent of the NodePool sweep — reclaims pods left by
    a crashed worker.  Lower stakes (no direct billing) but keeps the shared
    namespace clean for the next run.  Best-effort; never raises.
    """
    try:
        from kubernetes import client as k8s_client_mod
    except Exception as exc:  # noqa: BLE001
        log.debug("crash failsafe (%s): kubernetes SDK unavailable: %s", reason, exc)
        return
    _cleanup_pods(k8s_client_mod.CoreV1Api(), namespace, f"{_MANAGED_LABEL}=true")


def _crash_failsafe_sweep(k8s_cfg: dict, reason: str) -> None:
    """Run the label-based failsafe sweep for NodePools and managed pods.

    Wraps the individual sweeps so a hook can request the whole failsafe in one
    call.  Loads kubeconfig from the provider config first so the sweep targets
    the same cluster the run used; any failure is logged and swallowed because a
    failsafe must never break the session it protects.
    """
    try:
        from kubernetes import config as k8s_config_mod

        k8s_config_mod.load_kube_config(
            config_file=k8s_cfg.get("kubeconfig_path"), context=k8s_cfg.get("context")
        )
    except Exception as exc:  # noqa: BLE001 — sweep is best-effort
        log.debug("crash failsafe (%s): kubeconfig load skipped: %s", reason, exc)
        return
    _sweep_test_nodepools(reason)
    namespace = k8s_cfg.get("namespace") or "default"
    _sweep_managed_pods(str(namespace), reason)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Controller-side failsafe sweep, unconditional and refcount-independent.

    Runs once on the xdist controller (guarded by the ``workerinput`` check) at
    the very end of the session, AFTER every worker — including any that were
    hard-killed mid-run — has exited.  Because it sweeps purely by label rather
    than by the refcount, it reclaims a launched Graviton node whose owning
    worker died before decrementing the counter.  The normal refcount teardown
    remains the fast path; this only mops up what a crash left behind.
    """
    if not _is_k8s_run(session.config):
        return
    if _is_xdist_worker(session.config):
        return
    try:
        orb_config = _load_orb_config()
    except Exception as exc:  # noqa: BLE001 — failsafe must never raise
        log.debug("crash failsafe (post-run): config load skipped: %s", exc)
        return
    _crash_failsafe_sweep(_get_k8s_provider_config(orb_config), reason="post-run")


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
# Per-test isolated namespace
# ---------------------------------------------------------------------------


def _wait_default_sa(core_v1, namespace: str, timeout: float = 30.0) -> None:
    """Block until the ``default`` ServiceAccount exists in ``namespace``.

    A freshly-created namespace has its ``default`` ServiceAccount populated
    asynchronously by the token controller.  Pods that do not name a SA are
    admitted against ``default``; creating one before it exists yields a
    transient ``403 error looking up service account``.  Waiting here keeps
    the isolated-namespace tests deterministic.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            core_v1.read_namespaced_service_account(name="default", namespace=namespace)
            return
        except Exception:  # noqa: BLE001 — any read failure means "not ready yet"
            time.sleep(0.5)


@pytest.fixture
def k8s_isolated_namespace(k8s_core_v1) -> Generator[str, None, None]:
    """Create a throwaway namespace for a single test and delete it on teardown.

    Some scenarios operate on namespace-global objects that cannot be scoped
    to a single test by label:

    * A ``ResourceQuota`` with ``pods: 0`` blocks *every* pod creation in its
      namespace, not just the test's own pods.
    * The orphan garbage collector, run with an empty known-request-id set,
      classifies *every* ``orb.io/managed=true`` pod in the namespace as an
      orphan and deletes it.

    Running such a test in the shared namespace would break any other test
    creating pods at the same time.  Giving each of these tests its own
    namespace makes the whole live suite safe to run in parallel: the
    quota and the GC sweep are confined to a namespace no other test touches.
    Namespace deletion cascades to every resource inside it, so no per-object
    cleanup is required.
    """
    from kubernetes import client as k8s_client_mod

    ns = f"orb-live-iso-{uuid.uuid4().hex[:12]}"
    k8s_core_v1.create_namespace(
        body=k8s_client_mod.V1Namespace(
            metadata=k8s_client_mod.V1ObjectMeta(
                name=ns,
                labels={_MANAGED_LABEL: "true", "orb.io/live-isolated": "true"},
            )
        )
    )
    _wait_default_sa(k8s_core_v1, ns)
    try:
        yield ns
    finally:
        try:
            k8s_core_v1.delete_namespace(name=ns)
        except Exception as exc:  # noqa: BLE001 — teardown is best-effort
            log.warning("isolated-namespace cleanup failed for %s: %s", ns, exc)
        else:
            log.info("deleted isolated namespace %s", ns)


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
# Every test-owned Karpenter NodePool carries this label; the crash failsafe
# sweeps by it so a pool is reclaimed even when the refcount teardown never ran.
_TEST_LABEL = f"{_LABEL_PREFIX}/test"


@pytest.fixture(scope="session", autouse=True)
def nuclear_cleanup(
    request: pytest.FixtureRequest,
    k8s_core_v1,
    k8s_apps_v1,
    k8s_batch_v1,
    k8s_namespace: str,
) -> Generator[None, None, None]:
    """Session-scoped safety net: remove all ORB-labelled resources created
    during the test run.

    The broad ``orb.io/managed=true`` sweep of the shared namespace runs only
    once the *last* xdist worker has finished, coordinated by a file-lock +
    worker refcount in the shared xdist temp dir.  A per-worker sweep would
    delete pods that a still-running worker created — e.g. a fast test's
    worker finishing while another worker's acquire is mid-provision — so the
    sweep must wait until no worker can still be creating pods.  With xdist
    disabled the refcount trivially resolves to a single worker that sweeps on
    exit.  Best-effort — individual failures are logged and never raise.
    """
    from filelock import FileLock

    state = _shared_state_dir(request)
    lock = FileLock(str(state / "nuclear-cleanup.lock"))
    counter = state / "nuclear-cleanup.workers"
    with lock:
        current = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(current + 1))

    yield

    with lock:
        remaining = (int(counter.read_text()) - 1) if counter.exists() else 0
        counter.write_text(str(max(remaining, 0)))
        if remaining > 0:
            # Other workers are still running — deferring the sweep to the
            # last one avoids deleting their in-flight pods.
            return
        counter.unlink(missing_ok=True)

    # Last worker out: broad label-selector sweep of everything carrying
    # ``orb.io/managed=true`` in the configured namespace.  Safe because the
    # label is unique to ORB-owned resources and no worker can still be
    # creating pods at this point.
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


# ---------------------------------------------------------------------------
# Karpenter NodePool provisioning — cross-worker coordinated
# ---------------------------------------------------------------------------
#
# NodePools are cluster-scoped singletons, so under xdist every worker that
# needs one must agree on a single owner that creates it and a single owner
# that deletes it.  A file lock plus a reference-count file in the shared
# xdist temp directory coordinates this: the first worker to enter creates the
# NodePool, each worker increments the counter on setup, and the last worker
# to leave (counter back to zero) deletes it.  A run with xdist disabled
# (``-p no:xdist`` / ``-n0``) has a single process, so the same code path
# simply creates on first use and deletes on teardown.

_KARPENTER_GROUP = "karpenter.sh"
_KARPENTER_VERSION = "v1"
_KARPENTER_PLURAL = "nodepools"
_COLD_NODEPOOL_NAME = "orb-test-karpenter-cold"
_ARM64_NODEPOOL_NAME = "orb-test-karpenter-arm64"
_NODEPOOL_NODECLASS = "ms-default"


def _custom_objects_api(k8s_provider_config: dict):
    """Return a live ``CustomObjectsApi`` bound to the configured cluster."""
    from kubernetes import client as k8s_client_mod, config as k8s_config_mod

    kubeconfig_path = k8s_provider_config.get("kubeconfig_path")
    context = k8s_provider_config.get("context")
    k8s_config_mod.load_kube_config(config_file=kubeconfig_path, context=context)
    return k8s_client_mod.CustomObjectsApi()


def _node_class_name(k8s_provider_config: dict) -> str:
    """Resolve the EC2NodeClass the test NodePools should reference.

    Defaults to the cluster's ``ms-default`` NodeClass and can be overridden
    with ``ORB_K8S_TEST_NODECLASS`` for clusters that name their NodeClass
    differently.
    """
    return os.environ.get("ORB_K8S_TEST_NODECLASS", _NODEPOOL_NODECLASS)


def _cold_nodepool_body(node_class: str) -> dict:
    """Build a NodePool that can never provision a node.

    The ``instance-family`` requirement targets a family Karpenter's cloud
    provider does not offer, so every node claim the pool would create is
    rejected and pods scheduled onto it stay ``Pending`` indefinitely — the
    cold-node-timeout scenario.  No node (and therefore no cost) is ever
    provisioned.
    """
    return {
        "apiVersion": f"{_KARPENTER_GROUP}/{_KARPENTER_VERSION}",
        "kind": "NodePool",
        "metadata": {"name": _COLD_NODEPOOL_NAME, "labels": {"orb.io/test": "true"}},
        "spec": {
            "template": {
                "spec": {
                    "nodeClassRef": {
                        "group": "karpenter.k8s.aws",
                        "kind": "EC2NodeClass",
                        "name": node_class,
                    },
                    "requirements": [
                        {"key": "kubernetes.io/arch", "operator": "In", "values": ["amd64"]},
                        {
                            "key": "karpenter.k8s.aws/instance-family",
                            "operator": "In",
                            "values": ["nonexistent-family-x9"],
                        },
                    ],
                    "expireAfter": "720h",
                }
            },
            "limits": {"cpu": "10"},
        },
    }


def _arm64_nodepool_body(node_class: str) -> dict:
    """Build a NodePool that provisions real arm64 (Graviton) nodes on demand.

    The pool advertises ``kubernetes.io/arch=arm64`` capacity backed by small
    arm64 instances (2 vCPU) so an arm64-pinned pod triggers Karpenter to
    launch a genuine arm64 node.  Deleting the pool on teardown makes Karpenter
    reclaim any node it launched, so no arm64 node is left billing.
    """
    return {
        "apiVersion": f"{_KARPENTER_GROUP}/{_KARPENTER_VERSION}",
        "kind": "NodePool",
        "metadata": {"name": _ARM64_NODEPOOL_NAME, "labels": {"orb.io/test": "true"}},
        "spec": {
            "template": {
                "spec": {
                    "nodeClassRef": {
                        "group": "karpenter.k8s.aws",
                        "kind": "EC2NodeClass",
                        "name": node_class,
                    },
                    "requirements": [
                        {"key": "kubernetes.io/arch", "operator": "In", "values": ["arm64"]},
                        {
                            "key": "karpenter.k8s.aws/instance-category",
                            "operator": "In",
                            "values": ["m", "c", "t"],
                        },
                        {
                            "key": "karpenter.k8s.aws/instance-cpu",
                            "operator": "In",
                            "values": ["2"],
                        },
                        {
                            "key": "karpenter.sh/capacity-type",
                            "operator": "In",
                            "values": ["spot", "on-demand"],
                        },
                    ],
                    # Short TTL as a last-resort billing cap: if BOTH the
                    # refcount teardown AND the controller-side label failsafe
                    # somehow miss this pool, Karpenter still expires the node
                    # within the hour instead of the previous 30-day ceiling.
                    # A test runs in minutes, so 1h leaves ample headroom for
                    # the arm64-pinned pod to schedule and reach Running.
                    "expireAfter": "1h",
                }
            },
            "limits": {"cpu": "20"},
            # Reclaim the node once it is genuinely empty/underutilised.  This
            # only fires when nothing is scheduled on it, so it cannot pull the
            # node out from under the pending arm64 pod during the test — while
            # the pod is bound the node is not empty.  ``consolidateAfter`` adds
            # a short settle delay so a node that has just gone Ready is not
            # reclaimed in the brief window before the pending pod binds.  The
            # previous ``WhenEmpty`` + ``Never`` left an idle stranded node
            # billing forever; this lets Karpenter self-heal a leaked node.
            "disruption": {
                "consolidationPolicy": "WhenEmptyOrUnderutilized",
                "consolidateAfter": "2m",
            },
        },
    }


def _apply_nodepool(custom, body: dict) -> None:
    """Create the NodePool, tolerating a pre-existing one from a prior run."""
    name = body["metadata"]["name"]
    try:
        custom.create_cluster_custom_object(
            group=_KARPENTER_GROUP,
            version=_KARPENTER_VERSION,
            plural=_KARPENTER_PLURAL,
            body=body,
        )
        log.info("created Karpenter NodePool %s", name)
    except Exception as exc:
        if getattr(exc, "status", None) == 409:
            log.info("Karpenter NodePool %s already present; reusing", name)
            return
        raise


def _delete_nodepool(custom, name: str) -> None:
    """Delete the NodePool; Karpenter reclaims any node it launched."""
    try:
        custom.delete_cluster_custom_object(
            group=_KARPENTER_GROUP,
            version=_KARPENTER_VERSION,
            plural=_KARPENTER_PLURAL,
            name=name,
        )
        log.info("deleted Karpenter NodePool %s", name)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return
        log.warning("failed to delete Karpenter NodePool %s: %s", name, exc)


def _shared_state_dir(request: pytest.FixtureRequest) -> Path:
    """Return a directory shared across all xdist workers for this session.

    ``tmp_path_factory.getbasetemp()`` returns a per-worker directory whose
    parent is shared by every worker of the run (xdist names workers
    ``gw0``/``gw1``/... under a common base), so the parent is the natural
    place for cross-worker coordination files.
    """
    basetemp = request.getfixturevalue("tmp_path_factory").getbasetemp()
    # Under xdist the per-worker basetemp is ``.../popen-gwN``; its parent is
    # shared.  Without xdist getbasetemp() is already the shared root.
    shared = basetemp.parent if basetemp.name.startswith("popen-gw") else basetemp
    d = shared / "orb-k8s-live-nodepools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _coordinated_nodepool(
    request: pytest.FixtureRequest,
    k8s_provider_config: dict,
    name: str,
    body_builder,
) -> Generator[str, None, None]:
    """Create a shared NodePool coordinated across xdist workers via a file lock.

    The first worker to acquire the lock and find a zero refcount creates the
    NodePool; each worker increments the refcount on setup.  On teardown the
    refcount is decremented and the worker that brings it back to zero deletes
    the NodePool.  A ``filelock.FileLock`` serialises the read-modify-write of
    the refcount file so no two workers race the create/delete decision.

    This refcount is the fast/normal path only.  A hard-killed worker (SIGKILL,
    OOM, CI-timeout process-tree kill) — or a worker restarted by
    ``--max-worker-restart`` re-running setup — leaves its +1 stranded in the
    counter, so the count may never reach zero and this teardown may never
    delete the pool.  The controller-side label failsafe (see
    ``pytest_sessionfinish`` / the ``pytest_sessionstart`` pre-run sweep) covers
    both cases by deleting every ``orb.io/test=true`` NodePool regardless of the
    counter, so a stranded Graviton node is always reclaimed.
    """
    from filelock import FileLock

    state = _shared_state_dir(request)
    lock = FileLock(str(state / f"{name}.lock"))
    counter = state / f"{name}.count"
    node_class = _node_class_name(k8s_provider_config)

    with lock:
        current = int(counter.read_text()) if counter.exists() else 0
        if current == 0:
            custom = _custom_objects_api(k8s_provider_config)
            _apply_nodepool(custom, body_builder(node_class))
        counter.write_text(str(current + 1))

    try:
        yield name
    finally:
        with lock:
            remaining = (int(counter.read_text()) - 1) if counter.exists() else 0
            counter.write_text(str(max(remaining, 0)))
            if remaining <= 0:
                custom = _custom_objects_api(k8s_provider_config)
                _delete_nodepool(custom, name)
                counter.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def cold_karpenter_nodepool(
    request: pytest.FixtureRequest, k8s_provider_config: dict
) -> Generator[str, None, None]:
    """Provision the unschedulable cold-node NodePool for the whole session.

    Yields the NodePool name.  The pool cannot provision a node (it targets a
    non-existent instance family), so pods pinned to it stay ``Pending`` — the
    exact cold-node-timeout condition the T22 tests exercise.  No node is ever
    launched, so the fixture is free.

    Session-scoped so the pool is created once per worker (coordinated across
    xdist workers by a file-lock + refcount) and persists for the whole run —
    a per-test scope would let one worker delete the pool while another
    worker's pod is still pinned to it.
    """
    yield from _coordinated_nodepool(
        request, k8s_provider_config, _COLD_NODEPOOL_NAME, _cold_nodepool_body
    )


@pytest.fixture(scope="session")
def arm64_karpenter_nodepool(
    request: pytest.FixtureRequest, k8s_provider_config: dict
) -> Generator[str, None, None]:
    """Provision the arm64 NodePool so arm64-pinned pods get a real Graviton node.

    Yields the NodePool name.  Creating an arm64-affinity pod while this pool
    exists makes Karpenter launch a genuine, briefly-billed arm64 node.  At
    session end the pool is deleted and Karpenter reclaims any node it
    launched, so no arm64 node is left running.

    Session-scoped so the pool is created once per worker (coordinated across
    xdist workers by a file-lock + refcount) and stays up for the whole run;
    a per-test scope would tear the pool down between the arm64 tests, leaving
    a concurrently-pending arm64 pod on another worker unschedulable.
    """
    yield from _coordinated_nodepool(
        request, k8s_provider_config, _ARM64_NODEPOOL_NAME, _arm64_nodepool_body
    )


# ---------------------------------------------------------------------------
# ORB REST server — real subprocess, per-worker isolated
# ---------------------------------------------------------------------------


def _pick_free_port() -> int:
    """Claim an OS-assigned ephemeral port on loopback.

    Binds to port 0, reads the assigned port, and releases it.  Each xdist
    worker calls this independently so their servers never collide on a port.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _OrbRestServer:
    """Launch ``orb server start --foreground --api-only`` as a real subprocess.

    Each instance binds its own free loopback port and runs against an isolated
    ``ORB_WORK_DIR`` so the PID lock, request database, and loopback-admin
    token file never collide with a parallel worker's server.  The server is
    launched under the default scheduler so its request/machine JSON matches
    the snake_case shape the Go SDK and the REST cycle test expect.
    """

    def __init__(self) -> None:
        self.port = _pick_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._proc = None
        self._work_dir: Path | None = None
        self._log_path: Path | None = None
        self._log_fh = None
        self.token: str | None = None

    def start(self, timeout: float = 90.0) -> None:
        import subprocess  # noqa: S404 — launching the ORB CLI under test
        import sys
        import tempfile
        import urllib.request

        self._work_dir = Path(tempfile.mkdtemp(prefix=f"orb-live-rest-{self.port}-"))
        (self._work_dir / "server").mkdir(parents=True, exist_ok=True)
        self._log_path = self._work_dir / "server.log"

        env = os.environ.copy()
        env["ORB_WORK_DIR"] = str(self._work_dir)
        # UI build hooks are irrelevant for the API-only server and slow to run.
        env["ORB_SKIP_UI_BUILD"] = "1"

        cmd = [
            sys.executable,
            "-m",
            "orb.run",
            "server",
            "start",
            "--foreground",
            "--api-only",
            "--scheduler",
            "default",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]
        # Keep a reference so the handle is closed deterministically in stop().
        # Popen dup's the fd, so the child keeps writing after the parent closes.
        self._log_fh = open(self._log_path, "w", encoding="utf-8")
        self._proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT, text=True, env=env
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=3) as resp:
                    if resp.status == 200:
                        self.token = self._read_token()
                        log.info("ORB REST server ready on %s", self.base_url)
                        return
            except Exception:
                time.sleep(2)

        tail = ""
        try:
            if self._log_path and self._log_path.exists():
                tail = self._log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError as exc:
            # Best-effort diagnostics on the startup-failure path: if the server
            # log can't be read we still raise below with an empty tail rather
            # than masking the original readiness failure.
            log.debug("Could not read server log tail from %s: %s", self._log_path, exc)
        exit_code = self._proc.poll() if self._proc else None
        self.stop()
        raise RuntimeError(
            f"ORB REST server failed to become ready on {self.base_url} within {timeout}s "
            f"(exit_code={exit_code}).\n--- server log tail ---\n{tail}"
        )

    def _read_token(self) -> str | None:
        """Read the loopback-admin bearer token the daemon wrote at start.

        POST routes (``/machines/request``, ``/machines/return``) require the
        operator role; the anonymous fallback resolves to ``viewer`` and would
        get 403.  Sending this token as ``Authorization: Bearer`` authenticates
        the request as admin.
        """
        if self._work_dir is None:
            return None
        token_file = self._work_dir / "server" / "orb-server.token"
        try:
            if token_file.exists():
                return token_file.read_text(encoding="ascii").strip() or None
        except OSError:
            return None
        return None

    def stop(self) -> None:
        import shutil
        import subprocess

        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
        self._proc = None
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except OSError as exc:
                log.debug("Could not close server log handle: %s", exc)
            self._log_fh = None
        if self._work_dir is not None:
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = None


@pytest.fixture(scope="session")
def orb_rest_server() -> Generator[_OrbRestServer, None, None]:
    """Session-scoped real ORB REST server, one per xdist worker.

    Yields a running :class:`_OrbRestServer` exposing ``base_url`` and the
    loopback-admin ``token``.  Launched once per worker and torn down at
    session end.  ``ORB_REST_BASE_URL`` in the environment overrides the
    launch entirely so an operator can point the tests at an already-running
    server.
    """
    override = os.environ.get("ORB_REST_BASE_URL")
    if override:

        class _External:
            base_url = override.rstrip("/")
            token = os.environ.get("ORB_REST_ADMIN_TOKEN")

        log.info("Using external ORB REST server at %s", override)
        yield _External()  # type: ignore[misc]
        return

    server = _OrbRestServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Go SDK CLI — built from the in-repo Go SDK
# ---------------------------------------------------------------------------

# A tiny command-line wrapper over the ORB Go SDK (``sdk/go``).  The SDK ships
# as a library with zero external dependencies, so this wrapper builds offline
# and exercises the same RequestMachines / GetRequestStatus / ReturnMachines
# client methods a Go consumer would call.
_GO_SDK_CLI_MAIN = r"""
// Command-line wrapper over the in-repo ORB Go SDK for live cycle testing.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/finos/open-resource-broker/sdk/go/orb"
)

func fail(err error) {
	fmt.Fprintln(os.Stderr, err.Error())
	os.Exit(1)
}

func newClient(server, token string) *orb.Client {
	opts := []orb.Option{orb.WithBaseURL(server), orb.WithTimeout(60 * time.Second)}
	if token != "" {
		opts = append(opts, orb.WithAuth(orb.WithBearerToken(token)))
	} else {
		opts = append(opts, orb.WithAuth(orb.WithNoAuth()))
	}
	c, err := orb.NewClient(opts...)
	if err != nil {
		fail(err)
	}
	return c
}

type multiFlag []string

func (m *multiFlag) String() string { return fmt.Sprint([]string(*m)) }
func (m *multiFlag) Set(v string) error {
	*m = append(*m, v)
	return nil
}

func main() {
	server := flag.String("server", "http://127.0.0.1:8000", "ORB base URL")
	token := flag.String("token", "", "Bearer token")
	flag.Parse()
	args := flag.Args()
	if len(args) == 0 {
		fail(fmt.Errorf("usage: orb-go-cli <acquire|status|release> ..."))
	}
	ctx := context.Background()
	c := newClient(*server, *token)
	enc := json.NewEncoder(os.Stdout)
	switch args[0] {
	case "acquire":
		fs := flag.NewFlagSet("acquire", flag.ExitOnError)
		tpl := fs.String("template", "", "template id")
		count := fs.Int("count", 1, "count")
		_ = fs.Parse(args[1:])
		r, err := c.RequestMachines(ctx, orb.RequestMachinesRequest{TemplateID: *tpl, Count: *count})
		if err != nil {
			fail(err)
		}
		_ = enc.Encode(map[string]any{"request_id": r.RequestID, "message": r.Message})
	case "status":
		fs := flag.NewFlagSet("status", flag.ExitOnError)
		rid := fs.String("request-id", "", "request id")
		_ = fs.Parse(args[1:])
		st, err := c.GetRequestStatus(ctx, *rid, true)
		if err != nil {
			fail(err)
		}
		ids := []string{}
		for _, m := range st.Machines {
			ids = append(ids, m.MachineID)
		}
		_ = enc.Encode(map[string]any{"request_id": st.RequestID, "status": st.Status, "machine_ids": ids})
	case "release":
		fs := flag.NewFlagSet("release", flag.ExitOnError)
		var ids multiFlag
		fs.Var(&ids, "machine-id", "machine id (repeatable)")
		_ = fs.Parse(args[1:])
		if err := c.ReturnMachines(ctx, ids); err != nil {
			fail(err)
		}
		_ = enc.Encode(map[string]any{"status": "released", "machine_ids": []string(ids)})
	default:
		fail(fmt.Errorf("unknown command %q", args[0]))
	}
}
"""


def _go_sdk_dir() -> Path:
    """Absolute path to the in-repo Go SDK module (``sdk/go``)."""
    return Path(__file__).resolve().parents[4] / "sdk" / "go"


@pytest.fixture(scope="session")
def go_sdk_cli(request: pytest.FixtureRequest) -> str | None:
    """Build a CLI wrapper over the in-repo Go SDK and return its binary path.

    Returns ``None`` (so the test skips) when the Go toolchain is unavailable
    or the SDK module is missing.  ``ORB_GO_SDK_BINARY`` overrides the build
    entirely and uses a pre-built binary.

    The wrapper lives in a temporary module with a ``replace`` directive
    pointing at ``sdk/go`` so the repository tree is never modified.
    """
    import shutil
    import subprocess

    override = os.environ.get("ORB_GO_SDK_BINARY")
    if override and shutil.which(override):
        return shutil.which(override)

    go_bin = shutil.which("go")
    if go_bin is None:
        return None
    sdk_dir = _go_sdk_dir()
    if not (sdk_dir / "go.mod").exists():
        return None

    build_dir = Path(
        request.getfixturevalue("tmp_path_factory").mktemp("orb-go-cli", numbered=True)
    )
    (build_dir / "go.mod").write_text(
        "module orbgocli\n\n"
        "go 1.24\n\n"
        "require github.com/finos/open-resource-broker/sdk/go v0.0.0\n\n"
        f"replace github.com/finos/open-resource-broker/sdk/go => {sdk_dir}\n",
        encoding="utf-8",
    )
    (build_dir / "main.go").write_text(_GO_SDK_CLI_MAIN, encoding="utf-8")

    binary = build_dir / "orb-go-cli"
    env = os.environ.copy()
    env["GOFLAGS"] = "-mod=mod"
    try:
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            [go_bin, "build", "-o", str(binary), "."],
            cwd=str(build_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        log.warning("Go SDK CLI build failed: %s", detail)
        return None
    return str(binary)
