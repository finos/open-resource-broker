"""HostFactory output against the IBM Spectrum Symphony 7.3.2 documentation.

The fixtures under ``fixtures/ibm`` are the example blocks of
``docs/root/hostfactory/input-output.md``, one per operation. They must validate
against the HostFactory wire models, the formatter must produce every key the
IBM examples carry, and the negative cases below must be rejected.

Known divergence: machine ``status`` carries ORB lifecycle values such as
``pending`` and ``launching`` in addition to the values IBM lists. ``result`` is
the progress field.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from orb.api.models.hostfactory import (
    HfRequestIdResponse,
    HfRequestStatusResponse,
    HfReturnRequestsResponse,
    HfTemplatesResponse,
)
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)

from .conftest import make_machine_ref_dto, make_request_dto

_FIXTURES = Path(__file__).parent / "fixtures" / "ibm"
_SNAKE_CASE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")

_MODELS = {
    "getAvailableTemplates": HfTemplatesResponse,
    "requestMachines": HfRequestIdResponse,
    "requestReturnMachines": HfRequestIdResponse,
    "getRequestStatus": HfRequestStatusResponse,
    "getReturnRequests": HfReturnRequestsResponse,
}


def _golden(operation: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / f"{operation}.json").read_text())


def _snake_case_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        found = [k for k in value if _SNAKE_CASE.match(k)]
        return found + [k for v in value.values() for k in _snake_case_keys(v)]
    if isinstance(value, list):
        return [k for v in value for k in _snake_case_keys(v)]
    return []


@pytest.fixture
def hf() -> HostFactorySchedulerStrategy:
    return HostFactorySchedulerStrategy()


@pytest.mark.parametrize("operation", sorted(_MODELS))
def test_ibm_example_validates_against_the_wire_model(operation):
    _MODELS[operation].model_validate(_golden(operation))


# ---------------------------------------------------------------------------
# The formatter produces the keys of the IBM examples
# ---------------------------------------------------------------------------


def test_request_status_machine_carries_every_ibm_key(hf):
    golden_machine = _golden("getRequestStatus")["requests"][0]["machines"][0]
    machine = make_machine_ref_dto(machine_id=golden_machine["machineId"], status="terminated")
    dto = make_request_dto(status="complete", machine_refs=[machine], request_type="return")

    body = hf.format_request_status_response([dto])

    item = body["requests"][0]
    assert set(_golden("getRequestStatus")["requests"][0]) <= set(item)
    assert set(golden_machine) - {"priceType", "instanceType"} <= set(item["machines"][0])
    HfRequestStatusResponse.model_validate(body)


def test_request_creation_response_carries_every_ibm_key(hf):
    body = hf.format_request_response({"request_id": "req-1", "status": "pending"})

    assert set(_golden("requestMachines")) == set(body)
    HfRequestIdResponse.model_validate(body)


def test_return_requests_response_carries_every_ibm_key(hf):
    golden = _golden("getReturnRequests")
    items = [{"name": "host-1", "machine_id": "i-1", "grace_period": 0}]

    reclaimed = hf.format_reclaimed_machines_response(items)
    listed = hf.format_return_requests_response(
        [{"grace_period": 0, "machines": [{"name": "host-1"}]}]
    )

    for body in (reclaimed, listed):
        assert set(golden) == set(body)
        assert set(golden["requests"][0]) == set(body["requests"][0])
        HfReturnRequestsResponse.model_validate(body)


def test_templates_response_carries_every_ibm_key(hf):
    from orb.infrastructure.template.dtos import TemplateDTO

    template = TemplateDTO(
        template_id="contract-tpl-hf",
        name="contract-tpl-hf",
        max_instances=4,
        machine_types={"t3.micro": 1},
        image_id="ami-12345678",
        subnet_ids=["subnet-aabbccdd"],
        security_group_ids=["sg-11223344"],
        price_type="ondemand",
        provider_api="EC2Fleet",
    )
    golden = _golden("getAvailableTemplates")

    body = hf.format_templates_response([template])

    assert set(golden) <= set(body)
    assert {"templateId", "maxNumber", "attributes"} <= set(body["templates"][0])
    HfTemplatesResponse.model_validate(body)


def test_hostfactory_machine_output_has_no_snake_case_key(hf):
    machine = make_machine_ref_dto()
    dto = make_request_dto(status="complete", machine_refs=[machine])

    body = hf.format_request_status_response([dto])

    assert _snake_case_keys(body) == []


# ---------------------------------------------------------------------------
# Negative cases: the wire models reject what the IBM documentation forbids
# ---------------------------------------------------------------------------


def _mutated(mutate) -> dict[str, Any]:
    body = copy.deepcopy(_golden("getRequestStatus"))
    mutate(body["requests"][0]["machines"][0])
    return body


def test_iso_launchtime_is_rejected():
    body = _mutated(lambda m: m.update(launchtime="2024-12-19T14:52:22Z"))
    with pytest.raises(ValidationError):
        HfRequestStatusResponse.model_validate(body)


def test_missing_result_is_rejected():
    body = _mutated(lambda m: m.pop("result"))
    with pytest.raises(ValidationError):
        HfRequestStatusResponse.model_validate(body)


def test_null_private_ip_address_is_rejected():
    body = _mutated(lambda m: m.update(privateIpAddress=None))
    with pytest.raises(ValidationError):
        HfRequestStatusResponse.model_validate(body)


def test_snake_case_key_is_detected():
    body = _mutated(lambda m: m.update(private_ip_address="10.0.0.1"))
    assert _snake_case_keys(body) == ["private_ip_address"]
