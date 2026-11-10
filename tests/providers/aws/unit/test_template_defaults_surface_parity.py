"""Every interface reports the template values that default resolution produces.

One template file is loaded through the real HostFactory and default scheduler
loads (``load_templates_from_path`` and ``TemplateConfigurationManager``) over
a matrix of price types and of the layer that sets the values. The effective
``(price_type, allocation_strategy, allocation_strategy_on_demand,
fallback_to_ondemand)`` must be the same in:

- the scheduler load and the scheduler output formatting;
- GetTemplate, and the AWS template built from its DTO;
- the get-template operation as the CLI, REST, MCP and SDK render it, and the
  REST route itself;
- CreateRequest, through the AWS create path, as rendered into the EC2 Fleet,
  Spot Fleet, ASG and RunInstances payloads, legacy and spec.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.application.dto.queries import GetTemplateQuery, ValidateTemplateQuery
from orb.application.queries.template_query_handlers import (
    GetTemplateHandler,
    ValidateTemplateHandler,
)
from orb.application.services.orchestration.dtos import GetTemplateInput
from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
from orb.application.services.template_overrides import apply_template_overrides
from orb.domain.base.exceptions import ValidationError
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.adapters.template_configuration_adapter import (
    TemplateConfigurationAdapter,
)
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.response_formatting_service import ResponseFormattingService
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.scheduler.hostfactory_field_mapping import AWSFieldMapping
from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from tests.providers.aws.template_defaults_support import (
    DefaultSpecService,
    bundled_aws_template_defaults,
    fake_request,
    fleet_payloads,
    make_defaults_service,
    strategy_options,
)

pytestmark = pytest.mark.unit

PCO = "priceCapacityOptimized"
TEMPLATE_ID = "parity"

# HostFactory spelling -> snake_case spelling of the row fields.
_HF_KEYS = {
    "allocation_strategy": "allocationStrategy",
    "allocation_strategy_on_demand": "allocationStrategyOnDemand",
    "fallback_to_ondemand": "fallbackToOndemand",
    "price_type": "priceType",
}


@dataclass
class Case:
    """One cell of the matrix and the values every interface must report."""

    name: str
    row: dict[str, Any]
    expected: tuple[Any, Any, Any, Any]
    type_defaults: Optional[dict[str, Any]] = None
    instance_defaults: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)
    # What CreateRequest renders when a request override applies.
    expected_request: Optional[tuple[Any, Any, Any, Any]] = None
    legacy: bool = False


def _type_defaults(**changes: Any) -> dict[str, Any]:
    defaults = bundled_aws_template_defaults()
    for key, value in changes.items():
        if value is None:
            defaults.pop(key, None)
        else:
            defaults[key] = value
    return defaults


def _matrix() -> list[Case]:
    cases: list[Case] = []
    for price in ("spot", "ondemand", "heterogeneous"):
        row = {"price_type": price}
        cases += [
            Case(f"{price}-none", dict(row), (price, PCO, "lowestPrice", False)),
            Case(
                f"{price}-type",
                dict(row),
                (price, "capacityOptimized", "prioritized", True),
                type_defaults=_type_defaults(
                    allocation_strategy="capacityOptimized",
                    allocation_strategy_on_demand="prioritized",
                    fallback_to_ondemand=True,
                ),
            ),
            Case(
                f"{price}-instance",
                dict(row),
                (price, "diversified", "prioritized", True),
                instance_defaults={
                    "allocation_strategy": "diversified",
                    "allocation_strategy_on_demand": "prioritized",
                    "fallback_to_ondemand": True,
                },
            ),
            Case(
                f"{price}-template",
                {
                    **row,
                    "allocation_strategy": "capacityOptimizedPrioritized",
                    "allocation_strategy_on_demand": "prioritized",
                    "fallback_to_ondemand": True,
                },
                (price, "capacityOptimizedPrioritized", "prioritized", True),
                instance_defaults={
                    "allocation_strategy": "diversified",
                    "allocation_strategy_on_demand": "lowestPrice",
                    "fallback_to_ondemand": False,
                },
            ),
        ]
        other = "spot" if price != "spot" else "ondemand"
        cases.append(
            Case(
                f"{price}-request",
                dict(row),
                (price, PCO, "lowestPrice", False),
                overrides={"price_type": other, "fallback_to_ondemand": True},
                expected_request=(other, PCO, "lowestPrice", True),
            )
        )
    # Required cases: legacy on-demand rows, each with and without the
    # type-layer on-demand default.
    for with_default in (True, False):
        suffix = "with-type-on-demand" if with_default else "without-type-on-demand"
        type_defaults = None if with_default else _type_defaults(allocation_strategy_on_demand=None)
        cases += [
            Case(
                f"legacy-ondemand-prioritized-{suffix}",
                {"price_type": "ondemand", "allocation_strategy": "prioritized"},
                ("ondemand", PCO, "prioritized", False),
                type_defaults=type_defaults,
                legacy=True,
            ),
            Case(
                f"legacy-ondemand-lowestprice-spot-request-{suffix}",
                {"price_type": "ondemand", "allocation_strategy": "lowestPrice"},
                ("ondemand", PCO, "lowestPrice", False),
                type_defaults=type_defaults,
                overrides={"price_type": "spot"},
                expected_request=("spot", PCO, "lowestPrice", False),
                legacy=True,
            ),
        ]
    cases += [
        Case(
            "template-on-demand-prioritized-over-type-lowestprice",
            {"price_type": "heterogeneous", "allocation_strategy_on_demand": "prioritized"},
            ("heterogeneous", PCO, "prioritized", False),
        ),
        Case(
            "instance-lowestprice-without-on-demand-default",
            {"price_type": "ondemand"},
            ("ondemand", "lowestPrice", None, False),
            type_defaults=_type_defaults(allocation_strategy_on_demand=None),
            instance_defaults={"allocation_strategy": "lowestPrice"},
        ),
    ]
    return cases


CASES = _matrix()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _aws_factory() -> TemplateFactory:
    factory = TemplateFactory()
    factory.register_provider_template_class("aws", AWSTemplate)
    return factory


def _base_row(case: Case) -> dict[str, Any]:
    row = {
        "template_id": TEMPLATE_ID,
        "provider_type": "aws",
        "provider_api": "EC2Fleet",
        "machine_image": "ami-12345678",
        "max_machines": 4,
        "machine_types": {"t3.micro": 1, "t3.small": 1},
        "subnet_ids": ["subnet-1"],
        "security_group_ids": ["sg-1"],
        "fleet_role": "arn:aws:iam::123456789012:role/spot-fleet",
        **case.row,
    }
    if row["price_type"] == "heterogeneous":
        row["percent_on_demand"] = 50
    return row


def _hf_row(row: dict[str, Any]) -> dict[str, Any]:
    names = {
        "template_id": "templateId",
        "provider_type": "providerType",
        "provider_api": "providerApi",
        "machine_image": "imageId",
        "max_machines": "maxNumber",
        "machine_types": "vmTypes",
        "subnet_ids": "subnetIds",
        "security_group_ids": "securityGroupIds",
        "fleet_role": "fleetRole",
        "percent_on_demand": "percentOnDemand",
        **_HF_KEYS,
    }
    return {names[k]: v for k, v in row.items()}


class Loaded:
    """One scheduler load of the case's template file and the services over it."""

    def __init__(self, case: Case, scheduler: str, tmp_path: Path) -> None:
        self.case = case
        self.defaults = make_defaults_service(
            type_defaults=case.type_defaults, instance_defaults=case.instance_defaults
        )
        row = _base_row(case)
        if scheduler == "hostfactory":
            strategy: Any = HostFactorySchedulerStrategy(template_defaults_service=self.defaults)
            content = {"templates": [_hf_row(row)]}
        else:
            strategy = DefaultSchedulerStrategy(template_defaults_service=self.defaults)
            content = {"templates": [row]}
        strategy._logger = MagicMock()
        path = tmp_path / f"{scheduler}_templates.json"
        path.write_text(json.dumps(content))
        self.scheduler = strategy
        self._patches = [
            patch.object(
                FieldMappingRegistry,
                "get_or_none",
                side_effect=lambda t: AWSFieldMapping() if t == "aws" else None,
            ),
            patch.object(strategy, "_get_active_provider_type", return_value="aws"),
            patch.object(strategy, "_get_provider_name", return_value="aws-default"),
            patch.object(strategy, "get_template_paths", return_value=[str(path)]),
        ]
        for p in self._patches:
            p.start()
        self.manager = TemplateConfigurationManager(
            config_manager=MagicMock(),
            scheduler_strategy=strategy,
            logger=MagicMock(),
            storage_service=MagicMock(),
            template_factory=_aws_factory(),
        )
        self._patches.append(
            patch.object(self.manager, "_batch_resolve_images", AsyncMock(side_effect=lambda d: d))
        )
        self._patches[-1].start()
        self.port = TemplateConfigurationAdapter(self.manager, MagicMock())
        self.get_handler = GetTemplateHandler(
            logger=MagicMock(),
            error_handler=MagicMock(),
            template_manager=self.port,
            template_defaults_service=self.defaults,
            template_factory=_aws_factory(),
            template_dto_factory=TemplateDTOFactory(),
        )

    def stop(self) -> None:
        for p in reversed(self._patches):
            p.stop()

    async def scheduler_dto(self) -> Any:
        dto = await self.manager.get_template_by_id(TEMPLATE_ID)
        assert dto is not None, await self.manager.get_template_rejection(TEMPLATE_ID)
        return dto

    async def get_template(self) -> Any:
        return await self.get_handler.execute_query(
            GetTemplateQuery(template_id=TEMPLATE_ID, provider_name="aws-default")
        )


