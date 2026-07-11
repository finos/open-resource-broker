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
from typing import TYPE_CHECKING, Any, Callable, Optional

from orb.domain.base.operation_outcome import Accepted, Completed, Failed, OperationOutcome
from orb.domain.base.ports import LoggingPort
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler
from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler
from orb.providers.k8s.infrastructure.k8s_client import K8sClient
from orb.providers.k8s.value_objects import KubernetesProviderApi
from orb.providers.k8s.watch.multi_namespace import MultiNamespaceWatcher
from orb.providers.k8s.watch.node_state_cache import K8sNodeStateCache

if TYPE_CHECKING:  # pragma: no cover — type-checking only
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
        self._handler_classes: dict[str, type[K8sHandlerBase]] = dict(self._DEFAULT_HANDLER_CLASSES)
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
        return KubernetesProviderApi.POD.value

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

        handler_class = self._handler_classes.get(provider_api)
        if handler_class is not None:
            handler = handler_class(
                kubernetes_client=self._client_provider(),
                config=self._config,
                logger=self._logger,
                max_retries=self._config.max_retries,
                base_delay=self._config.retry_base_delay,
                max_delay=self._config.retry_max_delay,
                circuit_breaker_failure_threshold=self._config.circuit_breaker_failure_threshold,
                circuit_breaker_reset_timeout=self._config.circuit_breaker_reset_timeout,
                pod_state_cache=cache,
                cache_alive=alive,
                native_spec_service=native_spec_service,
                node_state_cache=node_cache,
                metrics=self._metrics_provider() if self._metrics_provider is not None else None,
            )
            self._handlers[provider_api] = handler
            return handler

        # Plugin-supplied handlers — see ``K8sProviderStrategy.register_handler``
        # and ``docs/root/providers/k8s/plugin-authoring.md``.
        # Factories receive the full kwarg surface so plugins that consume
        # ``native_spec_service`` or ``node_state_cache`` do not silently receive
        # ``None``.  The circuit-breaker and retry knobs are forwarded from config
        # so plugin handlers benefit from operator-tuned resilience settings too.
        factory = self._plugin_factories().get(provider_api)
        if factory is not None:
            handler = factory(
                kubernetes_client=self._client_provider(),
                config=self._config,
                logger=self._logger,
                max_retries=self._config.max_retries,
                base_delay=self._config.retry_base_delay,
                max_delay=self._config.retry_max_delay,
                circuit_breaker_failure_threshold=self._config.circuit_breaker_failure_threshold,
                circuit_breaker_reset_timeout=self._config.circuit_breaker_reset_timeout,
                pod_state_cache=cache,
                cache_alive=alive,
                native_spec_service=native_spec_service,
                node_state_cache=node_cache,
                metrics=self._metrics_provider() if self._metrics_provider is not None else None,
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
    ) -> list[Any]:
        """Return example templates contributed by every registered handler.

        Iterates the built-in :attr:`_HANDLER_CLASSES` dict plus any plugin-
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
        sources: dict[str, Any] = {**cls._DEFAULT_HANDLER_CLASSES, **(plugin_factories or {})}
        for handler_target in sources.values():
            getter = getattr(handler_target, "get_example_templates", None)
            if getter is None:
                continue
            try:
                examples.extend(getter())
            except Exception:
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

    async def return_machines(self, machine_ids: list[str], request: Request) -> OperationOutcome:
        """Delete the named pods via the per-API handler."""
        try:
            provider_api = self.resolve_provider_api(request)
            handler = self.get_handler(provider_api)
            provider_data: dict[str, Any] = dict(getattr(request, "provider_data", None) or {})
            provider_data.setdefault("request_id", str(request.request_id))
            release_result = await handler.release_hosts(list(machine_ids), provider_data)
            # release_hosts returns a dict with ``deleted`` and
            # ``failed_deletes`` when partial failure occurred.  The caller
            # only needs the successfully deleted IDs as pending_resource_ids
            # so the status-poll path can observe them draining.
            release_info: dict[str, Any] = (
                release_result if isinstance(release_result, dict) else {}
            )
            deleted = release_info.get("deleted", list(machine_ids))
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

    async def get_status(self, resource_ids: list[str], request: Request) -> OperationOutcome:
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

            is_return = (
                getattr(request, "request_type", None) == RequestType.RETURN
                or getattr(getattr(request, "request_type", None), "value", None) == "return"
            )

            # For return requests where pod_names is not recorded at all (key absent),
            # treat the caller-supplied resource_ids as the confirmed set.  This
            # covers the Job handler (which records job_name, not pod names) and
            # any provider_api that does not populate pod_names at acquire time.
            if is_return and "pod_names" not in provider_data:
                confirmed_pod_names = set(resource_ids or [])

            # Derive instance_type from the workload kind so machine rows
            # group and filter correctly by provider_api.
            api_to_instance_type: dict[str, str] = {
                "Pod": "k8s/Pod",
                "Deployment": "k8s/Deployment",
                "StatefulSet": "k8s/StatefulSet",
                "Job": "k8s/Job",
            }
            synthetic_instance_type = api_to_instance_type.get(provider_api, f"k8s/{provider_api}")

            live_ids = {i.get("instance_id", "") for i in instances}
            missing_ids = [mid for mid in (resource_ids or []) if mid and mid not in live_ids]
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
                all_confirmed_gone = not non_terminal and any(
                    i.get("status") == "terminated" for i in instances
                )
                if all_confirmed_gone:
                    completed_ids = [
                        i.get("instance_id", "")
                        for i in instances
                        if i.get("status") == "terminated"
                    ]
                    return Completed(resource_ids=completed_ids, metadata=metadata)
                # Otherwise still draining.
                pending_ids = non_terminal or list(resource_ids)
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
            K8sTemplate,
            upcast_to_k8s_template,
        )

        meta = getattr(request, "metadata", None) or {}
        if isinstance(meta, dict):
            template_payload = meta.get("template")
            if isinstance(template_payload, K8sTemplate):
                return template_payload
            if isinstance(template_payload, _Template):
                return upcast_to_k8s_template(template_payload)
            if isinstance(template_payload, dict):
                return K8sTemplate.model_validate(template_payload)
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
                return K8sTemplate.model_validate(flat)

        # Fall back to a minimal K8sTemplate built from the request fields.
        return K8sTemplate(
            template_id=str(request.template_id),
            provider_type="k8s",
            provider_api=request.provider_api or KubernetesProviderApi.POD.value,
            max_instances=max(int(request.requested_count), 1),
        )


__all__ = ["K8sHandlerRegistry"]
