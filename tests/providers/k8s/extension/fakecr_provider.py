"""A provider type built on the Kubernetes provider's hooks, serving a fake custom resource.

``fakecr`` creates ``widgets.fakecr.example.dev`` objects instead of pods.  It
reuses the Kubernetes client, handler base, strategy dispatch and registration
helpers, and overrides only what differs.  It is registered the way an in-tree
provider is (see :func:`build_registration_module`) but sits outside
``KNOWN_PROVIDERS`` because it only exists inside the tests.
"""

from __future__ import annotations

import functools
import json
import types
from importlib.machinery import ModuleSpec
from importlib.resources import files
from typing import Any, ClassVar, Optional

from pydantic_settings import SettingsConfigDict

from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.providers.base.config_input import provider_config_mapping
from orb.providers.base.provider_plugin import ProviderPlugin
from orb.providers.base.strategy import ProviderCapabilities, ProviderOperationType
from orb.providers.k8s.adapters.template_example_generator_adapter import (
    KubernetesTemplateExampleGeneratorAdapter,
)
from orb.providers.k8s.cli.k8s_cli_spec import K8sCLISpec
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.domain.template.k8s_template_dto_config import K8sTemplateDTOConfig
from orb.providers.k8s.infrastructure.handlers.base_handler import K8sHandlerBase, PodReadResult
from orb.providers.k8s.registration import create_k8s_strategy
from orb.providers.k8s.scheduler.hostfactory_field_mapping import K8sFieldMapping
from orb.providers.k8s.services.capability_service import K8sCapabilityService
from orb.providers.k8s.services.instance_operation_service import CancelKind
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy
from orb.providers.k8s.utilities.labels import instance_label_key

PROVIDER_TYPE = "fakecr"
GROUP = "fakecr.example.dev"
VERSION = "v1"
PLURAL = "widgets"
LABEL_PREFIX = "fakecr.example.dev"


class FakeCrConfig(K8sProviderConfig):
    """Own env prefix and label prefix, so no object it creates matches the k8s labels."""

    model_config = SettingsConfigDict(  # type: ignore[assignment]
        env_prefix="ORB_FAKECR_",
        case_sensitive=False,
        populate_by_name=True,
        env_nested_delimiter="__",
        extra="forbid",
    )

    provider_type: str = PROVIDER_TYPE
    label_prefix: str = LABEL_PREFIX


class FakeCrTemplate(K8sTemplate):
    DEFAULT_PROVIDER_TYPE: ClassVar[str] = PROVIDER_TYPE


class FakeCrTemplateDTOConfig(K8sTemplateDTOConfig):
    """Template extension container registered for the type."""