class _QueryBus:
    def __init__(self, handler: GetTemplateHandler) -> None:
        self._handler = handler

    async def execute(self, query: Any) -> Any:
        return await self._handler.execute_query(query)


def _values(source: Any) -> tuple[Any, Any, Any, Any]:
    if isinstance(source, dict):
        get = source.get
    else:

        def get(key: str) -> Any:
            return getattr(source, key, None)

    on_demand = get("allocation_strategy_on_demand")
    return (
        get("price_type"),
        get("allocation_strategy"),
        getattr(on_demand, "value", on_demand),
        get("fallback_to_ondemand"),
    )


def _hf_values(data: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return tuple(data.get(_HF_KEYS[k]) for k in _HF_KEYS)  # type: ignore[return-value]


def _displayed(loaded: Loaded, data: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    if isinstance(loaded.scheduler, HostFactorySchedulerStrategy):
        return (
            data.get("priceType"),
            data.get("allocationStrategy"),
            data.get("allocationStrategyOnDemand"),
            data.get("fallbackToOndemand"),
        )
    return _values(data)


async def _created_template(loaded: Loaded, dto: Any) -> AWSTemplate:
    """The AWS template the create path builds for this request."""
    if loaded.case.overrides:
        dto = apply_template_overrides(dto, loaded.case.overrides)
    captured: dict[str, AWSTemplate] = {}

    async def provision(request: Any, template: AWSTemplate) -> dict[str, Any]:
        captured["template"] = template
        return {"resource_ids": ["r-1"], "instances": []}

    adapter = MagicMock()
    adapter.provision_resources = provision
    service = AWSInstanceOperationService(
        aws_client=MagicMock(),
        logger=MagicMock(),
        provisioning_adapter=adapter,
        machine_adapter=MagicMock(),
    )
    result = await service.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": loaded.scheduler.format_template_for_provider(dto)},
        ),
        {"EC2Fleet": MagicMock()},
    )
    assert result.success, result.error_message
    return captured["template"]


