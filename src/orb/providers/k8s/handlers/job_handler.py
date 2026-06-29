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

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.handlers.job_status import JobStatusResolver
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
        *,
        pod_state_cache: Optional[PodStateCache] = None,
        cache_alive: Optional[Callable[[], bool]] = None,
        stale_cache_timeout_seconds: Optional[float] = None,
        native_spec_service: Optional[Any] = None,
        node_state_cache: Optional[Any] = None,
    ) -> None:
        super().__init__(
            kubernetes_client=kubernetes_client,
            config=config,
            logger=logger,
            pod_state_cache=pod_state_cache,
            cache_alive=cache_alive,
            stale_cache_timeout_seconds=stale_cache_timeout_seconds,
            native_spec_service=native_spec_service,
            node_state_cache=node_state_cache,
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
        job_name = make_job_name(str(request.request_id))

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

        await asyncio.to_thread(
            self.with_retry,
            self.client.batch_v1.create_namespaced_job,
            namespace=namespace,
            body=body,
            operation_name="create_namespaced_job",
        )

        return {
            "resource_ids": [job_name],
            "machine_ids": [],
            "provider_data": {
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
        request: Request,
    ) -> None:
        """Delete the whole Job (cascade-deletes pods).

        Selective release is **not supported** for Jobs — ``parallelism``
        cannot be safely mutated post-creation given ORB's
        ``backoffLimit=0`` invariant.  Any call to ``release_hosts``
        deletes the entire Job; ``machine_ids`` is logged for audit but
        not honoured selectively.

        The Job is deleted with ``propagation_policy='Background'`` so
        the API call returns immediately and the controller cleans up
        the owned pods asynchronously.

        Args:
            machine_ids: Pod names the caller wanted to release.  Logged
                at info level for audit; not used for selective release.
            request: Request providing namespace + job-name context via
                ``provider_data`` (falls back to deterministic defaults).
        """
        if not machine_ids:
            self._logger.debug(
                "release_hosts called with no machine_ids for job request %s — no-op",
                request.request_id,
            )
            return

        namespace = self._resolve_request_namespace(request)
        job_name = self._resolve_job_name(request)

        self._logger.info(
            "Kubernetes job release: request_id=%s namespace=%s job=%s "
            "requested_machine_ids=%s (deleting whole Job — selective release not supported)",
            request.request_id,
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
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_job_name(self, request: Request) -> str:
        """Recover the Job name created at acquire time.

        Persisted in ``request.provider_data["job_name"]`` by
        :meth:`acquire_hosts`; falls back to the deterministic
        :func:`make_job_name` when the field is missing so callers that
        operate on a freshly-loaded Request still resolve a sensible
        value.
        """
        provider_data = getattr(request, "provider_data", None) or {}
        if isinstance(provider_data, dict):
            name = provider_data.get("job_name")
            if isinstance(name, str) and name:
                return name
        return make_job_name(str(request.request_id))

    # ------------------------------------------------------------------
    # Examples
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Return one example template that submits as a ``Job``."""
        from orb.providers.k8s.domain.template.k8s_template import (  # noqa: PLC0415
            K8sResourceQuantities,
            K8sTemplate,
        )

        return [
            K8sTemplate(
                template_id="k8s-job-example",
                name="Kubernetes Job example",
                description="Submit a run-to-completion Job via the kubernetes provider.",
                provider_api="Job",
                image_id="busybox:latest",
                max_instances=3,
                resource_requests=K8sResourceQuantities(cpu="100m", memory="128Mi"),
                resource_limits=K8sResourceQuantities(cpu="500m", memory="256Mi"),
                command=["sh", "-c", "echo done"],
            ),
        ]


__all__ = [
    "K8sJobHandler",
]
