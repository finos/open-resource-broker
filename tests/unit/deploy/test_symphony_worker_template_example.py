"""The template example on the Symphony worker docs page renders a working pod body.

The example is loaded the way ORB loads template files (file dict -> domain
template -> TemplateDTO -> get-template query -> domain template) and then
rendered through the k8s native-spec service, so the assertions hold for what
Kubernetes would actually receive.
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, Mock

import pytest

from orb.application.dto.queries import GetTemplateQuery
from orb.application.dto.template import TemplateDTO
from orb.application.queries.template_query_handlers import GetTemplateHandler
from orb.application.services.native_spec_service import NativeSpecService
from orb.domain.base.ports.error_handling_port import ErrorHandlingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.domain.template.k8s_template_dto_config import K8sTemplateDTOConfig
from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
    K8sNativeSpecService,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = REPO_ROOT / "docs" / "root" / "deployment" / "symphony-worker.md"

DOWNWARD_API_ENV = {
    "MY_POD_NAME": "metadata.name",
    "MY_POD_IP": "status.podIP",
    "MY_POD_NAMESPACE": "metadata.namespace",
}
REQUIRED_LITERAL_ENV = {
    "SYMPHONY_SVC_VCPU",
    "SYMPHONY_SVC_MEMORY",
    "EGO_IGNORE_CPU_CGROUP_SETTING_IN_CONTAINER",
    "EGO_LIM_IS_IN_CONTAINER",
}


def _documented_template() -> dict[str, Any]:
    section = DOC.read_text().split("## Template example", 1)[1]
    blocks = re.findall(r"```json\n(.*?)```", section, re.S)
    templates = [json.loads(b) for b in blocks if '"template_id"' in b]
    assert len(templates) == 1, "expected exactly one template in the Template example section"
    return templates[0]


class _FakeTemplateManager:
    def __init__(self, template_dto: TemplateDTO) -> None:
        self._template = template_dto

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:
        return self._template if template_id == self._template.template_id else None


@pytest.fixture
def k8s_template_factory(monkeypatch: pytest.MonkeyPatch) -> TemplateFactory:
    extensions = dict(TemplateExtensionRegistry._extensions)
    extensions["k8s"] = K8sTemplateDTOConfig
    monkeypatch.setattr(TemplateExtensionRegistry, "_extensions", extensions)
    factory = TemplateFactory()
    factory.register_provider_template_class("k8s", K8sTemplate)
    return factory


async def _load_through_template_pipeline(
    raw: dict[str, Any], factory: TemplateFactory
) -> K8sTemplate:
    config = MagicMock()
    config.app_config.scheduler.type = "default"
    config.get_provider_config.return_value = None
    manager = TemplateConfigurationManager(
        config_manager=config,
        scheduler_strategy=MagicMock(),
        logger=MagicMock(),
        cache_service=MagicMock(),
        storage_service=MagicMock(),
        template_factory=factory,
    )
    template_dto = manager._convert_dict_to_template_dto(raw)

    defaults_service = Mock()
    defaults_service.resolve_template_defaults.side_effect = lambda data, **_: data
    handler = GetTemplateHandler(
        logger=Mock(),
        error_handler=Mock(spec=ErrorHandlingPort),
        template_manager=_FakeTemplateManager(template_dto),
        template_defaults_service=defaults_service,
        template_factory=factory,
        template_dto_factory=TemplateDTOFactory(),
    )
    resolved_dto = await handler.execute_query(GetTemplateQuery(template_id=raw["template_id"]))

    # The provider builds its domain template from the resolved DTO.
    data = resolved_dto.model_dump()
    data.setdefault("provider_api", resolved_dto.provider_api)
    template = factory.create_template(data)
    assert isinstance(template, K8sTemplate)
    return template


def _native_spec_service() -> K8sNativeSpecService:
    logger = Mock()
    app_config = Mock()
    app_config.get_native_spec_config.return_value = {"enabled": True}
    app_service = NativeSpecService(
        config_port=app_config, spec_renderer=JinjaSpecRenderer(logger=logger), logger=logger
    )
    port = Mock()
    port.get_package_info.return_value = {"name": "orb", "version": "test"}
    return K8sNativeSpecService(
        native_spec_service=app_service,
        config_port=port,
        k8s_config=K8sProviderConfig(namespace="symphony", native_spec_enabled=True),
    )


def _request(template_id: str) -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Pod",
        template_id=template_id,
        requested_count=1,
        provider_data={"namespace": "symphony"},
    )


@pytest.mark.asyncio
async def test_documented_template_renders_downward_api_env_and_probe(
    k8s_template_factory: TemplateFactory,
) -> None:
    raw = _documented_template()
    template = await _load_through_template_pipeline(raw, k8s_template_factory)

    body = _native_spec_service().process_pod_spec(
        template, _request(template.template_id), namespace="symphony"
    )

    assert body is not None
    containers = body["spec"]["containers"]
    assert len(containers) == 1
    container = containers[0]
    assert container["image"] == "registry.example.com/symphony-worker:7.3.2"

    env = {entry["name"]: entry for entry in container["env"]}
    for name, field_path in DOWNWARD_API_ENV.items():
        assert env[name]["valueFrom"] == {"fieldRef": {"fieldPath": field_path}}
    assert REQUIRED_LITERAL_ENV <= set(env)

    probe = container["livenessProbe"]
    assert probe["exec"]["command"] == ["/opt/orb-worker/bin/compute-liveness"]
    assert body["metadata"]["labels"]["orb.io/managed"] == "true"


@pytest.mark.asyncio
async def test_template_env_field_does_not_carry_value_from(
    k8s_template_factory: TemplateFactory,
) -> None:
    """The docs page says ``env`` keeps only literal values; update it if this changes."""
    raw = {
        "template_id": "env-only",
        "provider_type": "k8s",
        "provider_api": "Pod",
        "machine_image": "busybox:latest",
        "env": [
            {"name": "LITERAL", "value": "x"},
            {"name": "MY_POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
        ],
    }
    template = await _load_through_template_pipeline(raw, k8s_template_factory)

    names = {entry.name for entry in template.env or []}
    assert names == {"LITERAL"}


def test_documented_template_uses_current_field_names() -> None:
    raw = _documented_template()
    assert {"machine_image", "max_machines"} <= set(raw)
    assert not {"image_id", "max_instances"} & set(raw)