class FakeCrHandler(K8sHandlerBase):
    """Creates, reads and deletes ``Widget`` custom objects; names its machines."""

    PROVIDER_API = "Widget"
    resource_ids_name_machines = True
    timeout_delete_enabled = False

    def _labels(self, request_id: str) -> dict[str, str]:
        prefix = self._config.label_prefix
        return {
            f"{prefix}/managed": "true",
            f"{prefix}/request-id": request_id,
            instance_label_key(prefix): self._config.orb_instance_id or "",
        }

    def _namespace(self, template: Optional[Any] = None) -> str:
        return (getattr(template, "namespace", None) or self._config.namespace) or "default"

    async def acquire_hosts(self, request: Any, template: Any) -> dict[str, Any]:
        namespace = self._namespace(template)
        request_id = str(request.request_id)
        names = [f"widget-{request_id[:8]}-{i}" for i in range(int(request.requested_count))]
        for name in names:
            self._kubernetes_client.custom_objects_api.create_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                body={
                    "apiVersion": f"{GROUP}/{VERSION}",
                    "kind": "Widget",
                    "metadata": {"name": name, "labels": self._labels(request_id)},
                },
            )
        return {
            "resource_ids": names,
            "machine_ids": names,
            # The registry reconciles machine ids against this confirmed set.
            "provider_data": {"namespace": namespace, "pod_names": names},
        }

    def _list(self, namespace: str, request_id: Optional[str]) -> list[dict[str, Any]]:
        prefix = self._config.label_prefix
        selector = f"{prefix}/request-id={request_id}" if request_id else f"{prefix}/managed=true"
        response = self._kubernetes_client.custom_objects_api.list_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            label_selector=selector,
        )
        return list(response.get("items", []))

    @staticmethod
    def _instance(obj: dict[str, Any]) -> dict[str, Any]:
        name = obj["metadata"]["name"]
        return {
            "instance_id": name,
            "resource_id": name,
            "name": name,
            "instance_type": f"{PROVIDER_TYPE}/Widget",
            "image_id": "widget:1",
            "launch_time": None,
            "status": "running",
        }

    def check_hosts_status(self, request: Any) -> CheckHostsStatusResult:
        namespace = (getattr(request, "provider_data", None) or {}).get("namespace")
        objects = self._list(namespace or self._namespace(), str(request.request_id))
        instances = [self._instance(obj) for obj in objects]
        wanted = int(request.requested_count)
        state = "fulfilled" if len(instances) >= wanted else "in_progress"
        return CheckHostsStatusResult(
            instances=instances,
            fulfilment=ProviderFulfilment(
                state=state,  # type: ignore[arg-type]
                message=f"{len(instances)} of {wanted} widgets",
                target_units=wanted,
                running_count=len(instances),
                pending_count=max(wanted - len(instances), 0),
            ),
        )

    def read_pods_for_return(
        self, namespace: str, origin_request_id: Optional[str], names: list[str]
    ) -> PodReadResult:
        wanted = set(names)
        try:
            objects = self._list(namespace, origin_request_id)
        except Exception:
            return PodReadResult(unreadable=wanted)
        return PodReadResult(
            instances=[self._instance(obj) for obj in objects if obj["metadata"]["name"] in wanted]
        )

    async def release_hosts(self, machine_ids: list[str], provider_data: dict[str, Any]) -> dict:  # type: ignore[override]
        namespace = provider_data.get("namespace") or self._namespace()
        for name in machine_ids:
            self._kubernetes_client.custom_objects_api.delete_namespaced_custom_object(
                group=GROUP, version=VERSION, namespace=namespace, plural=PLURAL, name=name
            )
        return {"deleted": list(machine_ids), "failed_deletes": []}

    @classmethod
    def get_example_templates(cls) -> list[Any]:
        return [
            FakeCrTemplate(
                template_id="fakecr-widget-example",
                provider_api="Widget",
                machine_image="widget:1",
                max_machines=2,
            )
        ]


class FakeCrCapabilityService(K8sCapabilityService):
    def _build_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=PROVIDER_TYPE,
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.CANCEL_PROVISIONING,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.HEALTH_CHECK,
            ],
            supported_apis=["Widget"],
            features={"selective_termination": True, "watch_supported": False},
        )


def _list_widget_names(client: Any, namespace: str, selector: str) -> list[str]:
    response = client.custom_objects_api.list_namespaced_custom_object(
        group=GROUP, version=VERSION, namespace=namespace, plural=PLURAL, label_selector=selector
    )
    return [item["metadata"]["name"] for item in response.get("items", [])]


def _delete_widget(client: Any, namespace: str, name: str) -> None:
    client.custom_objects_api.delete_namespaced_custom_object(
        group=GROUP, version=VERSION, namespace=namespace, plural=PLURAL, name=name
    )


