"""Kubernetes Events API watcher — node-disruption visibility.

Streams ``CoreV1Api.list_event_for_all_namespaces`` (filtered to
``involvedObject.kind=Node``) in a background worker thread and
translates Karpenter node-disruption events into a small in-process
events cache so status reads can surface them.

Legacy behaviour (``src/orb/k8s_legacy/impl/watchers/kube_watcher.py``)
---
The legacy watcher subscribed to the k8s Events API with
``field_selector="involvedObject.kind=Node"`` and parsed two specific
Karpenter disruption messages:

* ``"Disrupting Node: Underutilized/Delete"`` — node is underutilised,
  Karpenter is evicting it.
* ``"Disrupting Node: Empty/Delete"`` — node is empty, Karpenter is
  reclaiming it.

Any recognised event was stored in a flat event buffer keyed by
``<node-name>::<node-uid>`` with the UTC timestamp of the disruption.

Modern equivalent
---
This watcher mirrors the legacy's field selector and message parsing,
adapted to the modern async architecture:

* Runs on a **worker thread** (not the asyncio event loop) using
  :class:`threading.Thread` — the same pattern as
  :class:`~orb.providers.k8s.watch.node_watcher.K8sNodeWatcher`.
* Maintains a small :class:`K8sNodeEventsCache` keyed by node name so
  status-read callers can inspect the most recent disruption reason and
  message.
* Applies the same resilience contract as the node watcher: 410 Gone
  -> reset resource_version; other failures -> exponential backoff.
* Cardinality-guarded: we never store the full event object -- only
  ``reason``, ``message``, ``event_type`` (``Normal`` / ``Warning``),
  and the raw ``first_timestamp`` / ``creation_timestamp`` string.

RBAC note
---
The Events watcher requires a ``list/watch`` verb on ``v1.events``
(the core API group).  This must be added to the ``ClusterRole``
(or namespace-scoped ``Role`` when watching a single namespace):

.. code-block:: yaml

    - apiGroups: [""]
      resources: ["events"]
      verbs: ["get", "list", "watch"]

A namespace-scoped ``Role`` suffices when the provider watches a
single namespace (the default); a ``ClusterRole`` is required when
``namespaces=["*"]`` is configured.

Opt-in via ``K8sProviderConfig.events_watch_enabled = True``
(default ``False`` -- the RBAC grant may not exist in every cluster).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.di.injectable import injectable
from orb.providers.k8s.infrastructure.k8s_client import K8sClient

if TYPE_CHECKING:  # pragma: no cover -- type-checking only
    from kubernetes.watch import Watch


# ---------------------------------------------------------------------------
# Karpenter disruption reason constants
# (verbatim from the legacy parser in k8sutils.parse_node_event)
# ---------------------------------------------------------------------------

# Karpenter v0.x message strings (exact-match).
KARPENTER_UNDERUTILIZED_DELETE = "Disrupting Node: Underutilized/Delete"
KARPENTER_EMPTY_DELETE = "Disrupting Node: Empty/Delete"

# Karpenter v1.x (>= 1.0, Oct 2024) uses reason="Disrupted" and emits
# messages that start with "Disrupting node:".  The v1 messages encode
# the disruption type differently from v0.x, but all share the same
# reason string.  We normalise each v1 cause to a canonical v0-style
# label so callers see a consistent value regardless of Karpenter version.
KARPENTER_V1_REASON = "Disrupted"

# Karpenter v1 message prefixes and their canonical reason labels.
# Ordered longest-first so the most-specific prefix matches first.
_KARPENTER_V1_MESSAGE_MAP: list[tuple[str, str]] = [
    ("Disrupting node: Underutilized/Delete", "Underutilized/Delete"),
    ("Disrupting node: Empty/Delete", "Empty/Delete"),
    ("Disrupting node: Drift/Delete", "Drift/Delete"),
    ("Disrupting node: Consolidat", "Consolidation/Delete"),  # prefix covers variants
    ("Disrupting node:", "Disrupted"),  # generic v1 fallback
]

# Default TTL for entries in :class:`K8sNodeEventsCache`.  After a node
# is scale-in terminated its name may be reused; entries older than this
# window are evicted on read/insert so stale disruption signals from a
# previous node with the same name do not bleed through.
_DEFAULT_NODE_EVENT_TTL_SECONDS: int = 3600  # 1 hour

# ---------------------------------------------------------------------------
# Cache data structures
# ---------------------------------------------------------------------------


@dataclass
class K8sNodeDisruptionEvent:
    """A single node-disruption event surfaced from the k8s Events API.

    Attributes:
        node_name: Name of the involved node.
        reason: ``event.reason`` field (e.g. ``"Disrupting"``).
        message: ``event.message`` field (e.g.
            ``"Disrupting Node: Underutilized/Delete"``).
        event_type: ``event.type`` field (``"Normal"`` or ``"Warning"``).
        karpenter_reason: Canonical disruption reason extracted from the
            message: one of ``"Underutilized/Delete"``,
            ``"Empty/Delete"``, or ``None`` when neither pattern matches.
        timestamp_str: String representation of the event timestamp
            taken from ``event.metadata.creation_timestamp``.
        observed_at: UTC timestamp of when this watcher observed the event.
    """

    node_name: str
    reason: Optional[str] = None
    message: Optional[str] = None
    event_type: Optional[str] = None
    karpenter_reason: Optional[str] = None
    timestamp_str: Optional[str] = None
    observed_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )


class K8sNodeEventsCache:
    """Thread-safe cache of the most-recent disruption event per node name.

    Only one :class:`K8sNodeDisruptionEvent` is stored per node -- the
    most recently observed one.  This is intentionally minimal: the
    purpose is to surface *whether* a node is undergoing Karpenter
    disruption and the reason, not to record a full audit trail.

    Readers (status-check paths) run on the asyncio thread while the
    watcher writes from a background thread -- a :class:`threading.RLock`
    guards every mutation and read.

    TTL eviction
    ------------
    To prevent stale disruption signals from a previous node that had the
    same name (common after cluster scale-in), entries older than
    ``ttl_seconds`` are silently evicted on :meth:`get` and pruned on
    :meth:`upsert`.  The default TTL is 1 hour
    (``_DEFAULT_NODE_EVENT_TTL_SECONDS``).
    """

    def __init__(self, ttl_seconds: int = _DEFAULT_NODE_EVENT_TTL_SECONDS) -> None:
        self._lock = threading.RLock()
        self._events: dict[str, K8sNodeDisruptionEvent] = {}
        self._ttl = timedelta(seconds=max(ttl_seconds, 0))

    def _is_expired(self, event: K8sNodeDisruptionEvent) -> bool:
        """Return ``True`` when *event* is older than the configured TTL."""
        if self._ttl.total_seconds() <= 0:
            return False
        age = datetime.now(tz=timezone.utc) - event.observed_at
        return age > self._ttl

    def upsert(self, event: K8sNodeDisruptionEvent) -> None:
        """Store or replace the most-recent disruption event for a node.

        Also evicts any other entries that have exceeded the TTL so the
        dict does not grow unboundedly on long-running clusters with high
        node churn.
        """
        with self._lock:
            self._events[event.node_name] = event
            # Prune expired entries on every write to bound the dict size.
            # We do this under the same lock to avoid a separate
            # background-sweep thread.  The linear scan is cheap because
            # the number of distinct node names is small (O(fleet size)).
            expired = [k for k, v in self._events.items() if self._is_expired(v)]
            for k in expired:
                del self._events[k]

    def get(self, node_name: str) -> Optional[K8sNodeDisruptionEvent]:
        """Return the latest disruption event for *node_name*, or ``None``.

        Returns ``None`` (not the event) when the entry has exceeded the
        TTL, and evicts it from the cache at the same time.
        """
        with self._lock:
            event = self._events.get(node_name)
            if event is None:
                return None
            if self._is_expired(event):
                del self._events[node_name]
                return None
            return event

    def all(self) -> list[K8sNodeDisruptionEvent]:
        """Return a snapshot of all non-expired cached disruption events."""
        with self._lock:
            now = datetime.now(tz=timezone.utc)
            return [v for v in self._events.values() if now - v.observed_at <= self._ttl]

    def clear(self) -> None:
        """Evict all cached entries.  Called on watcher restart."""
        with self._lock:
            self._events.clear()

    def size(self) -> int:
        """Return the number of nodes currently tracked (including expired)."""
        with self._lock:
            return len(self._events)


# ---------------------------------------------------------------------------
# Watcher timeouts / backoff constants  (mirror node_watcher.py)
# ---------------------------------------------------------------------------

_DEFAULT_WATCH_TIMEOUT_SECONDS = 300
_DEFAULT_BASE_BACKOFF_SECONDS = 1.0
_DEFAULT_MAX_BACKOFF_SECONDS = 60.0
_JOIN_TIMEOUT_SECONDS = 10.0

# Field selector applied to every watch session.
_NODE_EVENT_FIELD_SELECTOR = "involvedObject.kind=Node"

# Factory callable type for ``kubernetes.watch.Watch``.
WatchFactory = Callable[[], "Watch"]


def _default_watch_factory() -> Watch:
    """Default factory: returns a fresh ``kubernetes.watch.Watch``."""
    from kubernetes.watch import Watch as _Watch

    return _Watch()


# ---------------------------------------------------------------------------
# Karpenter message parser
# ---------------------------------------------------------------------------


def _parse_karpenter_reason(
    message: Optional[str],
    reason: Optional[str] = None,
) -> Optional[str]:
    """Extract a canonical Karpenter disruption reason from an event.

    Supports both Karpenter v0.x (pre-1.0) and v1.x (>= 1.0, Oct 2024):

    * **v0.x** -- ``reason`` is typically ``"Disrupting"`` and the
      ``message`` is one of the two exact strings
      ``KARPENTER_UNDERUTILIZED_DELETE`` / ``KARPENTER_EMPTY_DELETE``.
      Matched by exact-comparison so legacy parsing is unaffected.

    * **v1.x** -- ``reason`` is ``"Disrupted"`` and ``message`` starts
      with ``"Disrupting node:"`` followed by the disruption type.
      Matched by prefix so new disruption types introduced in future
      Karpenter releases are gracefully caught by the generic fallback.

    Returns one of:

    * ``"Underutilized/Delete"`` -- underutilised node scheduled for deletion.
    * ``"Empty/Delete"`` -- empty node reclaimed by Karpenter.
    * ``"Drift/Delete"`` -- node drifted from desired state (v1 only).
    * ``"Consolidation/Delete"`` -- node consolidated away (v1 only).
    * ``"Disrupted"`` -- v1 disruption with unrecognised type (generic fallback).
    * ``None`` -- the message does not match any known Karpenter pattern.

    Args:
        message: The raw ``event.message`` string.
        reason: The raw ``event.reason`` string.  When ``"Disrupted"``
            (Karpenter v1.x), message prefix matching is used instead of
            exact matching.  Optional for backward compatibility.
    """
    if not message:
        return None

    # --- Karpenter v0.x: exact-match legacy strings ---------------------
    if message == KARPENTER_UNDERUTILIZED_DELETE:
        return "Underutilized/Delete"
    if message == KARPENTER_EMPTY_DELETE:
        return "Empty/Delete"

    # --- Karpenter v1.x: reason="Disrupted" + prefix-match on message ---
    if reason == KARPENTER_V1_REASON:
        for prefix, canonical in _KARPENTER_V1_MESSAGE_MAP:
            if message.startswith(prefix):
                return canonical

    return None


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


@injectable
class K8sEventsWatcher:
    """Watch the k8s Events API for node-disruption events.

    Filters the event stream to ``involvedObject.kind=Node`` (matching
    the legacy ``kube_watcher.py`` field selector) and stores
    Karpenter disruption events in a :class:`K8sNodeEventsCache`.

    The watcher runs on a background :class:`threading.Thread` (not
    inside the asyncio event loop) so it never competes with or blocks
    the async provisioning paths.  Resilience contract mirrors
    :class:`~orb.providers.k8s.watch.node_watcher.K8sNodeWatcher`.

    Args:
        kubernetes_client: The provider's API facade; the watcher uses
            ``core_v1.list_event_for_all_namespaces`` filtered to
            ``involvedObject.kind=Node`` as the underlying watch target.
        cache: The shared :class:`K8sNodeEventsCache` to upsert into.
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
        cache: K8sNodeEventsCache,
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

        # Diagnostics -- updated by the worker thread.
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

        Idempotent -- subsequent calls while already running are ignored.
        After :meth:`stop` the watcher can be re-started.
        """
        if self.is_running():
            return
        self._stop_event = threading.Event()
        self._consecutive_failures = 0
        self._thread = threading.Thread(
            target=self._run,
            name="k8s-events-watcher",
            daemon=True,
        )
        self._thread.start()
        # Note: the strategy layer logs a matching "events watcher started"
        # message when it calls start(); we do not duplicate it here to
        # avoid a confusing double INFO line in the operator logs.

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
            except Exception as exc:  # pragma: no cover -- defensive
                self._logger.debug("Watch.stop raised (ignored): %s", exc, exc_info=True)

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._logger.warning(
                    "Kubernetes events watcher thread did not exit within %.1fs", timeout
                )
        self._thread = None
        self._logger.info("Kubernetes events watcher stopped")

    # ------------------------------------------------------------------
    # Worker thread entry point
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main watch loop -- reconnect with backoff until :meth:`stop` is called."""
        resource_version: Optional[str] = None
        while not self._stop_event.is_set():
            try:
                resource_version = self._run_one_session(resource_version)
                # Clean end-of-stream (``timeout_seconds`` expiry).
                self._consecutive_failures = 0
                self._last_error = None
            except _ResourceTooOld:
                self._logger.info(
                    "Kubernetes events watch returned 410 Gone; restarting from rv=None"
                )
                resource_version = None
                self._consecutive_failures = 0
                self._last_error = None
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = str(exc)
                backoff = self._backoff_for_attempt(self._consecutive_failures)
                self._logger.warning(
                    "Kubernetes events watch failed (attempt=%s); backing off %.1fs: %s",
                    self._consecutive_failures,
                    backoff,
                    exc,
                )
                self._sleep_or_stop(backoff)
        self._logger.debug("Kubernetes events watch loop exited")

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
                "field_selector": _NODE_EVENT_FIELD_SELECTOR,
            }
            if resource_version is not None:
                kwargs["resource_version"] = resource_version

            stream = watch.stream(self._client.core_v1.list_event_for_all_namespaces, **kwargs)
            for event in stream:
                if self._stop_event.is_set():
                    break
                if not isinstance(event, dict):
                    continue
                try:
                    self._handle_event(event)
                except Exception as exc:  # pragma: no cover -- defensive
                    self._logger.warning(
                        "Failed to handle events watch event: %s",
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
        except ImportError:  # pragma: no cover -- extra not installed
            return False
        if not isinstance(exc, ApiException):
            return False
        return getattr(exc, "status", None) == 410

    # ------------------------------------------------------------------
    # Event translation
    # ------------------------------------------------------------------

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Translate a single watch event into a cache mutation.

        Only ``ADDED`` and ``MODIFIED`` events are processed --
        ``DELETED`` events for the Event resource itself are irrelevant
        because we care only about the disruption fact recorded in the
        event, not about k8s garbage-collecting the Event object.
        """
        self._last_event_at = time.monotonic()
        event_type = event.get("type")
        k8s_event = event.get("object")
        if k8s_event is None:
            return

        # Only process create/update events on the k8s Event object.
        if event_type not in ("ADDED", "MODIFIED"):
            return

        metadata = getattr(k8s_event, "metadata", None)
        involved_object = getattr(k8s_event, "involved_object", None)
        if involved_object is None:
            return

        # Belt-and-suspenders: the field selector already filters to
        # ``involvedObject.kind=Node`` but we double-check here in case
        # the apiserver does not honour the filter (older clusters).
        if getattr(involved_object, "kind", None) != "Node":
            return

        node_name = getattr(involved_object, "name", None)
        if not node_name:
            return

        reason: Optional[str] = getattr(k8s_event, "reason", None)
        message: Optional[str] = getattr(k8s_event, "message", None)
        ev_type: Optional[str] = getattr(k8s_event, "type", None)

        # Timestamp: prefer first_timestamp, fall back to creation_timestamp.
        ts_raw = getattr(k8s_event, "first_timestamp", None)
        if ts_raw is None and metadata is not None:
            ts_raw = getattr(metadata, "creation_timestamp", None)
        timestamp_str = str(ts_raw) if ts_raw is not None else None

        karpenter_reason = _parse_karpenter_reason(message, reason=reason)

        disruption_event = K8sNodeDisruptionEvent(
            node_name=node_name,
            reason=reason,
            message=message,
            event_type=ev_type,
            karpenter_reason=karpenter_reason,
            timestamp_str=timestamp_str,
        )
        self._cache.upsert(disruption_event)

        if karpenter_reason is not None:
            self._logger.info(
                "Kubernetes events watcher: Karpenter node disruption detected "
                "(node=%s, reason=%s, karpenter_reason=%s)",
                node_name,
                reason,
                karpenter_reason,
            )
        else:
            self._logger.debug(
                "Kubernetes events watcher: node event stored (node=%s, reason=%s, message=%s)",
                node_name,
                reason,
                message,
            )


class _ResourceTooOld(Exception):
    """Internal sentinel for ``ApiException(status=410)`` -- never escapes the module."""


__all__ = [
    "K8sEventsWatcher",
    "K8sNodeEventsCache",
    "K8sNodeDisruptionEvent",
    "WatchFactory",
    "_parse_karpenter_reason",
    "KARPENTER_UNDERUTILIZED_DELETE",
    "KARPENTER_EMPTY_DELETE",
    "KARPENTER_V1_REASON",
]