def _payload_values(template: AWSTemplate) -> dict[str, Any]:
    """Wire values of each payload, legacy and spec; asserts the two agree."""
    out: dict[str, Any] = {}
    for api, (legacy, rendered) in fleet_payloads(template).items():
        assert strategy_options(api, legacy) == strategy_options(api, rendered), api
        out[api] = strategy_options(api, legacy)

    run_template = type(template).model_validate(
        {**template.model_dump(), "provider_api": "RunInstances"}
    )
    markets = []
    config_port = MagicMock()
    config_port.get_resource_prefix.return_value = "orb-"
    config_port.get_package_info.return_value = {"name": "open-resource-broker"}
    for spec in (None, DefaultSpecService()):
        handler = RunInstancesHandler(
            aws_client=MagicMock(),
            logger=MagicMock(),
            aws_ops=MagicMock(),
            launch_template_manager=MagicMock(),
            config_port=config_port,
            aws_native_spec_service=spec,
        )
        params = handler._create_run_instances_params(
            aws_template=run_template,
            request=fake_request(),
            launch_template_id="lt-1",
            launch_template_version="1",
        )
        markets.append((params.get("InstanceMarketOptions") or {}).get("MarketType"))
    assert markets[0] == markets[1]
    out["RunInstances"] = markets[0]
    return out