class FakeCrStrategy(K8sProviderStrategy):
    """Widgets instead of pods; no pod-based background services."""

    _PROVIDER_TYPE = PROVIDER_TYPE
    _CONFIG_CLASS = FakeCrConfig
    _DEFAULTS_RESOURCE = ("tests.providers.k8s.extension", "fakecr_defaults.json")
    _DEFAULT_API = "Widget"
    _API_ALIASES: ClassVar[dict[str, str]] = {"widget": "Widget"}
    _HANDLER_CLASSES = {"Widget": FakeCrHandler}  # type: ignore[assignment]
    _TEMPLATE_CLASS = FakeCrTemplate
    _CAPABILITY_SERVICE_CLASS = FakeCrCapabilityService
    # The pod reconciler, watch fleet and node watchers all read pods and nodes;
    # orphan collection over custom resources is not available to this type.
    _DAEMON_SERVICES = frozenset()
    _CANCEL_KINDS = (CancelKind("Widget", _list_widget_names, _delete_widget),)
    _RESOURCE_NAME_KEYS = ()
    _HEALTH_CHECK_NAME = "fakecr_api"

    def get_supported_apis(self) -> list[str]:
        return ["Widget"]


class FakeCrDefaultsLoader:
    def load_defaults(self) -> dict:
        resource = files("tests.providers.k8s.extension").joinpath("fakecr_defaults.json")
        return json.loads(resource.read_text(encoding="utf-8"))


class FakeCrPlugin(ProviderPlugin):
    provider_name = PROVIDER_TYPE

    def strategy_factory(self) -> Any:
        return functools.partial(
            create_k8s_strategy, strategy_class=FakeCrStrategy, config_class=FakeCrConfig
        )

    def config_factory(self) -> Any:
        return lambda data: FakeCrConfig(**provider_config_mapping(data))

    def strategy_class(self) -> Optional[type]:
        return FakeCrStrategy

    def default_api(self) -> Optional[str]:
        return "Widget"

    def display_name(self) -> Optional[str]:
        return "Fake custom resource"

    def provider_settings_class(self) -> Optional[type]:
        return FakeCrConfig

    def template_dto_config(self) -> Any:
        return FakeCrTemplateDTOConfig

    def template_class(self) -> Optional[type]:
        return FakeCrTemplate

    def cli_spec(self) -> Any:
        return K8sCLISpec()

    def field_mapping(self) -> Any:
        return K8sFieldMapping()

    def defaults_loader(self) -> Any:
        return FakeCrDefaultsLoader()

    def template_example_generator(self, container: Any) -> Any:
        return KubernetesTemplateExampleGeneratorAdapter(strategy_class=FakeCrStrategy)


_plugin = FakeCrPlugin()


def build_registration_module() -> types.ModuleType:
    """The ``orb.providers.fakecr.registration`` module an in-tree provider would ship.

    It carries a real ``__spec__``: the bootstrap loops call ``find_spec`` and
    silently skip a module without one.
    """
    module = types.ModuleType(f"orb.providers.{PROVIDER_TYPE}.registration")
    module.__spec__ = ModuleSpec(module.__name__, loader=None)

    def register_fakecr_provider(registry: Any = None, logger: Any = None, **kw: Any) -> None:
        _plugin.register_provider(registry, logger, **kw)

    def initialize_fakecr_provider(template_factory: Any = None, logger: Any = None) -> None:
        _plugin.initialize_provider(template_factory, logger)

    def register_fakecr_services_with_di(container: Any) -> None:
        _plugin.register_services_with_di(container)

    def register_fakecr_template_factory(factory: Any, logger: Any = None) -> None:
        factory.register_provider_template_class(PROVIDER_TYPE, FakeCrTemplate)

    module.register_fakecr_provider = register_fakecr_provider  # type: ignore[attr-defined]
    module.initialize_fakecr_provider = initialize_fakecr_provider  # type: ignore[attr-defined]
    module.register_fakecr_services_with_di = register_fakecr_services_with_di  # type: ignore[attr-defined]
    module.register_fakecr_template_factory = register_fakecr_template_factory  # type: ignore[attr-defined]
    return module


def build_package_module() -> types.ModuleType:
    package = types.ModuleType(f"orb.providers.{PROVIDER_TYPE}")
    package.__path__ = []  # type: ignore[attr-defined]
    package.__spec__ = ModuleSpec(package.__name__, loader=None, is_package=True)
    return package
