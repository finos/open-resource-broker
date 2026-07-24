"""Base class for Kubernetes provider handlers.

Mirrors :class:`orb.providers.aws.infrastructure.handlers.base_handler.AWSHandler`
in role: every per-resource-API handler (Pod, Deployment, StatefulSet,
Job) inherits from this class to share client wiring, label-injection,
namespace resolution, and retry helpers.

The base class is intentionally thin — Kubernetes does not need launch
templates, tagging mode toggles, or AMI resolution.  All the heavy
lifting is per-handler.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Generator, Optional, TypeVar

from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.di.injectable import injectable
from orb.infrastructure.resilience import MaxRetriesExceededError
from orb.providers.base.handler import ProviderHandlerBase
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
    stamp_native_workload_body as _stamp_workload_body,
)
from orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver import (
    resolve_namespace as _resolve_ns,
    resolve_namespace_from_provider_data as _resolve_ns_from_provider_data,
)
from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
    instance_dict_for_pod as _instance_dict_for_pod,
    instance_dict_for_state as _instance_dict_for_state,
)
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.resilience.circuit_breaker import K8sCircuitBreaker
from orb.providers.k8s.utilities.pod_spec import request_id_label_selector
from orb.providers.k8s.utilities.pod_spec_audit import audit_pod_spec
from orb.providers.k8s.utilities.pod_state import (
    extract_status_reason,
    is_pod_ready,
    pod_status_string,
)
from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.providers.k8s.exceptions.k8s_exceptions import K8sError
    from orb.providers.k8s.infrastructure.instrumentation.metrics import K8sMetrics
    from orb.providers.k8s.watch.pod_state_cache import PodState, PodStateCache

T = TypeVar("T")


@injectable
class K8sHandlerBase(ProviderHandlerBase, ABC):
    """Abstract base for kubernetes provider handlers.

    Subclasses implement the per-resource-API contract:

    * :meth:`acquire_hosts`         — async create the desired pods/workload
    * :meth:`check_hosts_status`    — return :class:`CheckHostsStatusResult`
    * :meth:`release_hosts`         — delete by machine_id list
    * :meth:`get_example_templates` — example templates for ``orb templates``
    """

    # Resource-API key for the handler (e.g. ``"Pod"``).  Used
    # for label injection and reconciler matching.  Subclasses override.
    PROVIDER_API: str = "Kubernetes"

    def __init__(
        self,
        kubernetes_client: K8sClient,
        config: K8sProviderConfig,
        logger: LoggingPort,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        *,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_reset_timeout: int = 60,
        pod_state_cache: Optional[PodStateCache] = None,
        cache_alive: Optional[Callable[[], bool]] = None,
        stale_cache_timeout_seconds: Optional[float] = None,
        native_spec_service: Optional[Any] = None,
        node_state_cache: Optional[K8sNodeStateCache] = None,
        metrics: Optional[K8sMetrics] = None,
    ) -> None:
        self._kubernetes_client = kubernetes_client
        self._config = config
        self._logger = logger
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._cb_failure_threshold = circuit_breaker_failure_threshold
        self._cb_reset_timeout = circuit_breaker_reset_timeout
        # Native-spec escape hatch.  ``None`` when the provider config
        # opts out (``native_spec_enabled=False``) or when the DI
        # resolution failed — handlers fall back to the typed builder
        # path in :mod:`orb.providers.k8s.utilities` when this is unset
        # or when the service reports the hatch disabled.
        self._native_spec_service = native_spec_service
        # Cache wiring: when both the cache and the ``cache_alive``
        # callable are supplied, :meth:`_read_from_cache` returns a
        # per-pod instance list built from the cached snapshots.  When
        # ``cache_alive()`` is ``False`` (watcher dead) or the cache has
        # no entry for the request (cold start), the helper returns
        # ``None`` so the caller falls back to a scoped list.  When the
        # cache contains entries but they are older than
        # ``stale_cache_timeout_seconds`` the cache is treated as stale
        # and the same fallback path is taken.
        self._pod_state_cache = pod_state_cache
        self._cache_alive = cache_alive
        self._stale_cache_timeout_seconds: float = (
            float(stale_cache_timeout_seconds)
            if stale_cache_timeout_seconds is not None
            else float(config.stale_cache_timeout_seconds)
        )
        # Node-state cache.  When ``node_watch_enabled=True`` the strategy
        # wires in a populated ``K8sNodeStateCache`` so handlers can look up
        # per-node metadata (instance type, zone, capacity type) by node_name
        # and attach it to the per-instance ``provider_data`` block.  When
        # ``None`` (the default) the lookup is silently skipped and the
        # provider_data block only contains the fields derived from the pod.
        self._node_state_cache = node_state_cache
        # Prometheus metrics.  When ``None`` (default) all record_* helpers
        # are no-ops so handlers stay side-effect-free in test paths that
        # do not inject a K8sMetrics instance.
        self._metrics = metrics

    # ------------------------------------------------------------------
    # Common helpers — used by every concrete handler
    # ------------------------------------------------------------------

    def _record_acquire(self, *, namespace: str, spec_kind: str) -> None:
        """Increment ``orb_k8s_acquire_total`` when metrics are wired."""
        if self._metrics is not None:
            self._metrics.record_acquire(namespace=namespace, spec_kind=spec_kind)

    def _record_release(self, *, namespace: str, spec_kind: str) -> None:
        """Increment ``orb_k8s_release_total`` when metrics are wired."""
        if self._metrics is not None:
            self._metrics.record_release(namespace=namespace, spec_kind=spec_kind)

    def _record_pod_creation(self, *, namespace: str, status: str) -> None:
        """Bucket a pod-creation outcome; safe under any input."""
        if self._metrics is not None:
            self._metrics.record_pod_creation(namespace=namespace, status=status)

    def _record_api_error(self, *, operation: str, error_code: str) -> None:
        """Increment ``orb_k8s_api_errors_total``; no-op when metrics not wired."""
        if self._metrics is not None:
            self._metrics.record_api_error(operation=operation, error_code=error_code)

    def _record_api_retry(self, *, operation: str) -> None:
        """Increment ``orb_k8s_api_retries_total``; no-op when metrics not wired."""
        if self._metrics is not None:
            self._metrics.record_api_retry(operation=operation)

    def _record_apiserver_latency(self, *, operation: str, seconds: float) -> None:
        """Observe an API server call latency sample; no-op when metrics not wired."""
        if self._metrics is not None:
            self._metrics.record_apiserver_latency(operation=operation, seconds=seconds)

    @contextmanager
    def _timed_api_call(self, operation: str) -> Generator[None, None, None]:
        """Context manager that times a single Kubernetes API call.

        Records ``orb_k8s_apiserver_latency_seconds`` with the measured wall
        time after the block completes (whether via normal return or exception).
        No-op when metrics are not wired.

        Uses :func:`time.perf_counter` (the highest-resolution clock for
        measuring elapsed durations) rather than :func:`time.monotonic` so the
        latency measurement is decoupled from the ``monotonic``-based TTL-cache
        bookkeeping in the status resolvers — a resolver may wrap its
        controller GET in this context manager and separately consult
        ``time.monotonic()`` for cache freshness without the two clocks
        interfering.

        Usage::

            with self._timed_api_call("create_namespaced_pod"):
                core_v1.create_namespaced_pod(...)
        """
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - t0
            self._record_apiserver_latency(operation=operation, seconds=elapsed)

    def _classify_and_record_api_exception(
        self,
        exc: BaseException,
        *,
        operation: str,
    ) -> K8sError:
        """Classify a raw ``ApiException`` into a typed K8sError and emit metrics.

        Call this at the API-call boundary after the retry budget is exhausted
        to translate the raw Kubernetes SDK exception into a structured typed
        exception.  Emits ``orb_k8s_api_errors_total`` with the HTTP status
        code.

        The retry layer may wrap the original exception in a
        ``MaxRetriesExceededError``; this helper unwraps one level to reach
        the underlying ``ApiException``.

        Args:
            exc: The exception caught at the API-call boundary.  May be a raw
                ``ApiException`` or a ``MaxRetriesExceededError`` wrapping one.
            operation: The Kubernetes API operation that failed (used for both
                the ``error_source`` field and metric labels).

        Returns:
            A :class:`~orb.providers.k8s.exceptions.k8s_exceptions.K8sError`
            sub-class instance with structured fields populated from the
            exception body.  If the exception is not an ``ApiException`` the
            base :class:`~orb.providers.k8s.exceptions.k8s_exceptions.K8sError`
            is returned wrapping the original message.
        """
        from orb.providers.k8s.exceptions.k8s_exceptions import K8sError, classify_api_exception

        # Unwrap one level of retry wrapping when present.
        candidate: BaseException = exc
        last_exception = getattr(exc, "last_exception", None)
        if isinstance(last_exception, BaseException):
            candidate = last_exception

        try:
            from kubernetes.client.exceptions import ApiException as _ApiException

            if isinstance(candidate, _ApiException):
                self._record_api_exception(exc, operation=operation)
                return classify_api_exception(candidate, operation=operation)
        except ImportError:  # pragma: no cover — kubernetes extra absent
            pass

        # Not an ApiException — wrap in base K8sError.
        self._record_api_exception(exc, operation=operation)
        return K8sError(str(exc), error_source=f"kubernetes.{operation}")

    def _record_api_exception(self, exc: BaseException, *, operation: str) -> None:
        """Record ``orb_k8s_api_errors_total`` for a caught API-boundary exception.

        Extracts the HTTP status code from a raw ``ApiException`` (unwrapping one
        level of retry wrapping when present) and increments the error counter.
        A ``429`` status additionally increments ``orb_k8s_api_throttles_total``
        via :meth:`K8sMetrics.record_api_error`.  Non-``ApiException`` inputs are
        bucketed under the ``"unknown"`` error code so read paths that swallow
        the exception (status resolvers) still surface as failures on the
        dashboards.

        Unlike :meth:`_classify_and_record_api_exception` this does not construct
        a typed :class:`K8sError`; it is intended for call sites that catch and
        recover from the exception rather than re-raising a structured error.
        """
        error_code = "unknown"
        # Unwrap one level of retry wrapping when present.
        candidate: BaseException = exc
        last_exception = getattr(exc, "last_exception", None)
        if isinstance(last_exception, BaseException):
            candidate = last_exception

        try:
            from kubernetes.client.exceptions import ApiException as _ApiException

            if isinstance(candidate, _ApiException):
                status = int(getattr(candidate, "status", 0) or 0)
                error_code = str(status) if status else "unknown"
        except ImportError:  # pragma: no cover — kubernetes extra absent
            pass

        self._record_api_error(operation=operation, error_code=error_code)

    @property
    def client(self) -> K8sClient:
        return self._kubernetes_client

    @property
    def config(self) -> K8sProviderConfig:
        return self._config

    def _audit_spec_body(self, body: Any) -> None:
        """Audit *body* for high-risk pod-spec fields.

        Called by each handler's ``acquire_hosts`` after the workload body
        is built and before it is submitted to the apiserver.  Behaviour
        is controlled by two :class:`K8sProviderConfig` flags:

        * ``audit_high_risk_pod_fields`` (default ``True``) — when
          ``False``, the entire audit is skipped silently.
        * ``reject_high_risk_pod_fields`` (default ``False``) — when
          ``True`` *and* findings are non-empty, a
          :class:`orb.providers.k8s.exceptions.k8s_exceptions.K8sError` is
          raised with the joined findings so the acquire call fails fast
          before touching the apiserver.

        *body* may be a plain ``dict`` (native-spec path, camelCase keys)
        or a Kubernetes SDK object whose ``.to_dict()`` produces a
        snake_case dict.  Both shapes are handled by converting SDK
        objects before passing to :func:`audit_pod_spec`.
        """
        if not self._config.audit_high_risk_pod_fields:
            return

        spec_dict: dict[str, Any]
        if isinstance(body, dict):
            spec_dict = body
        else:
            # SDK object (e.g. V1Pod, V1Deployment) — convert to a plain
            # dict so audit_pod_spec can walk the key/value pairs without
            # importing any kubernetes SDK types.
            try:
                spec_dict = body.to_dict()
            except AttributeError:
                # Unexpected type; skip rather than crash acquire.
                return

        findings = audit_pod_spec(spec_dict, self._logger)

        if findings and self._config.reject_high_risk_pod_fields:
            from orb.providers.k8s.exceptions.k8s_exceptions import K8sError

            raise K8sError(
                "Acquire rejected: pod spec contains high-risk fields — " + "; ".join(findings)
            )

    def resolve_namespace(self, template: Template) -> str:
        """Return the namespace this request should target.

        Delegates to
        :func:`~orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver.resolve_namespace`
        which applies the full resolution chain (template override →
        provider default → allowlist validation → RFC 1123 safety check).
        """
        return _resolve_ns(template, self._config)

    def build_label_selector(self, request: Request) -> str:
        """Convenience: build the ``label_selector=orb.io/request-id=<id>`` string."""
        return request_id_label_selector(request, label_prefix=self._config.label_prefix)

    def _stamp_native_workload_body(
        self,
        native_body: dict[str, Any],
        *,
        workload_name: str,
        namespace: str,
        replicas: int,
        request: Request,
    ) -> dict[str, Any]:
        """Stamp per-request identity onto a rendered native workload body.

        Delegates to
        :func:`~orb.providers.k8s.infrastructure.handlers.shared.label_stamper.stamp_native_workload_body`.
        """
        return _stamp_workload_body(
            native_body,
            workload_name=workload_name,
            namespace=namespace,
            replicas=replicas,
            request=request,
            label_prefix=self._config.label_prefix,
        )

    def apply_pod_timeouts(
        self,
        instances: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rewrite per-instance dicts for pods stuck Pending past the timeout.

        Wrapper around
        :func:`orb.providers.k8s.reconciliation.timeout_gc.apply_pod_timeout`
        that pulls the timeout from the provider config so handler call
        sites stay one line.  See the timeout_gc module docstring for
        the rewrite semantics — chiefly: ``status="terminated"`` and
        ``provider_data.unschedulable_reason`` populated from
        ``pod.status.conditions``.

        When :attr:`K8sProviderConfig.delete_timed_out_pods` is ``True``
        (the default), each newly-timed-out pod is also scheduled for
        immediate deletion via :func:`delete_timed_out_pod_async`.  The
        deletion is fired as a background asyncio task so the synchronous
        check path is not blocked.  404 responses and other errors are
        swallowed inside the async helper and logged without surfacing.
        When there is no running event loop (CLI / unit-test context) the
        deletion step is silently skipped.
        """
        from orb.providers.k8s.reconciliation.timeout_gc import (
            apply_pod_timeout,
        )

        rewritten = apply_pod_timeout(
            instances,
            pod_timeout_seconds=float(self._config.pod_timeout_seconds),
        )

        if self._config.delete_timed_out_pods:
            self._schedule_timed_out_pod_deletions(instances, rewritten)

        return rewritten

    def _schedule_timed_out_pod_deletions(
        self,
        original: list[dict[str, Any]],
        rewritten: list[dict[str, Any]],
    ) -> None:
        """Schedule fire-and-forget deletion tasks for pods newly timed out.

        Identifies instance dicts that transitioned to
        ``provider_data["timed_out"] = True`` in this pass (i.e. were not
        already marked timed-out on entry) and creates one async deletion
        task per pod.  Requires a running event loop; silently skips when
        none is available (CLI / synchronous test context).
        """
        import asyncio

        from orb.providers.k8s.reconciliation.timeout_gc import (
            delete_timed_out_pod_async,
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop — CLI or sync-test context; skip deletion.
            return

        # Build a set of identifiers for pods that were already timed-out on
        # entry so we do not re-schedule their deletions on every poll cycle.
        already_timed_out: set[str] = {
            str(inst.get("instance_id") or inst.get("name") or "")
            for inst in original
            if (inst.get("provider_data") or {}).get("timed_out")
        }

        core_v1 = self._kubernetes_client.core_v1

        for inst in rewritten:
            provider_data: dict[str, Any] = inst.get("provider_data") or {}
            if not provider_data.get("timed_out"):
                continue
            pod_id = str(inst.get("instance_id") or inst.get("name") or "")
            if pod_id in already_timed_out:
                continue

            pod_name: str = str(inst.get("name") or inst.get("instance_id") or "")
            namespace: str = str(provider_data.get("namespace") or self._config.namespace)
            reason: str = str(
                provider_data.get("unschedulable_reason")
                or inst.get("status_reason")
                or "Unschedulable"
            )

            if not pod_name:
                continue

            loop.create_task(
                delete_timed_out_pod_async(
                    core_v1,
                    name=pod_name,
                    namespace=namespace,
                    reason=reason,
                    logger=self._logger,
                )
            )

    def is_not_found(self, exc: BaseException) -> bool:
        """Return ``True`` when ``exc`` is (or wraps) a kubernetes 404 ``ApiException``.

        The retry decorator can wrap the original exception in a
        :class:`MaxRetriesExceededError` whose ``last_exception`` carries
        the genuine ``ApiException``; we unwrap one level to detect 404
        through the retry shell.
        """
        # Lazy import so the architecture test doesn't see a top-level kubernetes import.
        try:
            from kubernetes.client.exceptions import ApiException as _ApiException
        except ImportError:  # pragma: no cover — extra not installed
            return False

        candidate: BaseException | None = exc
        # Unwrap one level of retry wrapping when present.
        last_exception = getattr(exc, "last_exception", None)
        if isinstance(last_exception, BaseException):
            candidate = last_exception

        if not isinstance(candidate, _ApiException):
            return False
        status = getattr(candidate, "status", None)
        return status == 404

    def with_retry(
        self,
        operation: Callable[..., T],
        *args: Any,
        operation_name: str = "kubernetes_operation",
        **kwargs: Any,
    ) -> T:
        """Run ``operation`` with circuit-breaker-wrapped exponential-backoff retry.

        Used by handlers for individual SDK calls that should retry on
        transient errors (429 / 5xx).  400 / 403 / 404 / 409 / 410 / 422 are
        raised immediately — ``ExponentialBackoffStrategy.should_retry`` filters
        those non-recoverable Kubernetes API status codes without consuming
        retry budget.

        A per-handler-class circuit breaker is layered on top: once the
        apiserver failure count reaches ``circuit_breaker_failure_threshold``
        the circuit opens and subsequent calls fast-fail with
        ``CircuitBreakerOpenError`` until the reset window expires.  This
        prevents cascading retry storms during apiserver degradation.  The
        breaker is a :class:`K8sCircuitBreaker` wired with the handler's
        metrics recorder so ``orb_k8s_circuit_breaker_state`` reflects the
        state of the breakers that actually protect the apiserver calls.

        Metrics: every attempt beyond the first is a genuine retry from the
        resilience layer, so ``orb_k8s_api_retries_total`` is incremented once
        per re-invocation of ``operation`` (i.e. ``attempts - 1`` times).  The
        counter therefore reflects the real retry/backoff path rather than a
        caller-side estimate.  ``operation_name`` is used as the ``operation``
        label; values outside :data:`API_OPERATIONS` bucket to ``"unknown"``.
        """
        service_key = f"kubernetes.{self.PROVIDER_API.lower()}"

        # Metrics-aware circuit breaker so the gauge that tracks these
        # apiserver-protecting breakers is emitted from the production retry
        # path.  Semantics are identical to the base CircuitBreakerStrategy;
        # the subclass only layers the ``orb_k8s_circuit_breaker_state``
        # emission on top of the shared, class-level circuit state.
        breaker = K8sCircuitBreaker(
            service_name=service_key,
            max_attempts=self._max_retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            failure_threshold=self._cb_failure_threshold,
            reset_timeout=self._cb_reset_timeout,
            metrics=self._metrics,
        )

        attempt = 0
        # One-shot guard for the 401 credential-recovery path.  A 401 is
        # non-retryable via the backoff classifier (an expired/rejected token
        # needs a *refresh*, not a retry), so it is handled out-of-band: on the
        # first 401 we force the client to re-mint its credential and retry the
        # operation exactly once.  A second 401 falls through to the normal
        # non-retryable path and surfaces to the caller.
        auth_recovery_used = False
        while True:
            # Every invocation after the first is a retry issued by the
            # backoff loop — record it against the operation so
            # ``orb_k8s_api_retries_total`` tracks the actual retry path.
            if attempt > 0:
                self._record_api_retry(operation=operation_name)
            self._logger.debug(
                "Calling Kubernetes operation %s (args=%s kwargs=%s)",
                operation_name,
                args,
                {k: v for k, v in kwargs.items() if k != "body"},
            )
            try:
                result = operation(*args, **kwargs)
            except Exception as exc:
                # 401 recovery (bounded to once): an exec/SA token may be
                # rejected for a reason the SDK's expiry-based refresh hook does
                # not catch (identity change, clock skew, revoked session).
                # Force a credential re-mint and retry once before treating the
                # 401 as a terminal auth failure.
                if not auth_recovery_used and self._is_auth_401(exc):
                    auth_recovery_used = True
                    self._record_api_retry(operation=operation_name)
                    self._logger.warning(
                        "Kubernetes operation %s returned 401; forcing credential "
                        "refresh and retrying once.",
                        operation_name,
                    )
                    try:
                        self._kubernetes_client.force_token_refresh()
                    except Exception as refresh_exc:  # pragma: no cover — defensive
                        self._logger.error(
                            "Credential refresh after 401 failed for %s: %s",
                            operation_name,
                            refresh_exc,
                        )
                        raise exc from None
                    continue
                # ``should_retry`` updates the circuit state (and may raise
                # ``CircuitBreakerOpenError`` when the circuit is open),
                # emitting the state gauge on any transition.
                if not breaker.should_retry(attempt, exc):
                    if attempt >= self._max_retries:
                        raise MaxRetriesExceededError(attempt + 1, exc) from exc
                    raise
                delay = breaker.get_delay(attempt)
                breaker.on_retry(attempt, exc)
                if delay > 0:
                    time.sleep(delay)
                attempt += 1
                continue

            breaker.record_success()
            return result

    @staticmethod
    def _is_auth_401(exc: BaseException) -> bool:
        """Return ``True`` when *exc* is (or wraps) a 401 ``ApiException``.

        Unwraps one level of retry wrapping so a 401 surfaced through a
        ``MaxRetriesExceededError`` is still detected.  Returns ``False`` when
        the kubernetes SDK is not installed.
        """
        try:
            from kubernetes.client.exceptions import ApiException as _ApiException
        except ImportError:  # pragma: no cover — extra not installed
            return False

        candidate: BaseException = exc
        last_exception = getattr(exc, "last_exception", None)
        if isinstance(last_exception, BaseException):
            candidate = last_exception

        return isinstance(candidate, _ApiException) and getattr(candidate, "status", None) == 401

    # ------------------------------------------------------------------
    # Pod-state translation — shared between handlers and watcher
    # ------------------------------------------------------------------

    @staticmethod
    def _is_pod_ready(conditions: list[Any]) -> bool:
        """Thin delegate to :func:`pod_state.is_pod_ready` for subclass use."""
        return is_pod_ready(conditions)

    @staticmethod
    def _pod_status_string(
        phase: Optional[str],
        ready: bool,
        *,
        provider_api: Optional[str] = None,
    ) -> str:
        """Thin delegate to :func:`pod_state.pod_status_string` for subclass use."""
        return pod_status_string(phase, ready, provider_api=provider_api)

    @staticmethod
    def _extract_status_reason(
        container_statuses: list[Any],
        conditions: list[Any],
    ) -> Optional[str]:
        """Thin delegate to :func:`pod_state.extract_status_reason` for subclass use."""
        return extract_status_reason(container_statuses, conditions)

    def _instance_dict_for_pod(self, pod: Any, namespace: str) -> dict[str, Any]:
        """Convert a ``V1Pod`` to the per-instance dict shape ORB expects.

        Delegates to
        :func:`~orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator.instance_dict_for_pod`.
        """
        return _instance_dict_for_pod(
            pod,
            namespace,
            provider_api=self.PROVIDER_API,
            node_state_cache=self._node_state_cache,
            logger=self._logger,
        )

    def _instance_dict_for_state(self, state: PodState) -> dict[str, Any]:
        """Convert a cached :class:`PodState` into the instance-dict shape.

        Delegates to
        :func:`~orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator.instance_dict_for_state`.
        """
        return _instance_dict_for_state(
            state,
            provider_api=self.PROVIDER_API,
            node_state_cache=self._node_state_cache,
        )

    def _resolve_namespace_from_provider_data(self, provider_data: dict[str, Any]) -> str:
        """Resolve a namespace from a ``provider_data`` dict.

        Delegates to
        :func:`~orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver.resolve_namespace_from_provider_data`.
        """
        return _resolve_ns_from_provider_data(provider_data, self._config)

    def _resolve_request_namespace(self, request: Request) -> str:
        """Resolve a request's namespace using saved provider_data when present.

        Thin wrapper over :meth:`_resolve_namespace_from_provider_data` for
        callers (status resolvers) that still hold the full Request aggregate.
        """
        provider_data = getattr(request, "provider_data", None) or {}
        return self._resolve_namespace_from_provider_data(
            provider_data if isinstance(provider_data, dict) else {}
        )

    def _read_from_cache(self, request: Request) -> Optional[CheckHostsStatusResult]:
        """Cache-first read path.

        Returns:

        * ``None`` when the cache is not wired, the watcher reports
          dead, the cache has no entry for ``request.request_id``
          (cold start), or every cached entry was deemed stale.
        * Otherwise a :class:`CheckHostsStatusResult` whose
          ``instances`` field is the per-instance dict list built from
          the cached snapshots.  ``fulfilment`` is a placeholder that
          the caller MUST replace — either by computing it from the
          per-pod statuses (Pod handler) or by rebasing on the
          controller's view (Deployment / StatefulSet / Job).

        Stale-entry policy: cached entries for the request older than
        ``stale_cache_timeout_seconds`` are dropped before the lookup so
        the cache hit is consistent.  The dropped entries are logged at
        debug level.
        """
        cache = self._pod_state_cache
        if cache is None:
            return None
        if self._cache_alive is not None and not self._cache_alive():
            return None

        request_id = str(request.request_id)
        # Drop and discard entries older than the staleness window
        # before we consult the cache so the cache hit is consistent.
        dropped = cache.mark_stale(request_id, self._stale_cache_timeout_seconds)
        if dropped:
            self._logger.debug(
                "Dropped %s stale pod cache entr%s for request %s",
                len(dropped),
                "y" if len(dropped) == 1 else "ies",
                request_id,
            )

        states = cache.get(request_id)
        if states is None:
            return None

        instances = [self._instance_dict_for_state(state) for state in states]
        return CheckHostsStatusResult(
            instances=instances,
            fulfilment=ProviderFulfilment(
                state="in_progress",
                message="placeholder (caller rebases fulfilment)",
                target_units=request.requested_count,
            ),
        )

    # ------------------------------------------------------------------
    # Abstract contract — concrete handlers MUST implement
    # ------------------------------------------------------------------

    @abstractmethod
    async def acquire_hosts(self, request: Request, template: Template) -> dict[str, Any]:
        """Asynchronously provision pods/workloads to satisfy ``request``.

        Returns a dict with at minimum:

        * ``resource_ids`` — provider-level resource identifiers
          (pod names for the Pod handler; workload names for
          Deployment/StatefulSet/Job).
        * ``machine_ids``  — per-ORB-unit machine identifiers (typically
          pod names).
        * ``provider_data`` — provider-specific bookkeeping copied onto
          the Request aggregate.
        """

    @abstractmethod
    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Return per-instance details + a :class:`ProviderFulfilment` verdict."""

    @abstractmethod
    async def release_hosts(
        self,
        machine_ids: list[str],
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        """Delete the pods/workloads identified by ``machine_ids``.

        ``context`` is the neutral per-release context dict.  For Kubernetes
        handlers it is the ``provider_data`` dict stored on the Request
        aggregate at acquire time: it carries ``namespace`` (and for
        controller-based handlers ``deployment_name`` / ``job_name`` /
        ``statefulset_name``) so the handler can resolve context without
        needing the full Request aggregate.  The dict is the same object that
        was stamped under ``Request.provider_data`` by ``acquire_hosts``.
        """

    @classmethod
    @abstractmethod
    def get_example_templates(cls) -> list[Template]:
        """Return example templates for this handler's provider-API key."""

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    # get_handler_type is inherited from ProviderHandlerBase.
