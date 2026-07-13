"""K8sJobHandler — ``batch/v1 Job`` provisioning handler.

Run-to-completion semantics — Job controller
============================================

A ``batch/v1 Job`` is the Kubernetes-native primitive for
run-to-completion workloads.  The Job controller spawns
``spec.parallelism`` pods concurrently and the Job is ``Complete`` once
``spec.completions`` pods exit ``0``.  The handler maps one ORB request
to one Job with ``parallelism = completions = N`` so every requested
unit must run successfully to completion.

Crucial invariants
------------------

* ``backoffLimit = 0`` — ORB owns retry semantics at the *request*
  level.  The Job controller must NOT silently restart failed pods.
* ``parallelism`` cannot be safely mutated post-creation.  The Job
  controller does honour patches to ``spec.parallelism`` for live Jobs
  (since k8s 1.21), but the semantics around ``completions``,
  in-progress pods, and the ``Complete`` condition are subtle enough
  that **selective release is not supported**.  ``release_hosts`` deletes
  the entire Job (cascade-deletes pods) regardless of how many
  ``machine_ids`` the caller passes.
* Pod-level ``restartPolicy = Never`` is required when ``backoffLimit=0``
  (the controller validates it).  This is consistent with the
  stand-alone Pod handler's invariants.

Module split
------------

* ``acquire_hosts``    — creates one Job with
  ``spec.parallelism=spec.completions=request.requested_count`` and a
  pod template inheriting the full ORB label set.
* ``check_hosts_status`` — delegated to
  :class:`orb.providers.k8s.handlers.job_status.JobStatusResolver`
  which lists pods via the request-id label selector (cache-first when
  a watcher is wired) and reads back the controller view from the
  Job status.
* ``release_hosts``    — deletes the Job (cascade-deletes pods).  The
  ``machine_ids`` argument is informational only; selective release is
  not supported and the handler logs the requested IDs at info level for
  audit.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.di.injectable import injectable
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_exceptions import K8sError
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.handlers.job_status import JobStatusResolver
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.job_spec import (
    build_job_spec,
    make_job_name,
)
from orb.providers.k8s.watch.pod_state_cache import PodStateCache


@injectable
class K8sJobHandler(K8sHandlerBase):
    """Handler for the ``Job`` provider-API key.

    One ORB capacity unit equals one pod under a single Job
    (``batch/v1``).  Selective termination is NOT supported by this
    handler — see the module docstring for the rationale.
    """

    PROVIDER_API: str = "Job"

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
        node_state_cache: Optional[Any] = None,
        metrics: Optional[Any] = None,
    ) -> None:
        super().__init__(
            kubernetes_client=kubernetes_client,
            config=config,
            logger=logger,
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            circuit_breaker_failure_threshold=circuit_breaker_failure_threshold,
            circuit_breaker_reset_timeout=circuit_breaker_reset_timeout,
            pod_state_cache=pod_state_cache,
            cache_alive=cache_alive,
            stale_cache_timeout_seconds=stale_cache_timeout_seconds,
            native_spec_service=native_spec_service,
            node_state_cache=node_state_cache,
            metrics=metrics,
        )
        self._status_resolver = JobStatusResolver(self)

    # ------------------------------------------------------------------
    # acquire_hosts
    # ------------------------------------------------------------------

    async def acquire_hosts(self, request: Request, template: Template) -> dict[str, Any]:
        """Create a single Job with ``parallelism = completions = N``.

        The Job is named ``orb-{request_id[:8]}``.  Pod names are
        stamped by the controller as ``orb-{request_id[:8]}-<random>``
        and are NOT known at acquire time — the strategy resolves them
        later via :meth:`check_hosts_status`.

        Returns a dict consumed by the strategy's ``acquire`` to build
        the :class:`Accepted` outcome.  ``resource_ids`` is the
        single-element list ``[job_name]``; ``machine_ids`` is empty at
        acquire time because the controller has not yet stamped pod
        names.  ``provider_data`` carries the namespace, Job name and
        the requested parallelism so the release / status paths can
        recover context without re-querying.
        """
        namespace = self.resolve_namespace(template)
        parallelism = max(int(request.requested_count), 1)
        job_name = make_job_name(str(request.request_id), naming=self._config.naming)

        self._record_acquire(namespace=namespace, spec_kind=self.PROVIDER_API)
        self._logger.info(
            "Kubernetes job acquire: request_id=%s namespace=%s job=%s parallelism=%s",
            request.request_id,
            namespace,
            job_name,
            parallelism,
        )

        native_body = (
            self._native_spec_service.process_job_spec(template, request, namespace=namespace)
            if self._native_spec_service is not None
            else None
        )
        if native_body is not None:
            body: Any = self._stamp_native_workload_body(
                native_body,
                workload_name=job_name,
                namespace=namespace,
                replicas=parallelism,
                request=request,
            )
        else:
            body = build_job_spec(
                template,
                request,
                job_name=job_name,
                namespace=namespace,
                parallelism=parallelism,
                provider_api=self.PROVIDER_API,
                config=self._config,
            )

        self._audit_spec_body(body)

        try:
            with self._timed_api_call("create_namespaced_job"):
                await asyncio.to_thread(
                    self.with_retry,
                    self.client.batch_v1.create_namespaced_job,
                    namespace=namespace,
                    body=body,
                    operation_name="create_namespaced_job",
                )
        except Exception as exc:
            raise self._classify_and_record_api_exception(
                exc, operation="create_namespaced_job"
            ) from exc

        return {
            "success": True,
            "resource_ids": [job_name],
            "machine_ids": [],
            "provider_data": {
                "request_id": str(request.request_id),
                "namespace": namespace,
                "job_name": job_name,
                "parallelism": parallelism,
            },
        }

    # ------------------------------------------------------------------
    # check_hosts_status — delegated to JobStatusResolver
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Delegate the status read path to :class:`JobStatusResolver`."""
        return self._status_resolver.check_hosts_status(request)

    # ------------------------------------------------------------------
    # release_hosts
    # ------------------------------------------------------------------

    async def release_hosts(
        self,
        machine_ids: list[str],
        provider_data: dict[str, Any],
    ) -> None:
        """Delete the whole Job (cascade-deletes pods).

        Selective release is **not supported** for Jobs — ``parallelism``
        cannot be safely mutated post-creation given ORB's
        ``backoffLimit=0`` invariant.  When the caller-supplied
        ``machine_ids`` covers every pod of the Job, the entire Job is
        deleted; when it covers only a subset, the handler refuses the
        request rather than silently deleting workloads the caller did
        not ask to release.

        When ``provider_data["parallelism"]`` is absent or zero (for
        example, return callers that stamp only ``job_name`` and not
        ``parallelism``), the handler resolves parallelism from the live
        Job spec via ``read_namespaced_job`` before applying the
        full/subset check.  This keeps the selective-release guard intact
        while allowing legitimate full releases that omit parallelism in
        ``provider_data``.

        Args:
            machine_ids: Pod names the caller wants to release.  Must
                cover every pod of the Job (length >=
                ``resolved_parallelism``); a strict subset is rejected.
                If the live Job cannot be read and parallelism is still
                unknown, the release is refused — we cannot confirm the
                caller is releasing the whole Job.
            provider_data: The ``provider_data`` dict stamped onto the
                Request aggregate at acquire time.  Carries ``namespace``,
                ``job_name`` and optionally ``parallelism``.

        Raises:
            K8sError: When ``machine_ids`` covers fewer pods than the
                Job was created with, or when ``parallelism`` cannot be
                determined (absent in ``provider_data`` and live Job
                read fails).
        """
        request_id = provider_data.get("request_id", "unknown")
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for job request %s — no-op",
                request_id,
            )
            return

        namespace = self._resolve_namespace_from_provider_data(provider_data)
        job_name = self._resolve_job_name_from_provider_data(provider_data)
        self._record_release(namespace=namespace, spec_kind=self.PROVIDER_API)

        parallelism = int(provider_data.get("parallelism") or 0)
        if parallelism == 0:
            # parallelism is absent or zero in provider_data — resolve it
            # from the live Job spec so that legitimate full releases that
            # omit parallelism (e.g. return callers stamping only
            # job_name) are not wrongly refused.
            parallelism = await self._resolve_parallelism_from_live_job(
                namespace, job_name, request_id
            )
            if parallelism == 0:
                # Live read failed or returned no usable parallelism —
                # cannot confirm whether machine_ids covers the full Job.
                # Deleting the Job would cascade-delete every pod, not
                # just the ones the caller named.  Refuse rather than
                # silently over-releasing.
                raise K8sError(
                    "Job release refused for "
                    f"request {request_id} (job={namespace}/{job_name}): "
                    f"provider_data is missing 'parallelism' and the live Job "
                    f"spec could not be read, so we cannot confirm that the "
                    f"{len(machine_ids)} machine_id(s) cover the full Job.  "
                    "Deleting the Job would cascade-delete all pods, not just "
                    "the requested subset.  Ensure provider_data['parallelism'] "
                    "is set (it is written by acquire_hosts).  "
                    f"requested_machine_ids={machine_ids}"
                )

        if len(machine_ids) < parallelism:
            raise K8sError(
                "Job selective release refused for "
                f"request {request_id} (job={namespace}/{job_name}): the Job "
                f"controller does not support subset release (parallelism cannot be "
                f"mutated post-creation).  Deleting the Job now would evict every "
                f"pod, not just the {len(machine_ids)} caller requested.  Either "
                f"release every pod for the request (len(machine_ids) == parallelism "
                f"== {parallelism}) or leave the Job running.  requested_machine_ids="
                f"{machine_ids}"
            )

        self._logger.info(
            "Kubernetes job release: request_id=%s namespace=%s job=%s "
            "requested_machine_ids=%s (deleting whole Job)",
            request_id,
            namespace,
            job_name,
            machine_ids,
        )

        await self._delete_job(namespace, job_name)

    async def _delete_job(self, namespace: str, job_name: str) -> None:
        """Delete the Job with background propagation.

        Background propagation lets the API return immediately and the
        controller cleans up the owned pods asynchronously.  404s are
        best-effort — a Job that already evaporated is fine.
        """
        try:
            with self._timed_api_call("delete_namespaced_job"):
                await asyncio.to_thread(
                    self.client.batch_v1.delete_namespaced_job,
                    name=job_name,
                    namespace=namespace,
                    propagation_policy="Background",
                )
            return
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "Job %s in %s already gone (404) — delete is a no-op",
                    job_name,
                    namespace,
                )
                return
            self._logger.debug(
                "Initial delete failed for job=%s in %s; retrying: %s",
                job_name,
                namespace,
                exc,
            )

        try:
            with self._timed_api_call("delete_namespaced_job"):
                await asyncio.to_thread(
                    self.with_retry,
                    self.client.batch_v1.delete_namespaced_job,
                    name=job_name,
                    namespace=namespace,
                    propagation_policy="Background",
                    operation_name="delete_namespaced_job",
                )
        except Exception as exc:
            if self.is_not_found(exc):
                return
            self._logger.warning(
                "Failed to delete job=%s namespace=%s: %s",
                job_name,
                namespace,
                exc,
            )
            raise self._classify_and_record_api_exception(
                exc, operation="delete_namespaced_job"
            ) from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_parallelism_from_live_job(
        self,
        namespace: str,
        job_name: str,
        request_id: str,
    ) -> int:
        """Read parallelism from the live Job spec when provider_data omits it.

        Returns ``spec.parallelism`` (falling back to ``spec.completions``)
        from a live ``read_namespaced_job`` call.  Returns ``0`` when the
        Job cannot be read (404, API error) or when neither field carries a
        positive integer — the caller must then refuse the release rather
        than guess.

        This is called only when ``provider_data["parallelism"]`` is absent
        or zero, which happens for return requests that stamp only
        ``job_name`` and not the full acquire-time provider_data.
        """
        self._logger.debug(
            "release_hosts: parallelism absent in provider_data for request %s "
            "(job=%s/%s) — resolving from live Job spec",
            request_id,
            namespace,
            job_name,
        )
        try:
            job = await asyncio.to_thread(
                self.with_retry,
                self.client.batch_v1.read_namespaced_job,
                name=job_name,
                namespace=namespace,
                operation_name="read_namespaced_job",
            )
        except Exception as exc:
            if self.is_not_found(exc):
                self._logger.debug(
                    "release_hosts: Job %s/%s not found while resolving parallelism "
                    "— treating as already-released (returning 0)",
                    namespace,
                    job_name,
                )
            else:
                self._logger.warning(
                    "release_hosts: read_namespaced_job failed for %s/%s while "
                    "resolving parallelism: %s",
                    namespace,
                    job_name,
                    exc,
                )
            return 0

        spec = getattr(job, "spec", None)
        if spec is None:
            return 0
        # Prefer spec.parallelism; fall back to spec.completions as a
        # secondary source (they are always equal for ORB-created Jobs).
        for attr in ("parallelism", "completions"):
            value = getattr(spec, attr, None)
            if isinstance(value, int) and value > 0:
                self._logger.debug(
                    "release_hosts: resolved parallelism=%s from live Job spec.%s "
                    "for request %s (job=%s/%s)",
                    value,
                    attr,
                    request_id,
                    namespace,
                    job_name,
                )
                return value
        return 0

    def _resolve_job_name_from_provider_data(self, provider_data: dict[str, Any]) -> str:
        """Recover the Job name from a ``provider_data`` dict.

        Reads the ``job_name`` key written by ``acquire_hosts``; falls back
        to the deterministic :func:`make_job_name` using the ``request_id``
        key when the field is missing.
        """
        name = provider_data.get("job_name")
        if isinstance(name, str) and name:
            return name
        return make_job_name(
            str(provider_data.get("request_id", "unknown")), naming=self._config.naming
        )

    def _resolve_job_name(self, request: Request) -> str:
        """Thin wrapper for status resolvers that hold the full Request aggregate."""
        provider_data = getattr(request, "provider_data", None) or {}
        pd = provider_data if isinstance(provider_data, dict) else {}
        name = pd.get("job_name")
        if isinstance(name, str) and name:
            return name
        return make_job_name(str(request.request_id), naming=self._config.naming)

    # ------------------------------------------------------------------
    # Examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Return one example template that submits as a ``Job``."""
        from orb.providers.k8s.domain.template.k8s_template_aggregate import (
            K8sResourceQuantities,
            K8sTemplate,
        )

        return [
            K8sTemplate(
                template_id="k8s-job-example",
                name="Kubernetes Job example",
                description="Submit a run-to-completion Job via the kubernetes provider.",
                provider_api="Job",
                # A Job needs run-to-completion semantics, so it uses a minimal
                # image that has a shell and a command that exits 0 immediately.
                # The pause image (used by the long-running kinds) has no shell,
                # so it cannot run a command and is unsuitable for a Job.
                image_id="busybox:1.37",
                max_instances=3,
                resource_requests=K8sResourceQuantities(cpu="100m", memory="128Mi"),
                resource_limits=K8sResourceQuantities(cpu="500m", memory="256Mi"),
                command=["sh", "-c", "exit 0"],
            ),
        ]


__all__ = [
    "K8sJobHandler",
]
