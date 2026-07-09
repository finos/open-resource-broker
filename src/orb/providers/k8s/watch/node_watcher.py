"""Kubernetes node watcher.

Streams ``CoreV1Api.list_node`` events in a background worker thread
and translates each event into a
:class:`~orb.providers.k8s.watch.node_state_cache.K8sNodeState` upserted
into the supplied
:class:`~orb.providers.k8s.watch.node_state_cache.K8sNodeStateCache`.

Nodes are cluster-scoped resources — ``list_node`` does not accept a
``namespace`` parameter.  This means the watcher requires a
*cluster-scoped* RBAC grant (``ClusterRole`` with ``nodes``
``get/list/watch``) even when ORB otherwise manages pods in a single
namespace.  See ``docs/root/providers/k8s/rbac.yaml`` for the full
manifest; the relevant rule is:

.. code-block:: yaml

    - apiGroups: [""]
      resources: ["nodes"]
      verbs: ["get", "list", "watch"]

This rule must be in a ``ClusterRole`` (not a namespaced ``Role``)
because ``Node`` objects exist outside any namespace.

Resilience contract mirrors :class:`~orb.providers.k8s.watch.watcher.K8sWatcher`:

* **410 Gone**          — drop the in-flight ``resource_version`` and
  restart the stream from ``None`` so the apiserver re-lists from the
  latest snapshot.  The retry budget is reset because a 410 is an
  expected bookmark expiry, not a fault.
* **Other ApiException** — exponential backoff (1 s, 2 s, 4 s … capped
  at ``max_backoff_seconds``) then retry.
* **Generic exceptions** — same exponential backoff.
* **Stop signal**       — :attr:`_stop_event` (:class:`threading.Event`)
  is set by :meth:`stop`; the watcher's inner ``Watch.stop()`` is called
  so the blocking ``readline()`` in the SDK returns promptly.  The worker
  thread joins within the timeout passed to :meth:`stop`.

The watcher runs entirely on a worker thread (never on the asyncio event
loop) and uses only :class:`threading.Event` for signalling — matching
the fix that replaced asyncio.Event in
:class:`~orb.providers.k8s.watch.watcher.K8sWatcher` to avoid asyncio
contract violations from worker-thread code.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.watch.node_state_cache import K8sNodeState, K8sNodeStateCache

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Node
    from kubernetes.watch import Watch

# Re-LIST timeout forwarded to the apiserver per session.  Without this
# the stream can stall behind a dead TCP connection indefinitely.
_DEFAULT_WATCH_TIMEOUT_SECONDS = 300

# Initial and cap for exponential backoff on non-410 errors.
_DEFAULT_BASE_BACKOFF_SECONDS = 1.0
_DEFAULT_MAX_BACKOFF_SECONDS = 60.0

# How long :meth:`stop` waits for the worker thread to exit.
_JOIN_TIMEOUT_SECONDS = 10.0


# Factory callable type for ``kubernetes.watch.Watch``.
WatchFactory = Callable[[], "Watch"]


def _default_watch_factory() -> Watch:
    """Default factory: returns a fresh ``kubernetes.watch.Watch``."""
    from kubernetes.watch import Watch as _Watch

    return _Watch()


@injectable
class K8sNodeWatcher:
    """Watch all nodes in the cluster and populate a :class:`K8sNodeStateCache`.

    Unlike the pod watcher, node watches are always cluster-scoped — no
    namespace parameter is accepted by ``list_node``.  One instance
    suffices for the whole cluster regardless of how many namespaces the
    provider monitors.

    The watcher runs on a background :class:`threading.Thread` (not
    inside the asyncio event loop) so it never competes with or blocks
    the async provisioning paths.

    Args:
        kubernetes_client: The provider's API facade; the watcher uses
            ``core_v1.list_node`` as the underlying watch target.
        cache: The shared :class:`K8sNodeStateCache` to upsert into.
        logger: Logging port.
        watch_timeout_seconds: ``timeout_seconds`` forwarded to the
            apiserver; clean expiry triggers a reconnect.
        base_backoff_seconds: Initial backoff after a non-410 failure.
        max_backoff_seconds: Cap on the backoff schedule.
        watch_factory: Factory returning a fresh
            ``kubernetes.watch.Watch``.  Tests inject a stub; production
            uses the default which constructs
            ``kubernetes.watch.Watch()``.
    """

    def __init__(
        self,
        kubernetes_client: K8sClient,
        cache: K8sNodeStateCache,
        logger: LoggingPort,
        *,
        watch_timeout_seconds: int = _DEFAULT_WATCH_TIMEOUT_SECONDS,
        base_backoff_seconds: float = _DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: float = _DEFAULT_MAX_BACKOFF_SECONDS,
        watch_factory: WatchFactory = _default_watch_factory,
    ) -> None:
        self._client = kubernetes_client
        self._cache = cache
        self._logger = logger
        self._watch_timeout_seconds = watch_timeout_seconds
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._watch_factory = watch_factory

        self._stop_event = threading.Event()
        self._active_watch: Optional[Watch] = None
        self._thread: Optional[threading.Thread] = None

        # Diagnostics — updated by the worker thread.
        self._last_event_at: float = 0.0
        self._last_error: Optional[str] = None
        self._consecutive_failures: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def last_event_at(self) -> float:
        """Monotonic timestamp of the last event observed (0.0 if none)."""
        return self._last_event_at

    @property
    def last_error(self) -> Optional[str]:
        """Last failure message recorded by the watch loop (None on success)."""
        return self._last_error

    def is_running(self) -> bool:
        """Return ``True`` while the worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Spawn the watch worker thread.

        Idempotent — subsequent calls while already running are ignored.
        After :meth:`stop` the watcher can be re-started.
        """
        if self.is_running():
            return
        self._stop_event = threading.Event()
        self._consecutive_failures = 0
        self._thread = threading.Thread(
            target=self._run,
            name="k8s-node-watcher",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Kubernetes node watcher started")

    def stop(self, *, timeout: float = _JOIN_TIMEOUT_SECONDS) -> None:
        """Signal the worker thread to stop and wait for it to exit.

        Safe to call multiple times.  Closes the inner ``Watch`` so the
        blocking stream returns promptly, then joins the thread.

        Args:
            timeout: Maximum seconds to wait for the thread to exit
                before returning (the thread may still be alive after
                this call if it is stuck in I/O).
        """
        self._stop_event.set()
        watch = self._active_watch
        if watch is not None:
            try:
                stop_fn = getattr(watch, "stop", None)
                if callable(stop_fn):
                    stop_fn()
            except Exception as exc:  # pragma: no cover — defensive
                self._logger.debug("Watch.stop raised (ignored): %s", exc, exc_info=True)

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._logger.warning(
                    "Kubernetes node watcher thread did not exit within %.1fs", timeout
                )
        self._thread = None
        self._logger.info("Kubernetes node watcher stopped")

    # ------------------------------------------------------------------
    # Worker thread entry point
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main watch loop — reconnect with backoff until :meth:`stop` is called."""
        resource_version: Optional[str] = None
        while not self._stop_event.is_set():
            try:
                resource_version = self._run_one_session(resource_version)
                # Clean end-of-stream (``timeout_seconds`` expiry).
                # Loop straight back into a fresh session.
                self._consecutive_failures = 0
                self._last_error = None
            except _ResourceTooOld:
                # 410 Gone — drop resource_version and re-LIST.
                self._logger.info(
                    "Kubernetes node watch returned 410 Gone; restarting from rv=None"
                )
                resource_version = None
                self._consecutive_failures = 0
                self._last_error = None
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = str(exc)
                backoff = self._backoff_for_attempt(self._consecutive_failures)
                self._logger.warning(
                    "Kubernetes node watch failed (attempt=%s); backing off %.1fs: %s",
                    self._consecutive_failures,
                    backoff,
                    exc,
                )
                self._sleep_or_stop(backoff)
        self._logger.debug("Kubernetes node watch loop exited")

    def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep for ``seconds`` but wake up early if :meth:`stop` was called."""
        self._stop_event.wait(timeout=seconds)

    def _backoff_for_attempt(self, attempt: int) -> float:
        """Compute exponential backoff for the n-th consecutive failure.

        ``attempt`` is 1-based; doubles each time, capped at
        :attr:`_max_backoff_seconds`.
        """
        attempt = max(attempt, 1)
        delay = self._base_backoff_seconds * (2 ** (attempt - 1))
        return min(delay, self._max_backoff_seconds)

    # ------------------------------------------------------------------
    # Single watch session
    # ------------------------------------------------------------------

    def _run_one_session(self, resource_version: Optional[str]) -> Optional[str]:
        """Open a single watch session and consume events until it ends.

        Returns the resource_version observed at the end of the session
        so the outer loop can resume from the same point.

        Raises :class:`_ResourceTooOld` on 410 so the outer loop drops
        the resource_version and re-LISTs.
        """
        watch = self._watch_factory()
        self._active_watch = watch
        try:
            kwargs: dict[str, Any] = {
                "timeout_seconds": self._watch_timeout_seconds,
            }
            if resource_version is not None:
                kwargs["resource_version"] = resource_version

            stream = watch.stream(self._client.core_v1.list_node, **kwargs)
            for event in stream:
                if self._stop_event.is_set():
                    break
                if not isinstance(event, dict):
                    continue
                try:
                    self._handle_event(event)
                except Exception as exc:  # pragma: no cover — defensive
                    self._logger.warning(
                        "Failed to handle node watch event: %s",
                        exc,
                        exc_info=True,
                    )
            return getattr(watch, "resource_version", resource_version)
        except Exception as exc:
            if self._is_resource_version_too_old(exc):
                raise _ResourceTooOld() from exc
            raise
        finally:
            self._active_watch = None

    @staticmethod
    def _is_resource_version_too_old(exc: BaseException) -> bool:
        """Return ``True`` when ``exc`` is a 410 ``ApiException``."""
        try:
            from kubernetes.client.exceptions import ApiException
        except ImportError:  # pragma: no cover — extra not installed
            return False
        if not isinstance(exc, ApiException):
            return False
        return getattr(exc, "status", None) == 410

    # ------------------------------------------------------------------
    # Event translation
    # ------------------------------------------------------------------

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Translate a single watch event into a cache mutation."""
        self._last_event_at = time.monotonic()
        event_type = event.get("type")
        node = event.get("object")
        if node is None:
            return
        metadata = getattr(node, "metadata", None)
        if metadata is None:
            return
        node_name = getattr(metadata, "name", None)
        if not node_name:
            return

        if event_type == "DELETED":
            self._cache.delete(node_name)
            self._logger.debug("Node watcher: deleted node %s from cache", node_name)
            return

        state = self._node_to_state(node)
        self._cache.upsert(state)
        self._logger.debug(
            "Node watcher: upserted node %s (ready=%s, instance_type=%s, zone=%s)",
            node_name,
            state.ready,
            state.instance_type,
            state.zone,
        )

    @staticmethod
    def _extract_conditions(status: Any) -> list[dict]:
        """Extract a list of condition dicts from ``node.status.conditions``.

        Each returned dict contains ``type``, ``status``, ``reason``, and
        ``lastTransitionTime`` — the four fields relevant to node health
        monitoring.  Missing fields default to ``None``.
        """
        raw_conditions = (
            list(getattr(status, "conditions", None) or []) if status is not None else []
        )
        result: list[dict] = []
        for cond in raw_conditions:
            result.append(
                {
                    "type": getattr(cond, "type", None),
                    "status": getattr(cond, "status", None),
                    "reason": getattr(cond, "reason", None),
                    "lastTransitionTime": str(getattr(cond, "last_transition_time", None) or ""),
                }
            )
        return result

    @staticmethod
    def _is_ready(conditions: list[dict]) -> bool:
        """Return ``True`` when the ``Ready`` condition has ``status="True"``."""
        for cond in conditions:
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                return True
        return False

    def _node_to_state(self, node: V1Node) -> K8sNodeState:
        """Convert a ``V1Node`` event payload into a :class:`K8sNodeState`."""
        metadata = getattr(node, "metadata", None)
        status = getattr(node, "status", None)

        name: str = getattr(metadata, "name", "") if metadata is not None else ""
        labels: dict[str, str] = (
            dict(getattr(metadata, "labels", None) or {}) if metadata is not None else {}
        )

        # Instance type — prefer the stable label, fall back to the beta alias.
        instance_type: Optional[str] = labels.get("node.kubernetes.io/instance-type") or labels.get(
            "beta.kubernetes.io/instance-type"
        )

        # Availability zone — prefer the stable label, fall back to the beta alias.
        zone: Optional[str] = labels.get("topology.kubernetes.io/zone") or labels.get(
            "failure-domain.beta.kubernetes.io/zone"
        )

        # Karpenter / CAS capacity type.
        capacity_type: Optional[str] = labels.get("karpenter.sh/capacity-type")

        # Resource quantities from node.status.capacity and .allocatable.
        capacity = getattr(status, "capacity", None) if status is not None else None
        allocatable = getattr(status, "allocatable", None) if status is not None else None

        cpu_capacity: Optional[str] = None
        memory_capacity: Optional[str] = None
        cpu_allocatable: Optional[str] = None
        memory_allocatable: Optional[str] = None

        if capacity is not None:
            if isinstance(capacity, dict):
                cpu_capacity = capacity.get("cpu")
                memory_capacity = capacity.get("memory")
            else:
                cpu_capacity = getattr(capacity, "cpu", None)
                memory_capacity = getattr(capacity, "memory", None)

        if allocatable is not None:
            if isinstance(allocatable, dict):
                cpu_allocatable = allocatable.get("cpu")
                memory_allocatable = allocatable.get("memory")
            else:
                cpu_allocatable = getattr(allocatable, "cpu", None)
                memory_allocatable = getattr(allocatable, "memory", None)

        conditions = self._extract_conditions(status)
        ready = self._is_ready(conditions)

        return K8sNodeState(
            name=name,
            instance_type=instance_type or None,
            zone=zone or None,
            capacity_type=capacity_type or None,
            cpu_capacity=str(cpu_capacity) if cpu_capacity is not None else None,
            memory_capacity=str(memory_capacity) if memory_capacity is not None else None,
            cpu_allocatable=str(cpu_allocatable) if cpu_allocatable is not None else None,
            memory_allocatable=str(memory_allocatable) if memory_allocatable is not None else None,
            conditions=conditions,
            ready=ready,
        )


class _ResourceTooOld(Exception):
    """Internal sentinel for ``ApiException(status=410)`` — never escapes the module."""


__all__ = ["K8sNodeWatcher", "WatchFactory"]
