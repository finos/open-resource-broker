"""``fallback_to_ondemand`` has one default and the usual override chain.

The default is ``template.default_fallback_to_ondemand`` (false). Provider-type
and provider-instance ``template_defaults``, the template row and a per-request
override each win over the layers below. AWS RunInstances and OCI both read the
resolved core field; OCI defines no default of its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import GetTemplateQuery
from orb.application.queries.template_query_handlers import GetTemplateHandler
from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.application.services.template_overrides import (
    OVERRIDABLE_FIELDS,
    apply_template_overrides,
)
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.oci.configuration.template_extension import OCITemplateExtensionConfig
from orb.providers.oci.mapping.template_mapper import OCITemplateMapper
from tests.providers.aws.template_defaults_support import (
    bundled_aws_template_defaults,
    bundled_global_template_config,
)

pytestmark = pytest.mark.unit

_OCI_TEMPLATES = (
    Path(__file__).resolve().parents[4] / "src/orb/providers/oci/config/oci_templates.json"
)


def _service(
    provider_type: str,
    *,
    global_value: Optional[bool] = None,
    type_value: Optional[bool] = None,
    instance_value: Optional[bool] = None,
) -> TemplateDefaultsService:
    global_config = bundled_global_template_config()
    if global_value is not None:
        global_config["default_fallback_to_ondemand"] = global_value
    type_defaults: dict[str, Any] = (
        bundled_aws_template_defaults()
        if provider_type == "aws"
        else {"provider_api": "OCICompute"}
    )
    if type_value is not None:
        type_defaults["fallback_to_ondemand"] = type_value
    instance_defaults = {} if instance_value is None else {"fallback_to_ondemand": instance_value}
    config_manager = MagicMock()
    config_manager.get_template_config.return_value = global_config
    config_manager.get_provider_config.return_value = SimpleNamespace(
        provider_defaults={provider_type: SimpleNamespace(template_defaults=type_defaults)},
        providers=[
            SimpleNamespace(
                name=f"{provider_type}-default",
                type=provider_type,
                template_defaults=instance_defaults,
            )
        ],
    )
    return TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())


def _resolve(service: TemplateDefaultsService, row: dict[str, Any], provider_type: str) -> Any:
    return service.resolve_template_defaults(
        {"template_id": "t", "provider_type": provider_type, **row}, f"{provider_type}-default"
    )


def test_the_shipped_global_default_is_false() -> None:
    assert bundled_global_template_config()["default_fallback_to_ondemand"] is False
    assert _resolve(_service("aws"), {}, "aws")["fallback_to_ondemand"] is False


@pytest.mark.parametrize(
    ("layers", "row", "expected"),
    [
        ({"global_value": True}, {}, True),
        ({"global_value": False, "type_value": True}, {}, True),
        ({"global_value": False, "type_value": True, "instance_value": False}, {}, False),
        (
            {"global_value": False, "type_value": True, "instance_value": False},
            {"fallback_to_ondemand": True},
            True,
        ),
    ],
)
@pytest.mark.parametrize("provider_type", ["aws", "oci"])
def test_each_layer_wins_over_the_layers_below(
    provider_type: str, layers: dict[str, Any], row: dict[str, Any], expected: bool
) -> None:
    resolved = _resolve(_service(provider_type, **layers), row, provider_type)
    assert resolved["fallback_to_ondemand"] is expected


def test_a_request_override_wins_over_the_template() -> None:
    assert "fallback_to_ondemand" in OVERRIDABLE_FIELDS
    service = _service("aws", type_value=True)
    factory = TemplateFactory()
    factory.register_provider_template_class("aws", AWSTemplate)
    dto = TemplateDTOFactory().from_domain(
        factory.create_template(_resolve(service, {"fallback_to_ondemand": True}, "aws"))
    )
    assert dto.fallback_to_ondemand is True

    overridden = apply_template_overrides(dto, {"fallback_to_ondemand": False})
    assert overridden.fallback_to_ondemand is False


@pytest.mark.asyncio
async def test_get_template_carries_the_resolved_value() -> None:
    service = _service("aws", global_value=False, type_value=True)
    factory = TemplateFactory()
    factory.register_provider_template_class("aws", AWSTemplate)
    stored = TemplateDTOFactory().from_domain(
        factory.create_template(_resolve(service, {"provider_api": "RunInstances"}, "aws"))
    )
    port = MagicMock()
    port.get_template_by_id = AsyncMock(return_value=stored)
    handler = GetTemplateHandler(
        logger=MagicMock(),
        error_handler=MagicMock(),
        template_manager=port,
        template_defaults_service=service,
        template_factory=factory,
        template_dto_factory=TemplateDTOFactory(),
    )

    dto = await handler.execute_query(
        GetTemplateQuery(template_id="t", provider_name="aws-default")
    )

    assert dto.fallback_to_ondemand is True


def test_oci_defines_no_default_of_its_own() -> None:
    assert "fallback_to_ondemand" not in OCITemplateExtensionConfig.model_fields


@pytest.mark.parametrize("value", [True, False])
def test_oci_launch_payload_carries_the_resolved_value(value: bool) -> None:
    resolved = _resolve(
        _service("oci", global_value=value),
        {
            "compartment_id": "ocid1.compartment.oc1..c",
            "subnet_ids": ["ocid1.subnet.oc1..s"],
            "machine_image": "ocid1.image.oc1..i",
            "machine_types": {"VM.Standard.E6.Flex": 1},
            "capacity_type": "preemptible",
        },
        "oci",
    )
    payload = OCITemplateMapper.build_launch_payload(resolved, "worker")
    assert payload["fallback_to_ondemand"] is value


def test_bundled_oci_examples_follow_the_provider_type_default() -> None:
    rows = json.loads(_OCI_TEMPLATES.read_text())["templates"]
    service = _service("oci", global_value=False, type_value=True)
    explicit = [r["template_id"] for r in rows if "fallback_to_ondemand" in r]
    # Only the example that demonstrates the feature sets it.
    assert len(explicit) == 1
    for row in rows:
        resolved = service.resolve_template_defaults(dict(row), "oci-default")
        assert resolved["fallback_to_ondemand"] is True, row["template_id"]
