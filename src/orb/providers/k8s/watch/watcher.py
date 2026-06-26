"""Asyncio-driven Kubernetes pod watcher.

Wraps the synchronous ``kubernetes.watch.Watch().stream(...)`` generator
in :func:`asyncio.to_thread` so the watch loop coexists with the rest
of the asyncio runtime.  Translates each pod event into a
:class:`~orb.providers.k8s.watch.pod_state_cache.PodState` and
upserts it into the supplied cache.

Resilience contract:

* **410 Gone**          — drop the in-flight ``resource_version`` and
  restart the stream from ``None`` (the apiserver picks the latest).
  The retry budget is reset because a 410 is expected and not a fault.
* **Other ApiException** — exponential backoff (1s, 2s, 4s … capped
  at ``max_backoff_seconds``) and retry.
* **Generic exceptions** — same exponential backoff.
* **Cancellation**       — :meth:`stop` flips a flag and cancels the
  worker task; the inner stream is closed via :meth:`Watch.stop` so
  the blocking ``readline()`` returns promptly.

The watcher is single-namespace; the multi-namespace fan-out lives in
:mod:`~orb.providers.k8s.watch.multi_namespace`.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.pod_state import (
    extract_status_reason,
    is_pod_ready,
    pod_status_string,
)
from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from kubernetes.client import V1Pod
    from kubernetes.watch import Watch


# Re-LIST timeout we hand to the apiserver per watch session.  Without
# this the stream can stall behind a dead TCP connection forever.  The
# kubernetes client treats a clean ``timeout_seconds`` expiry as a
# normal end-of-stream and the outer loop simply re-enters.
_DEFAULT_WATCH_TIMEOUT_SECONDS = 300

# Initial / cap on the exponential-backoff schedule for non-410 errors.
_DEFAULT_BASE_BACKOFF_SECONDS = 1.0
_DEFAULT_MAX_BACKOFF_SECONDS = 60.0


# Factory for ``kubernetes.watch.Watch``.  Wrapped behind a callable so
# tests can inject a stub without monkey-patching the SDK.
WatchFactory = Callable[[], "Watch"]


def _default_watch_factory() -> "Watch":
    """Default factory: returns a fresh ``kubernetes.watch.Watch``."""
    from kubernetes.watch import Watch as _Watch  # noqa: PLC0415

    return _Watch()


@injectable
class K8sWatcher:
    """Watch pods in a single namespace and populate a :class:`PodStateCache`.

    Use one watcher per namespace; the :class:`MultiNamespaceWatcher`
    fans this class out across the configured namespace list.

    Args:
        kubernetes_client: The provider's API facade.  The watcher uses
            ``core_v1.list_namespaced_pod`` (or
            ``core_v1.list_pod_for_all_namespaces`` when ``namespace``
            is ``None``) as the underlying watch target.
        cache: The shared :class:`PodStateCache` to upsert into.
        logger: Logging port.
        namespace: Namespace to watch.  ``None`` runs a cluster-scoped
            watch (used when the provider config is
            ``namespaces=["*"]``).
        label_selector: Selector applied to the watch request; defaults
            to ``"orb.io/managed=true"``.
        request_id_label: Label key carrying the ORB request id; reads
            from the pod's metadata to key the cache.
        watch_timeout_seconds: ``timeout_seconds`` parameter forwarded
            to the apiserver.  The kubernetes client treats expiry as
            a clean end-of-stream and the outer loop reconnects.
        base_backoff_seconds: Initial backoff after a non-410 failure.
        max_backoff_seconds: Cap on the backoff schedule.
        watch_factory: Factory returning a new ``kubernetes.watch.Watch``
            instance.  Tests inject a stub; production uses the default
            which constructs ``kubernetes.watch.Watch()``.
    """

    def __init__(
        self,
        kubernetes_client: K8sClient,
        cache: PodStateCache,
        logger: LoggingPort,
        *,
        namespace: Optional[str],
        label_selector: str = "orb.io/managed=true",
        request_id_label: str = "orb.io/request-id",
        watch_timeout_seconds: int = _DEFAULT_WATCH_TIMEOUT_SECONDS,
        base_backoff_seconds: float = _DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: float = _DEFAULT_MAX_BACKOFF_SECONDS,
        watch_factory: WatchFactory = _default_watch_factory,
    ) -> None:
        self._client = kubernetes_client
        self._cache = cache
        self._logger = logger
        self._namespace = namespace
        self._label_selector = label_selector
        self._request_id_label = request_id_label
        self._watch_timeout_seconds = watch_timeout_seconds
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._watch_factory = watch_factory

        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._active_watch: "Optional[Watch]" = None
        # Tracked for diagnostics / liveness checks.  Updated each time
        # a watch session ends (cleanly or with error) so external
        # callers can tell "stream produced at least one event" from
        # "stream never connected".
        self._last_event_at: float = 0.0
        self._last_error: Optional[str] = None
        self._consecutive_failures: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def namespace(self) -> Optional[str]:
        return self._namespace

    @property
    def last_event_at(self) -> float:
        """Monotonic timestamp of the last event observed (0.0 if none)."""
        return self._last_event_at

    @property
    def last_error(self) -> Optional[str]:
        """Last failure message recorded by the watch loop (None on success)."""
        return self._last_error

    def is_running(self) -> bool:
        """Return ``True`` while the watch task is alive and not cancelled."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Spawn the watch task on the current event loop.

        Idempotent — subsequent calls while the task is running are
        ignored.  After :meth:`stop` the watcher can be re-started.
        """
        if self.is_running():
            return
        self._stop_event = asyncio.Event()
        self._consecutive_failures = 0
        self._task = asyncio.create_task(
            self._run(),
            name=(
                f"k8s-watcher[{self._namespace}]"
                if self._namespace is not None
                else "k8s-watcher[cluster]"
            ),
        )

    async def stop(self) -> None:
        """Stop the watch task and wait for it to settle.

        Safe to call multiple times.  Closes the inner ``Watch`` so the
        blocking stream returns promptly, then awaits the task.
        """
        self._stop_event.set()
        watch = self._active_watch
        if watch is not None:
            try:
                # ``Watch.stop`` does its own socket shutdown — protect
                # against the unlikely case where the SDK raises.
                stop_fn = getattr(watch, "stop", None)
                if callable(stop_fn):
                    stop_fn()
            except Exception as exc:  # pragma: no cover — defensive
                self._logger.debug("Watch.stop raised (ignored): %s", exc, exc_info=True)

        task = self._task
        if task is None:
            return
        if not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # pragma: no cover
                    pass
        self._task = None

    # ------------------------------------------------------------------
    # Inner loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Main watch loop — reconnect with backoff until :meth:`stop` is called."""
        resource_version: Optional[str] = None
        while not self._stop_event.is_set():
            try:
                resource_version = await asyncio.to_thread(
                    self._run_one_session,
                    resource_version,
                )
                # Clean end-of-stream (e.g. ``timeout_seconds`` expiry).
                # Loop straight back into a fresh session.
                self._consecutive_failures = 0
                self._last_error = None
                continue
            except _ResourceTooOld:
                # 410 Gone — drop resource_version and re-LIST.
                self._logger.info(
                    "Kubernetes watch returned 410 Gone (namespace=%s); restarting from rv=None",
                    self._namespace,
                )
                resource_version = None
                self._consecutive_failures = 0
                self._last_error = None
                continue
            except asyncio.CancelledError:  # pragma: no cover — propagated by stop()
                raise
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = str(exc)
                backoff = self._backoff_for_attempt(self._consecutive_failures)
                self._logger.warning(
                    "Kubernetes watch failed (namespace=%s, attempt=%s); backing off %ss: %s",
                    self._namespace,
                    self._consecutive_failures,
                    f"{backoff:.1f}",
                    exc,
                )
                if await self._sleep_or_stop(backoff):
                    break
                continue
        self._logger.debug("Kubernetes watch loop exited (namespace=%s)", self._namespace)

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """Sleep for ``seconds`` but wake up early if :meth:`stop` was called.

        Returns ``True`` if the watcher was asked to stop, ``False``
        otherwise.
        """
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    def _backoff_for_attempt(self, attempt: int) -> float:
        """Compute the backoff delay for the n-th consecutive failure.

        Doubles each time, capped at :attr:`_max_backoff_seconds`.
        ``attempt`` is 1-based.
        """
        attempt = max(attempt, 1)
        delay = self._base_backoff_seconds * (2 ** (attempt - 1))
        return min(delay, self._max_backoff_seconds)

    # ------------------------------------------------------------------
    # Single watch session (runs in worker thread)
    # ------------------------------------------------------------------

    def _run_one_session(self, resource_version: Optional[str]) -> Optional[str]:
        """Open a single watch session and consume events until it ends.

        Returns the resource_version observed at the end of the session
        so the outer loop can resume; raises :class:`_ResourceTooOld` on
        410 so the outer loop knows to drop the rv.

        Executed via :func:`asyncio.to_thread` from :meth:`_run`.
        """
        watch = self._watch_factory()
        self._active_watch = watch
        try:
            api_func, kwargs = self._build_list_call(resource_version)
            kwargs.setdefault("label_selector", self._label_selector)
            kwargs.setdefault("timeout_seconds", self._watch_timeout_seconds)
            stream = watch.stream(api_func, **kwargs)
            for event in stream:
                if self._stop_event.is_set():
                    break
                # ``stream`` is typed to potentially yield log strings
                # for non-pod APIs; for pod watches it always yields
                # dicts.  Guard defensively to keep pyright honest.
                if not isinstance(event, dict):
                    continue
                try:
                    self._handle_event(event)
                except Exception as exc:  # pragma: no cover — defensive
                    self._logger.warning(
                        "Failed to handle pod watch event (namespace=%s): %s",
                        self._namespace,
                        exc,
                        exc_info=True,
                    )
            # Stream ended cleanly (timeout / stop).  Return the
            # resource_version the SDK recorded so the next session
            # resumes correctly.
            return getattr(watch, "resource_version", resource_version)
        except Exception as exc:
            if self._is_resource_version_too_old(exc):
                raise _ResourceTooOld() from exc
            raise
        finally:
            self._active_watch = None

    def _build_list_call(
        self,
        resource_version: Optional[str],
    ) -> tuple[Callable[..., Any], dict[str, Any]]:
        """Pick the list function + kwargs for the current namespace mode."""
        core_v1 = self._client.core_v1
        kwargs: dict[str, Any] = {}
        if resource_version is not None:
            kwargs["resource_version"] = resource_version
        if self._namespace is None:
            return core_v1.list_pod_for_all_namespaces, kwargs
        return core_v1.list_namespaced_pod, {"namespace": self._namespace, **kwargs}

    @staticmethod
    def _is_resource_version_too_old(exc: BaseException) -> bool:
        """Return ``True`` when ``exc`` is a 410 ``ApiException``.

        We avoid an unconditional top-level kubernetes import so the
        architecture test stays happy; this method only runs in the
        worker thread where the SDK is already required.
        """
        try:
            from kubernetes.client.exceptions import ApiException  # noqa: PLC0415
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
        pod = event.get("object")
        if pod is None:
            return
        metadata = getattr(pod, "metadata", None)
        if metadata is None:
            return
        pod_name = getattr(metadata, "name", None)
        if not pod_name:
            return
        labels = dict(getattr(metadata, "labels", None) or {})
        request_id = labels.get(self._request_id_label)
        if not request_id:
            # Pod is managed by ORB (label_selector filter passed) but
            # is missing the request-id label — log once and skip.
            self._logger.debug(
                "Pod %s lacks %s label; skipping cache update",
                pod_name,
                self._request_id_label,
            )
            return

        namespace = getattr(metadata, "namespace", None) or self._namespace or ""

        if event_type == "DELETED":
            # Surface the terminal state before evicting so any read
            # racing the delete sees a final snapshot.
            state = self._pod_to_state(pod, request_id, namespace, deleted=True)
            self._cache.upsert(state)
            self._cache.delete(request_id, pod_name)
            return

        state = self._pod_to_state(pod, request_id, namespace, deleted=False)
        self._cache.upsert(state)

    def _pod_to_state(
        self,
        pod: "V1Pod",
        request_id: str,
        namespace: str,
        *,
        deleted: bool,
    ) -> PodState:
        """Convert a ``V1Pod`` event payload into a :class:`PodState`.

        Mirrors :meth:`K8sPodHandler._instance_dict_for_pod` so
        the cache-fed and list-fed code paths produce identical
        per-instance dicts downstream.
        """
        metadata = getattr(pod, "metadata", None)
        status = getattr(pod, "status", None)
        spec = getattr(pod, "spec", None)

        name = getattr(metadata, "name", "") if metadata is not None else ""
        labels = dict(getattr(metadata, "labels", None) or {}) if metadata is not None else {}
        phase = getattr(status, "phase", None) if status is not None else None
        pod_ip = getattr(status, "pod_ip", None) if status is not None else None
        host_ip = getattr(status, "host_ip", None) if status is not None else None
        node_name = getattr(spec, "node_name", None) if spec is not None else None
        start_time = getattr(status, "start_time", None) if status is not None else None
        conditions = list(getattr(status, "conditions", None) or []) if status is not None else []
        container_statuses = (
            list(getattr(status, "container_statuses", None) or []) if status is not None else []
        )

        ready = is_pod_ready(conditions)
        status_str = pod_status_string(phase, ready)
        reason = extract_status_reason(container_statuses, conditions)

        return PodState(
            request_id=request_id,
            pod_name=name,
            namespace=namespace,
            status=status_str,
            phase=phase,
            ready=ready,
            pod_ip=pod_ip,
            host_ip=host_ip,
            node_name=node_name,
            status_reason=reason,
            start_time=str(start_time) if start_time is not None else None,
            labels=labels,
            deleted=deleted,
        )


class _ResourceTooOld(Exception):
    """Internal sentinel for ``ApiException(status=410)`` — never escapes the module."""


__all__ = ["K8sWatcher", "WatchFactory"]
