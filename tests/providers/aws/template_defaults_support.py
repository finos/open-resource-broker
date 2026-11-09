"""Template defaults built from the bundled AWS configuration, for tests.

Builds a real ``TemplateDefaultsService`` over the ``template_defaults`` block
shipped in ``aws_defaults.json`` and the global ``default_price_type``, so a
test resolves a template row exactly as a load does.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional
from unittest.mock import MagicMock

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.providers.aws.provider_plugin import AWSPlugin

AWS_DEFAULTS_PATH = (
    Path(__file__).resolve().parents[3] / "src/orb/providers/aws/config/aws_defaults.json"
)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "src/orb/config/default_config.json"


def bundled_aws_template_defaults() -> dict[str, Any]:
    """The ``provider_defaults.aws.template_defaults`` block shipped with ORB."""
    data = json.loads(AWS_DEFAULTS_PATH.read_text())
    return dict(data["provider"]["provider_defaults"]["aws"]["template_defaults"])


def bundled_global_template_config() -> dict[str, Any]:
    """The global ``template`` block shipped with ORB."""
    data = json.loads(DEFAULT_CONFIG_PATH.read_text())
    return dict(data["template"])


class _Registry:
    """Provider registry stand-in that serves the AWS plugin's registration values."""

    def __init__(self) -> None:
        self._plugin = AWSPlugin()

    def get_default_api(self, provider_type: str) -> Optional[str]:
        return None

    def get_template_row_migration(self, provider_type: str) -> Optional[Callable[..., Any]]:
        if provider_type == "aws":
            return self._plugin.template_row_migration()
        return None


def make_defaults_service(
    *,
    type_defaults: Optional[dict[str, Any]] = None,
    instance_defaults: Optional[dict[str, Any]] = None,
    global_config: Optional[dict[str, Any]] = None,
    instance_name: str = "aws-default",
    logger: Any = None,
) -> TemplateDefaultsService:
    """A defaults service over the bundled layers, with optional overrides per layer."""
    config_manager = MagicMock()
    config_manager.get_template_config.return_value = (
        bundled_global_template_config() if global_config is None else global_config
    )
    provider_config = SimpleNamespace(
        provider_defaults={
            "aws": SimpleNamespace(
                template_defaults=bundled_aws_template_defaults()
                if type_defaults is None
                else type_defaults
            )
        },
        providers=[
            SimpleNamespace(
                name=instance_name, type="aws", template_defaults=dict(instance_defaults or {})
            )
        ],
    )
    config_manager.get_provider_config.return_value = provider_config
    return TemplateDefaultsService(
        config_manager=config_manager,
        logger=logger or MagicMock(),
        provider_registry=_Registry(),  # type: ignore[arg-type]
    )


class DefaultSpecService:
    """Native spec service that renders only the bundled default specs."""

    def __init__(self) -> None:
        from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer
        from orb.providers.aws.infrastructure.services.aws_native_spec_service import (
            AWSNativeSpecService,
        )

        native = MagicMock()
        native.spec_renderer = JinjaSpecRenderer(MagicMock())
        self._service = AWSNativeSpecService(native, MagicMock())

    def process_provider_api_spec_with_merge(self, *_: Any, **__: Any) -> None:
        return None

    def render_default_spec(self, spec_type: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._service.render_default_spec(spec_type, context)


def fake_request(requested_count: int = 4) -> Any:
    """A request stand-in carrying the fields the payload builders read."""
    request = MagicMock()
    request.request_id = "req-00000000-0000-0000-0000-000000000001"
    request.requested_count = requested_count
    request.metadata = {}
    return request


def _for_api(template: Any, api: str) -> Any:
    """The same template validated for another provider API."""
    return type(template).model_validate({**template.model_dump(), "provider_api": api})


def fleet_payloads(template: Any, request: Any = None) -> dict[str, tuple[dict, dict]]:
    """``{api: (legacy payload, default-spec payload)}`` for the fleet and ASG builders."""
    from orb.providers.aws.infrastructure.handlers.asg.config_builder import ASGConfigBuilder
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.config_builder import (
        EC2FleetConfigBuilder,
    )
    from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
        SpotFleetConfigBuilder,
    )

    request = request or fake_request()
    spec = DefaultSpecService()
    payloads: dict[str, tuple[dict, dict]] = {}
    for api, builder_cls in (
        ("EC2Fleet", EC2FleetConfigBuilder),
        ("SpotFleet", SpotFleetConfigBuilder),
    ):
        fleet_template = _for_api(template, api)
        legacy = builder_cls(None, MagicMock(), MagicMock()).build(
            fleet_template, request, "lt-1", "1"
        )
        rendered = builder_cls(spec, MagicMock(), MagicMock()).build(
            fleet_template, request, "lt-1", "1"
        )
        payloads[api] = (legacy, rendered)
    asg_template = _for_api(template, "ASG")
    payloads["ASG"] = (
        ASGConfigBuilder(None, MagicMock(), MagicMock()).build(
            "asg-1", asg_template, request, "lt-1", "1"
        ),
        ASGConfigBuilder(spec, MagicMock(), MagicMock()).build(
            "asg-1", asg_template, request, "lt-1", "1"
        ),
    )
    return payloads


def strategy_options(api: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The allocation-strategy options a payload carries, by wire key."""
    if api == "EC2Fleet":
        return {
            "spot": (payload.get("SpotOptions") or {}).get("AllocationStrategy"),
            "on_demand": (payload.get("OnDemandOptions") or {}).get("AllocationStrategy"),
            "has_spot_options": "SpotOptions" in payload,
        }
    if api == "SpotFleet":
        return {
            "spot": payload.get("AllocationStrategy"),
            "on_demand": payload.get("OnDemandAllocationStrategy"),
        }
    distribution = (payload.get("MixedInstancesPolicy") or {}).get("InstancesDistribution") or {}
    return {
        "spot": distribution.get("SpotAllocationStrategy"),
        "on_demand": distribution.get("OnDemandAllocationStrategy"),
    }
