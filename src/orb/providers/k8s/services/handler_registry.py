"""Handler registry for :class:`K8sProviderStrategy`.

Encapsulates the per-API handler factory wiring, the template-resolution
path used at acquire time, and the typed dispatch helpers
(``acquire`` / ``return_machines`` / ``get_status``).

The strategy keeps the lifecycle surface (initialize / cleanup, watch
manager, startup reconciler, orphan GC, health, capabilities, plugin
registration, native-spec resolution) and delegates the per-request
execution to this registry, which mirrors the AWS provider's separation
between its strategy shell and its handler factory.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional

from orb.domain.base.operation_outcome import Accepted, Completed, Failed, OperationOutcome
from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler
from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver import (
    resolve_namespace_from_provider_data,
)
from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.utilities.labels import K8sValidationError
from orb.providers.k8s.value_objects import KubernetesProviderApi
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache

if TYPE_CHECKING:
    from orb.domain.request.aggregate import Request
    from orb.domain.template.template_aggregate import Template


class K8sHandlerRegistry:
    """Resolve per-API handlers and dispatch acquire/return/status calls.

    Construction is light: the registry holds references to the strategy's
    config, logger, lazy client accessor, the lazy watcher accessor, the
    native-spec resolver, and the plugin-factory accessor.  Handler
    instances are cached on first resolution so each provider-API only
    ever holds one live handler.

    Plugin-registered handler factories are looked up on the
    :class:`K8sProviderStrategy` class — the registry consults
    ``plugin_factories`` (a callable returning the dict) so the strategy
    remains the single source of truth for plugin state.
    """

    # Frozen default handler classes — used only as the seed for
    # per-instance copies constructed in ``__init__``.  Never mutate
    # this dict directly; handler registration operates on the
    # instance's ``_handler_classes`` instead.
    _DEFAULT_HANDLER_CLASSES: dict[str, type[K8sHandlerBase]] = {
        KubernetesProviderApi.POD.value: K8sPodHandler,
        KubernetesProviderApi.DEPLOYMENT.value: K8sDeploymentHandler,
        KubernetesProviderApi.STATEFUL_SET.value: K8sStatefulSetHandler,
        KubernetesProviderApi.JOB.value: K8sJobHandler,
    }

    def __init__(
        self,
        *,
        config: K8sProviderConfig,
        logger: LoggingPort,
        client_provider: Callable[[], K8sClient],
        watch_manager_provider: Callable[[], Optional[MultiNamespaceWatcher]],
        plugin_factories: Callable[[], dict[str, Callable[..., K8sHandlerBase]]],
        native_spec_service_provider: Callable[[], Optional[Any]],
        handler_overrides: Optional[dict[str, K8sHandlerBase]] = None,
        node_state_cache_provider: Optional[Callable[[], Optional[K8sNodeStateCache]]] = None,
        api_aliases: Optional[dict[str, str]] = None,
        metrics_provider: Optional[Callable[[], Optional[Any]]] = None,
        handler_classes: Optional[Mapping[str, type[K8sHandlerBase]]] = None,
        default_api: str = KubernetesProviderApi.POD.value,
        provider_type: str = "k8s",
        template_class: Optional[type[K8sTemplate]] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._client_provider = client_provider
        self._watch_manager_provider = watch_manager_provider
        self._plugin_factories = plugin_factories
        self._native_spec_service_provider = native_spec_service_provider
        # Node-state cache provider.  When non-None, handlers look up node
        # metadata (instance type, zone, capacity type) by node_name to
        # enrich the per-instance ``provider_data`` block.
        self._node_state_cache_provider = node_state_cache_provider
        # Alias table for alternate-case provider_api spellings.  Consulted
        # by resolve_provider_api before the handler cache lookup so that
        # lowercase submissions (e.g. "pod") route to the correct handler.
        self._api_aliases: dict[str, str] = dict(api_aliases or {})
        # Metrics provider — called lazily so handlers get the same
        # ``K8sMetrics`` instance the strategy holds.  ``None`` when the
        # strategy did not initialise metrics.
        self._metrics_provider = metrics_provider
        # Per-instance mutable copy of the handler-class table.  Seeded from
        # the class-level defaults so every registry instance starts with the
        # four built-in kinds but is fully isolated from every other instance.
        self._handler_classes: dict[str, type[K8sHandlerBase]] = dict(
            handler_classes if handler_classes is not None else self._DEFAULT_HANDLER_CLASSES
        )
        # ``provider_api`` a request without one resolves to, the provider type
        # used for synthetic ``<type>/<api>`` instance types and fallback
        # templates, and the template class requests are upcast to.
        self._default_api = default_api
        self._provider_type = provider_type
        self._template_class: type[K8sTemplate] = template_class or K8sTemplate
        # Handler cache keyed by provider_api value.  Tests can pre-seed
        # this via ``handler_overrides`` to inject mock handlers.
        self._handlers: dict[str, K8sHandlerBase] = dict(handler_overrides or {})

    # ------------------------------------------------------------------
    # Public surface used by the strategy
    # ------------------------------------------------------------------

    @property
    def handlers(self) -> dict[str, K8sHandlerBase]:
        """Mutable handler cache — exposed for test fixtures."""
        return self._handlers

    def resolve_provider_api(self, request: Request) -> str:
        """Pick the provider-API key for ``request``.

        Applies alias normalisation (e.g. ``"pod"`` → ``"Pod"``) before
        returning so that case-variant submissions reach the correct handler.
        Defaults to :attr:`KubernetesProviderApi.POD` when the request
        carries no ``provider_api`` field.
        """
        api = getattr(request, "provider_api", None)
        if api:
            raw = str(api)
            return self._api_aliases.get(raw, raw)
        return self._default_api

    def _resilience_kwargs(self) -> dict[str, Any]:
        """Map the provider config's circuit-breaker / retry knobs to handler kwargs.

        ``K8sHandlerBase`` (and every concrete handler) accepts
        ``max_retries`` / ``base_delay`` / ``max_delay`` positionally and
        ``circuit_breaker_failure_threshold`` / ``circuit_breaker_reset_timeout``
        as keyword-only args, then feeds them into :meth:`with_retry`.  The
        provider config exposes the operator-facing spellings
        (``retry_base_delay`` / ``retry_max_delay`` / ``max_retries`` /
        ``circuit_breaker_*``); this helper bridges the two so the configured
        values actually reach the handler's retry/circuit-breaker layer.

        ``getattr`` with the K8sHandlerBase default is used so a config object
        that predates these fields (or a hand-rolled test stub) still yields a
        valid kwargs mapping rather than raising ``AttributeError``.
        """
        cfg = self._config
        return {
            "max_retries": getattr(cfg, "max_retries", 3),
            "base_delay": getattr(cfg, "retry_base_delay", 1.0),
            "max_delay": getattr(cfg, "retry_max_delay", 30.0),
            "circuit_breaker_failure_threshold": getattr(
                cfg, "circuit_breaker_failure_threshold", 5
            ),
            "circuit_breaker_reset_timeout": getattr(cfg, "circuit_breaker_reset_timeout", 60),
        }

    def get_handler(self, provider_api: str) -> K8sHandlerBase:
        """Return (and lazily construct) the handler for ``provider_api``.

        Applies alias normalisation before the cache lookup so that
        lowercase submissions (e.g. ``"pod"``) reach the correct handler
        without requiring callers to pre-normalise the key.
        """
        provider_api = self._api_aliases.get(provider_api, provider_api)
        handler = self._handlers.get(provider_api)
        if handler is not None:
            return handler

        watch_manager = self._watch_manager_provider()
        cache = watch_manager.cache if watch_manager is not None else None
        alive = (lambda m=watch_manager: m.is_healthy()) if watch_manager is not None else None
        native_spec_service = self._native_spec_service_provider()
        node_cache = (
            self._node_state_cache_provider()
            if self._node_state_cache_provider is not None
            else None
        )

        # Circuit-breaker and retry knobs read from the provider config so the
        # handler's ``with_retry`` uses operator-tuned resilience values instead
        # of the K8sHandlerBase hardcoded defaults.  Threaded into both the
        # built-in and plugin construction paths.  See
        # ``K8sProviderConfig`` (circuit_breaker_* / max_retries / retry_*).
        resilience_kwargs = self._resilience_kwargs()

        handler_class = self._handler_classes.get(provider_api)
        if handler_class is not None:
            handler = handler_class(
                kubernetes_client=self._client_provider(),
                config=self._config,
                logger=self._logger,
                pod_state_cache=cache,
                cache_alive=alive,
                native_spec_service=native_spec_service,
                node_state_cache=node_cache,
                metrics=self._metrics_provider() if self._metrics_provider is not None else None,
                **resilience_kwargs,
            )
            self._handlers[provider_api] = handler
            return handler

        # Plugin-supplied handlers — see ``K8sProviderStrategy.register_handler``
        # and ``docs/root/providers/k8s/plugin-authoring.md``.
        # Factories receive the full seven-kwarg surface so plugins that
        # consume ``native_spec_service`` or ``node_state_cache`` do not
        # silently receive ``None``, plus the resilience knobs so plugin
        # handlers inherit the same operator-tuned retry/circuit-breaker values.
        factory = self._plugin_factories().get(provider_api)
        if factory is not None:
            handler = factory(
                kubernetes_client=self._client_provider(),
                config=self._config,
                logger=self._logger,
                pod_state_cache=cache,
                cache_alive=alive,
                native_spec_service=native_spec_service,
                node_state_cache=node_cache,
                metrics=self._metrics_provider() if self._metrics_provider is not None else None,
                **resilience_kwargs,
            )
            self._handlers[provider_api] = handler
            return handler
        raise NotImplementedError(
            f"Kubernetes handler for provider_api={provider_api!r} is not yet implemented "
            "(Pod, Deployment, StatefulSet and Job are implemented; third-party "
            "plugins may register additional handlers via "
            "K8sProviderStrategy.register_handler)."
        )

    @classmethod
    def generate_example_templates(
        cls,
        *,
        plugin_factories: Optional[dict[str, Callable[..., K8sHandlerBase]]] = None,
        handler_classes: Optional[Mapping[str, type[K8sHandlerBase]]] = None,
    ) -> list[Any]:
        """Return example templates contributed by every registered handler.

        Iterates the handler-class table (the built-in kinds unless
        ``handler_classes`` supplies a provider type's own) plus any plugin-
        registered factories.  Each handler exposes a classmethod
        ``get_example_templates`` (or a callable on the plugin factory's
        target class).  Handlers without that method or that fail to produce
        examples are skipped silently — the adapter degrades gracefully.

        Class-level entry point so it can be called without constructing a
        live :class:`K8sHandlerRegistry` (i.e. without ``K8sClient`` /
        watcher providers).  Mirrors
        :meth:`AWSHandlerFactory.generate_example_templates`.
        """
        examples: list[Any] = []
        table = handler_classes if handler_classes is not None else cls._DEFAULT_HANDLER_CLASSES
        sources: dict[str, Any] = {**table, **(plugin_factories or {})}
        for handler_target in sources.values():
            getter = getattr(handler_target, "get_example_templates", None)
            if getter is None:
                continue
            try:
                examples.extend(getter())
            except Exception:  # noqa: S112  # a handler failing to produce examples must not block the others
                continue
        return examples

    # ------------------------------------------------------------------
    # Typed provisioning interface
    # ------------------------------------------------------------------

    async def acquire(self, request: Request) -> OperationOutcome:
        """Submit an acquisition request to Kubernetes via the per-API handler."""
        try:
            provider_api = self.resolve_provider_api(request)
            handler = self.get_handler(provider_api)
            template = self.build_template_for_request(request)
            result = await handler.acquire_hosts(request, template)

            resource_ids = list(result.get("resource_ids", []) or [])
            machine_ids = list(result.get("machine_ids", []) or [])
            metadata: dict[str, Any] = {
                "provider_api": provider_api,
                "provider_data": result.get("provider_data", {}),
                "machine_ids": machine_ids,
            }
            self._logger.info(
                "Kubernetes acquire accepted: request_id=%s pods=%s",
                request.request_id,
                resource_ids,
            )
            return Accepted(
                request_id=str(request.request_id),
                pending_resource_ids=resource_ids,
                metadata=metadata,
            )
        except Exception as exc:
            self._logger.error("Kubernetes acquire failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=False)

    async def return_machines(
        self,
        machine_ids: list[str],
        request: Request,
        provider_data_overrides: Optional[dict[str, Any]] = None,
        machine_coordinates: Optional[dict[str, dict[str, Any]]] = None,
    ) -> OperationOutcome:
        """Delete the named resources via the per-API handler.

        ``machine_coordinates`` (per machine id, as persisted at acquire time)
        places each pod: pods are deleted in the namespace they were created in,
        not in the return request's (empty) default.

        ``provider_data_overrides`` lets the caller supply the acquire-time
        controller name and origin request id, which the controller-backed
        handlers (Deployment/StatefulSet/Job) need to resolve the correct
        resource to delete.  A return request carries its own (different)
        ``request_id`` and no ``deployment_name``/etc., so without the override
        those handlers would derive a wrong name and no-op on a 404, leaking the
        controller.  The Pod handler is unaffected (it deletes by ``machine_ids``).
        """
        try:
            provider_api = self.resolve_provider_api(request)
            handler = self.get_handler(provider_api)
            provider_data: dict[str, Any] = dict(getattr(request, "provider_data", None) or {})
            provider_data.setdefault("request_id", str(request.request_id))
            # Overrides win: they carry the acquire-time resource name + origin id.
            for key, value in (provider_data_overrides or {}).items():
                if value:
                    provider_data[key] = value
            # release_hosts returns a dict with ``deleted`` and
            # ``failed_deletes`` when partial failure occurred.  The caller
            # only needs the successfully deleted IDs as pending_resource_ids
            # so the status-poll path can observe them draining.  One call per
            # namespace the machines were placed in.
            deleted: list[str] = []
            failed_names: list[str] = []
            # Release per namespace only: controller-backed handlers compare the
            # number of ids released against the controller's replicas, so ids of
            # one controller must not be split across calls.
            by_namespace: dict[str, list[str]] = {}
            groups, invalid_ids = self._group_by_placement(
                list(machine_ids), machine_coordinates, request
            )
            # Machines whose stored namespace is invalid are not released; the
            # rest of the batch proceeds.
            failed_names.extend(invalid_ids)
            delete_failures = 0
            for (namespace, _origin), grouped in groups.items():
                by_namespace.setdefault(namespace, []).extend(grouped)
            for namespace, names in by_namespace.items():
                release_result = await handler.release_hosts(
                    names, {**provider_data, "namespace": namespace}
                )
                release_info: dict[str, Any] = (
                    release_result if isinstance(release_result, dict) else {}
                )
                deleted.extend(release_info.get("deleted", names))
                failed_deletes = [
                    str(entry[0] if isinstance(entry, (tuple, list)) else entry)
                    for entry in (release_info.get("failed_deletes") or [])
                ]
                failed_names.extend(failed_deletes)
                delete_failures += len(failed_deletes)
            if failed_names:
                # Some deletes failed. Report the per-ID split under
                # ``provider_data`` so the deprovisioning orchestrator can mark
                # only the deleted pods as terminated and retry the rest.
                self._logger.warning(
                    "Kubernetes return partially failed: request_id=%s deleted=%d failed=%s",
                    request.request_id,
                    len(deleted),
                    failed_names,
                )
                return Failed(
                    error=f"{len(failed_names)} of {len(machine_ids)} pod deletions failed",
                    # An invalid stored namespace never succeeds on retry; only
                    # failed deletes are worth retrying.
                    recoverable=delete_failures > 0,
                    metadata={
                        "provider_api": provider_api,
                        "provider_data": {
                            "successful_instance_ids": list(deleted),
                            "failed_instance_ids": failed_names,
                        },
                    },
                )
            self._logger.info(
                "Kubernetes return accepted: request_id=%s machine_ids=%s",
                request.request_id,
                deleted,
            )
            return Accepted(
                request_id=str(request.request_id),
                pending_resource_ids=deleted,
                metadata={"provider_api": provider_api},
            )
        except Exception as exc:
            self._logger.error("Kubernetes return_machines failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=False)

    def _group_by_placement(
        self,
        machine_ids: list[str],
        machine_coordinates: Optional[dict[str, dict[str, Any]]],
        request: Request,
    ) -> tuple[dict[tuple[str, Optional[str]], list[str]], list[str]]:
        """Group machine ids by ``(namespace, origin request id)``.

        Namespace precedence per machine: its persisted ``provider_data``, then
        the request's own ``provider_data``, then the configured default.
        Machines without coordinates land in the default namespace with no
        origin request id.

        Returns the groups plus the ids whose stored namespace is invalid; those
        machines are left out of the groups so one bad row cannot fail the batch.
        """
        request_data = request.provider_data or {}
        groups: dict[tuple[str, Optional[str]], list[str]] = {}
        invalid: list[str] = []
        for mid in machine_ids:
            coords = (machine_coordinates or {}).get(mid) or {}
            placed = coords.get("provider_data") or {}
            source = placed if placed.get("namespace") else request_data
            try:
                namespace = resolve_namespace_from_provider_data(
                    source if isinstance(source, dict) else {}, self._config
                )
            except K8sValidationError:
                self._logger.warning(
                    "Kubernetes machine has an invalid stored namespace: machine_id=%s", mid
                )
                invalid.append(mid)
                continue
            origin = str(coords["request_id"]) if coords.get("request_id") else None
            groups.setdefault((namespace, origin), []).append(mid)
        return groups, invalid

    async def _read_return_pods(
        self,
        handler: K8sHandlerBase,
        resource_ids: list[str],
        request: Request,
        machine_coordinates: Optional[dict[str, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], set[str], ProviderFulfilment]:
        """List the returned pods per (namespace, origin request) group."""
        instances: list[dict[str, Any]] = []
        unreadable: set[str] = set()
        groups, invalid_ids = self._group_by_placement(
            list(resource_ids), machine_coordinates, request
        )
        unreadable.update(invalid_ids)
        for (namespace, origin), names in groups.items():
            result = await asyncio.to_thread(handler.read_pods_for_return, namespace, origin, names)
            instances.extend(result.instances)
            unreadable |= result.unreadable
        pending = len(unreadable) + sum(
            1 for i in instances if i.get("status") not in ("terminated", "unknown")
        )
        fulfilment = ProviderFulfilment(
            state="in_progress" if pending else "fulfilled",
            message=f"{pending} returned machine(s) not yet gone",
            target_units=len(resource_ids),
            pending_count=pending,
        )
        return instances, unreadable, fulfilment

    async def get_status(
        self,
        resource_ids: list[str],
        request: Request,
        machine_coordinates: Optional[dict[str, dict[str, Any]]] = None,
    ) -> OperationOutcome:
        """Poll the per-API handler's ``check_hosts_status`` for a verdict.

        Returns ``Completed`` when fulfilment is terminal (``fulfilled``,
        ``partial``, or ``failed``); ``Accepted`` while ``in_progress``.

        Caller-supplied ``resource_ids`` (machine IDs that the caller knows
        about) are reconciled against the live cluster: any ID absent from
        the live list is surfaced as a synthetic ``status='terminated'``
        instance.  This is what lets a return request observe its target
        pods disappearing — the typed ``Accepted`` outcome carries the
        full machine surface so :mod:`machine_sync_service` can mark the
        machine row as terminated when it sees ``status='terminated'``.
        """
        try:
            from orb.domain.request.request_types import (
                RequestType,
            )

            provider_api = self.resolve_provider_api(request)
            handler = self.get_handler(provider_api)
            is_return = (
                getattr(request, "request_type", None) == RequestType.RETURN
                or getattr(getattr(request, "request_type", None), "value", None) == "return"
            )
            unreadable: set[str] = set()
            if is_return:
                # A return request's own id labels no pod: read the returned pods
                # by the request that created them, in the namespace they live in.
                instances, unreadable, fulfilment = await self._read_return_pods(
                    handler, list(resource_ids or []), request, machine_coordinates
                )
            else:
                check_result = await asyncio.to_thread(handler.check_hosts_status, request)
                instances = list(check_result.instances)
                fulfilment = check_result.fulfilment

            # Only synthesise ``terminated`` entries for IDs that were
            # confirmed submitted at acquire time (recorded in
            # provider_data["pod_names"]).  IDs that were never successfully
            # created must not be closed out as terminated — they surface as
            # ``unknown`` instead, preventing phantom machine rows.
            #
            # Exception: for return requests where ``pod_names`` was never
            # recorded in provider_data (e.g. Job handler stores ``job_name``,
            # not individual pod names; or older acquire paths that pre-date the
            # pod_names field), every ID in ``resource_ids`` was explicitly
            # submitted for deletion so its absence from the cluster confirms
            # deletion.  We only expand the confirmed set when ``pod_names``
            # is entirely absent from provider_data — when it is explicitly set
            # to ``[]`` the acquire confirmed zero pods and we must not falsely
            # advance the return request.
            provider_data: dict[str, Any] = getattr(request, "provider_data", None) or {}
            confirmed_pod_names: set[str] = set(provider_data.get("pod_names") or [])

            # For return requests where pod_names is not recorded at all (key absent),
            # treat the caller-supplied resource_ids as the confirmed set.  This
            # covers the Job handler (which records job_name, not pod names) and
            # any provider_api that does not populate pod_names at acquire time.
            if is_return and "pod_names" not in provider_data:
                confirmed_pod_names = set(resource_ids or [])

            # Derive instance_type from the workload kind so machine rows
            # group and filter correctly by provider_api.
            synthetic_instance_type = f"{self._provider_type}/{provider_api}"

            live_ids = {i.get("instance_id", "") for i in instances}
            # The resource ids of a Deployment, StatefulSet or Job acquire name the
            # controller, not machines: the pods it runs are the machines and the
            # handler reports them. A controller is never a pod to reconcile, so
            # only a pod request or a return (whose ids are pod names) is checked
            # for absent ids.
            names_machines = is_return or self._names_machines(handler, provider_api)
            missing_ids = (
                [
                    mid
                    for mid in (resource_ids or [])
                    if mid and mid not in live_ids and mid not in unreadable
                ]
                if names_machines
                else []
            )
            for mid in missing_ids:
                if confirmed_pod_names and mid in confirmed_pod_names:
                    instances.append(
                        {
                            "instance_id": mid,
                            "resource_id": mid,
                            "instance_type": synthetic_instance_type,
                            "image_id": "unknown",
                            "launch_time": None,
                            "status": "terminated",
                        }
                    )
                else:
                    # ID was not in the confirmed submitted set — treat as
                    # unknown rather than falsely marking it terminated.
                    instances.append(
                        {
                            "instance_id": mid,
                            "resource_id": mid,
                            "instance_type": synthetic_instance_type,
                            "image_id": "unknown",
                            "launch_time": None,
                            "status": "unknown",
                        }
                    )

            if is_return:
                instances.extend(
                    self._unreadable_placeholders(
                        sorted(unreadable), machine_coordinates, synthetic_instance_type
                    )
                )

            metadata: dict[str, Any] = {
                "provider_api": provider_api,
                "fulfilment": fulfilment,
                "instances": instances,
            }

            if is_return:
                # Return is complete when every *confirmed* pod is gone (either
                # seen as live-and-terminated or absent from the list — i.e.
                # in "terminated" synthetic status).  Phantom IDs (status
                # "unknown") are excluded from the completion check so they do
                # not falsely advance the return to Completed.
                non_terminal = [
                    i.get("instance_id", "")
                    for i in instances
                    if i.get("status") not in ("terminated", "unknown")
                ]
                if unreadable:
                    # A failed list proves nothing: never close these out.
                    metadata["unreadable_resource_ids"] = sorted(unreadable)
                all_confirmed_gone = (
                    not non_terminal
                    and not unreadable
                    and any(i.get("status") == "terminated" for i in instances)
                )
                if all_confirmed_gone:
                    completed_ids = [
                        i.get("instance_id", "")
                        for i in instances
                        if i.get("status") == "terminated"
                    ]
                    return Completed(resource_ids=completed_ids, metadata=metadata)
                # Otherwise still draining.
                pending_ids = non_terminal + sorted(unreadable) or list(resource_ids)
                return Accepted(
                    request_id=str(request.request_id),
                    pending_resource_ids=pending_ids,
                    metadata=metadata,
                )

            if fulfilment.state == "in_progress":
                pending = [
                    i.get("instance_id", "")
                    for i in instances
                    if i.get("status") in ("pending", "starting")
                ]
                return Accepted(
                    request_id=str(request.request_id),
                    pending_resource_ids=pending or list(resource_ids),
                    metadata=metadata,
                )

            terminal_ids = [i.get("instance_id", "") for i in instances]
            return Completed(resource_ids=terminal_ids, metadata=metadata)
        except Exception as exc:
            self._logger.error("Kubernetes get_status failed: %s", exc, exc_info=True)
            return Failed(error=str(exc), recoverable=True)

    @staticmethod
    def _unreadable_placeholders(
        machine_ids: list[str],
        machine_coordinates: Optional[dict[str, dict[str, Any]]],
        instance_type: str,
        status: str = "unknown",
    ) -> list[dict[str, Any]]:
        """One placeholder per machine whose pods could not be listed.

        A machine the status sync is never told about is read as gone and closed
        out, so each unreadable machine is reported with a status that changes
        nothing. The sync overlays whatever a read reports onto the stored row,
        so the stored placement is carried back; leaving it out would send the
        next read to the default namespace. Addresses and tags are not carried:
        they are refreshed by the next successful read.
        """
        return [
            {
                "instance_id": mid,
                "resource_id": mid,
                "instance_type": instance_type,
                "image_id": "unknown",
                "launch_time": None,
                "status": status,
                "provider_data": dict(
                    ((machine_coordinates or {}).get(mid) or {}).get("provider_data") or {}
                ),
            }
            for mid in machine_ids
        ]

    def _names_machines(self, handler: K8sHandlerBase, provider_api: str) -> bool:
        """Whether *handler*'s resource ids name machines (see ``resource_ids_name_machines``).

        A handler that does not declare the flag (a test double) is judged by
        the built-in rule: only the Pod kind names machines.
        """
        # getattr: a test double does not declare the flag
        declared = getattr(type(handler), "resource_ids_name_machines", None)
        if isinstance(declared, bool):
            return declared
        return provider_api == KubernetesProviderApi.POD.value

    def build_template_for_request(self, request: Request) -> Template:
        """Resolve the :class:`Template` carried by ``request``.

        The kubernetes provider picks up the template payload from
        ``request.metadata['template']`` (REST/CLI submission shape) and
        falls back to a minimal template assembled from the request fields
        when nothing richer is available.  In every case the result is
        upcast to :class:`K8sTemplate` so the spec builders see the typed
        k8s-specific surface — the fallback path historically built a
        bare ``Template`` and silently dropped any k8s fields.
        """
        from orb.domain.template.template_aggregate import (
            Template as _Template,
        )
        from orb.providers.k8s.domain.template.k8s_template_aggregate import (
            upcast_to_k8s_template,
        )

        template_class = self._template_class
        meta = getattr(request, "metadata", None) or {}
        if isinstance(meta, dict):
            template_payload = meta.get("template")
            if isinstance(template_payload, template_class):
                return template_payload
            if isinstance(template_payload, _Template):
                return upcast_to_k8s_template(template_payload, template_class)
            if isinstance(template_payload, dict):
                return template_class.model_validate(template_payload)
            # ``TemplateDTO`` from the application layer carries the generic
            # fields on the top level and the typed K8s-specific fields under
            # ``provider_config``.  Flatten both into the K8sTemplate.  Done
            # generically (no isinstance against TemplateDTO so the domain
            # layer stays free of infrastructure imports).
            if template_payload is not None and hasattr(template_payload, "model_dump"):
                flat = template_payload.model_dump()
                provider_config = flat.pop("provider_config", None) or {}
                if isinstance(provider_config, dict):
                    # Merge provider_config keys into flat, preferring non-None
                    # provider_config values over None flat values.  The standard
                    # dict.setdefault() only inserts when the key is absent, so it
                    # silently ignores top-level None fields produced by model_dump()
                    # — e.g. flat["namespace"] = None would prevent
                    # provider_config["namespace"] = "custom-ns" from taking effect.
                    # We override flat[key] when it is absent or None, which matches
                    # the intended precedence: explicit top-level fields win; fields
                    # set only in provider_config are promoted.
                    for key, value in provider_config.items():
                        if value is not None and flat.get(key) is None:
                            flat[key] = value
                return template_class.model_validate(flat)

        # Fall back to a minimal template built from the request fields.
        return template_class(
            template_id=str(request.template_id),
            provider_type=self._provider_type,
            provider_api=request.provider_api or self._default_api,
            max_instances=max(int(request.requested_count), 1),
        )


__all__ = ["K8sHandlerRegistry"]