def _expected_payloads(
    expected: tuple[Any, Any, Any, Any], percent_on_demand: Optional[int]
) -> dict[str, Any]:
    price, spot, on_demand, _ = expected
    wire = {
        "capacityOptimized": "capacity-optimized",
        "capacityOptimizedPrioritized": "capacity-optimized-prioritized",
        "diversified": "diversified",
        "lowestPrice": "lowest-price",
        "priceCapacityOptimized": "price-capacity-optimized",
        "prioritized": "prioritized",
    }
    has_spot = price in ("spot", "heterogeneous")
    has_on_demand = price in ("ondemand", "heterogeneous")
    return {
        "EC2Fleet": {
            "spot": wire[spot] if has_spot and spot else None,
            "on_demand": wire[on_demand] if has_on_demand and on_demand else None,
            "has_spot_options": bool(has_spot and spot),
        },
        # Spot Fleet sends the on-demand strategy whenever it requests on-demand capacity.
        "SpotFleet": {
            "spot": spot,
            "on_demand": on_demand if has_on_demand or (percent_on_demand or 0) > 0 else None,
        },
        "ASG": {
            "spot": wire[spot] if has_spot and spot else None,
            "on_demand": wire[on_demand] if has_spot and on_demand else None,
        },
        "RunInstances": "spot" if price == "spot" else None,
    }


