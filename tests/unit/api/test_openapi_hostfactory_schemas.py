"""The exported OpenAPI document describes the HostFactory format and is static."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from orb.api.documentation.openapi_config import BUILT_IN_FORMATS
from orb.api.models.hostfactory import HF_MODELS, HF_OPERATION_MODELS
from orb.api.server import create_fastapi_app
from orb.config.schemas.server_schema import AuthConfig, CORSConfig, RateLimitConfig, ServerConfig
from orb.infrastructure.scheduler.registry import get_scheduler_registry
from tests.utilities.reset_singletons import reset_all_singletons


def _spec() -> dict:
    config = ServerConfig(  # type: ignore[call-arg]
        enabled=True,
        auth=AuthConfig(enabled=False, strategy="replace"),  # type: ignore[call-arg]
        cors=CORSConfig(origins=["*"]),  # type: ignore[call-arg]
        rate_limiting=RateLimitConfig(enabled=False),  # type: ignore[call-arg]
    )
    return create_fastapi_app(config).openapi()


@pytest.fixture(scope="module")
def spec() -> dict:
    return _spec()


def test_hostfactory_components_are_registered(spec):
    schemas = spec["components"]["schemas"]

    for model in HF_MODELS:
        assert model.__name__ in schemas


def test_each_mapped_operation_names_every_built_in_format(spec):
    operations = {
        op["operationId"]: op
        for item in spec["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    }

    for operation_id, model in HF_OPERATION_MODELS.items():
        formats = operations[operation_id]["x-orb-formats"]
        assert tuple(formats) == BUILT_IN_FORMATS
        assert formats["hostfactory"] == {"$ref": f"#/components/schemas/{model.__name__}"}
        success = next(
            r for code, r in operations[operation_id]["responses"].items() if code[0] == "2"
        )
        assert formats["default"] == success["content"]["application/json"]["schema"]


def test_spec_does_not_depend_on_registered_schedulers(spec):
    get_scheduler_registry().register(
        "spec-probe", lambda cfg: MagicMock(), lambda c: None, strategy_class=MagicMock
    )
    try:
        again = _spec()
    finally:
        reset_all_singletons()

    assert json.dumps(again, sort_keys=True) == json.dumps(spec, sort_keys=True)