@pytest.fixture(params=["hostfactory", "default"])
def scheduler(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(autouse=True)
def aws_dto_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the AWS DTO config as AWS provider registration does."""
    from orb.infrastructure.registry.template_extension_registry import (
        TemplateExtensionRegistry,
    )
    from orb.providers.aws.domain.template.aws_template_dto_config import AWSTemplateDTOConfig

    monkeypatch.setitem(TemplateExtensionRegistry._extensions, "aws", AWSTemplateDTOConfig)


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_every_interface_reports_the_resolved_values(
    case: Case, scheduler: str, tmp_path: Path
) -> None:
    loaded = Loaded(case, scheduler, tmp_path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            dto = await loaded.scheduler_dto()
            got = await loaded.get_template()

        # Scheduler load, its output formatting, GetTemplate and the AWS template.
        assert _values(dto) == case.expected
        assert _displayed(loaded, loaded.scheduler.format_template_for_display(dto)) == (
            case.expected
        )
        assert _values(got) == case.expected
        assert _values(AWSTemplate.model_validate(got.model_dump())) == case.expected

        # The get-template operation on every interface.
        orchestrator = GetTemplateOrchestrator(
            MagicMock(), _QueryBus(loaded.get_handler), MagicMock()
        )
        output = await orchestrator.execute(
            GetTemplateInput(template_id=TEMPLATE_ID, provider_name="aws-default")
        )
        formatter = ResponseFormattingService(loaded.scheduler)
        entry = OPERATION_CATALOG["get_template"]
        for interface in (Interface.CLI, Interface.REST, Interface.MCP, Interface.SDK):
            rendered = entry.renderer_for(interface)(formatter, output).data
            assert _displayed(loaded, rendered) == case.expected, interface

        # CreateRequest through the AWS create path into every payload.
        expected_request = case.expected_request or case.expected
        created = await _created_template(loaded, got)
        assert _values(created) == expected_request
        assert _payload_values(created) == _expected_payloads(
            expected_request, created.percent_on_demand
        )
    finally:
        loaded.stop()


@pytest.mark.asyncio
async def test_rest_route_reports_the_resolved_values(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orb.api.dependencies import get_get_template_orchestrator, get_request_formatter
    from orb.api.routers.templates import router

    case = next(c for c in CASES if c.name == "heterogeneous-instance")
    loaded = Loaded(case, "default", tmp_path)
    try:
        orchestrator = GetTemplateOrchestrator(
            MagicMock(), _QueryBus(loaded.get_handler), MagicMock()
        )
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_get_template_orchestrator] = lambda: orchestrator
        app.dependency_overrides[get_request_formatter] = lambda: ResponseFormattingService(
            loaded.scheduler
        )
        response = TestClient(app).get(f"/templates/{TEMPLATE_ID}")
        assert response.status_code == 200, response.text
        assert _values(response.json()) == case.expected
    finally:
        loaded.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_default", [True, False], ids=["with-type-on-demand", "without"])
async def test_a_legacy_row_warns_once_per_load(
    scheduler: str, with_default: bool, tmp_path: Path
) -> None:
    case = next(
        c
        for c in CASES
        if c.name
        == "legacy-ondemand-prioritized-"
        + ("with-type-on-demand" if with_default else "without-type-on-demand")
    )
    loaded = Loaded(case, scheduler, tmp_path)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            await loaded.scheduler_dto()
            await loaded.get_template()
        deprecations = [
            w
            for w in caught
            if issubclass(w.category, DeprecationWarning) and "on-demand strategy" in str(w.message)
        ]
        assert len(deprecations) == 1, [str(w.message) for w in deprecations]
        assert "allocation_strategy_on_demand" in str(deprecations[0].message)
    finally:
        loaded.stop()


# ---------------------------------------------------------------------------
# An invalid template is reported as invalid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_template_is_reported_by_get_template_create_request_and_validate(
    scheduler: str, tmp_path: Path
) -> None:
    from orb.application.commands.request_creation_handlers import CreateMachineRequestHandler

    case = Case("invalid", {"price_type": "spot", "allocation_strategy": "bogus"}, (None,) * 4)
    loaded = Loaded(case, scheduler, tmp_path)
    try:
        assert await loaded.manager.get_template_by_id(TEMPLATE_ID) is None

        with pytest.raises(ValidationError, match=f"template {TEMPLATE_ID} is invalid: .*bogus"):
            await loaded.get_template()

        creator = CreateMachineRequestHandler.__new__(CreateMachineRequestHandler)
        creator._query_bus = _QueryBus(loaded.get_handler)  # type: ignore[attr-defined]
        creator.logger = MagicMock()  # type: ignore[misc]
        with pytest.raises(ValidationError, match="is invalid"):
            await creator._load_template(TEMPLATE_ID)

        validator = ValidateTemplateHandler(
            logger=MagicMock(), template_manager=loaded.port, error_handler=MagicMock()
        )
        result = await validator.execute_query(ValidateTemplateQuery(template_id=TEMPLATE_ID))
        assert result["valid"] is False
        assert any("is invalid" in e and "bogus" in e for e in result["validation_errors"])
    finally:
        loaded.stop()
