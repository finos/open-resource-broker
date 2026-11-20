"""Incomplete live metadata must not become launch compatibility evidence."""

import copy
import json
import subprocess
from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.services.launch_preflight import OCILaunchPreflight, OCIPreflightError

AD = "test:PHX-AD-1"
SHAPE = "VM.Standard.E5.Flex"
IMAGE_GET = ("compute", "image", "get")
SUBNET_GET = ("network", "subnet", "get")
SHAPE_LIST = ("compute", "shape", "list")
COMPATIBILITY_GET = ("compute", "image-shape-compatibility-entry", "get")


@pytest.fixture
def case():
    payload = {
        "compartment_id": "compartment",
        "availability_domain": AD,
        "shape": SHAPE,
        "shape_config": {"ocpus": 4, "memoryInGBs": 32},
        "source_details": {"image_id": "image"},
        "create_vnic_details": {"subnet_id": "subnet"},
    }
    responses = {
        IMAGE_GET: {"data": {"id": "image", "lifecycle-state": "AVAILABLE"}},
        SUBNET_GET: {
            "data": {
                "id": "subnet",
                "lifecycle-state": "AVAILABLE",
                "availability-domain": None,
            }
        },
        SHAPE_LIST: {
            "data": [
                {
                    "shape": SHAPE,
                    "is-flexible": True,
                    "ocpus": 4,
                    "memory-in-gbs": 32,
                    "ocpu-options": {"min": 1, "max": 64},
                    "memory-options": {
                        "min-in-g-bs": 1,
                        "max-in-g-bs": 1024,
                        "min-per-ocpu-in-gbs": 1,
                        "max-per-ocpu-in-gbs": 64,
                    },
                }
            ]
        },
        COMPATIBILITY_GET: {"data": {"image-id": "image", "shape": SHAPE}},
    }
    return payload, responses


def validate(case):
    payload, responses = case
    return OCILaunchPreflight(lambda args: responses[tuple(args[:3])]).validate(payload)


def assert_error(case, expected):
    with pytest.raises(OCIPreflightError) as raised:
        validate(case)
    assert raised.value.error_code == expected


@pytest.mark.parametrize("member", [None, {}, {"shape": 7}])
def test_malformed_shape_member_is_unavailable_even_beside_requested_shape(case, member):
    _, responses = case
    responses[SHAPE_LIST]["data"].append(member)

    assert_error(case, "OCI_PREFLIGHT_UNAVAILABLE")


@pytest.mark.parametrize(
    ("option", "limits"),
    [
        ("ocpu-options", {"min": 1}),
        ("ocpu-options", {"max": 64}),
        ("memory-options", {"min-in-g-bs": 1}),
        ("memory-options", {"max-in-g-bs": 1024}),
    ],
)
def test_partial_required_live_bounds_are_unavailable(case, option, limits):
    _, responses = case
    responses[SHAPE_LIST]["data"][0][option] = limits

    assert_error(case, "OCI_PREFLIGHT_UNAVAILABLE")


@pytest.mark.parametrize("minimum", [True, "1", float("nan"), float("inf")])
def test_invalid_live_bound_is_unavailable_rather_than_requested_sizing_rejection(case, minimum):
    _, responses = case
    responses[SHAPE_LIST]["data"][0]["ocpu-options"]["min"] = minimum

    assert_error(case, "OCI_PREFLIGHT_UNAVAILABLE")


@pytest.mark.parametrize("subnet_ad", ["", 7])
def test_unreadable_subnet_ad_does_not_fall_back_to_explicit_template_ad(case, subnet_ad):
    _, responses = case
    responses[SUBNET_GET]["data"]["availability-domain"] = subnet_ad

    assert_error(case, "OCI_PREFLIGHT_UNAVAILABLE")


@pytest.mark.parametrize("sizing", [{"ocpus": 4}, {"ocpus": 4, "memoryInGBs": 32}])
def test_fixed_vm_shape_rejects_flex_overrides(case, sizing):
    payload, responses = case
    payload["shape"] = "VM.Standard2.4"
    payload["shape_config"] = sizing
    responses[SHAPE_LIST]["data"][0].update(shape=payload["shape"], **{"is-flexible": False})
    responses[COMPATIBILITY_GET]["data"]["shape"] = payload["shape"]

    assert_error(case, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize(
    ("option", "dimension", "reported", "different"),
    [("ocpu-options", "ocpus", 4, 6), ("memory-options", "memoryInGBs", 32, 64)],
)
def test_flex_with_a_fixed_dimension_accepts_only_its_reported_size(
    case, option, dimension, reported, different
):
    payload, responses = case
    responses[SHAPE_LIST]["data"][0][option] = None
    payload["shape_config"][dimension] = reported
    assert validate(case) == AD

    payload["shape_config"][dimension] = different
    assert_error(case, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize("failure", ["shape-member", "partial-bounds", "subnet-ad", "fixed-vm"])
@pytest.mark.asyncio
async def test_invalid_live_evidence_blocks_posts_and_capacity_fallback(case, failure):
    payload, responses = case
    if failure == "shape-member":
        responses[SHAPE_LIST]["data"].append(None)
    elif failure == "partial-bounds":
        responses[SHAPE_LIST]["data"][0]["ocpu-options"] = {"min": 1}
    elif failure == "subnet-ad":
        responses[SUBNET_GET]["data"]["availability-domain"] = ""
    else:
        payload["shape"] = "VM.Standard2.4"
        responses[SHAPE_LIST]["data"][0].update(shape=payload["shape"], **{"is-flexible": False})
        responses[COMPATIBILITY_GET]["data"]["shape"] = payload["shape"]
    calls = []

    def run(cmd, **kwargs):
        calls.append(copy.deepcopy(cmd))
        command = tuple(cmd[1:4])
        assert command in responses, f"Unexpected OCI subprocess: {command}"
        return subprocess.CompletedProcess(cmd, 0, json.dumps(responses[command]))

    handler = OCIComputeHandler(
        MagicMock(), "us-phoenix-1", auth_config={"launch_preflight": True}, run_command=run
    )
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "template_id": "preflight-edge",
            "image_id": "image",
            "subnet_ids": ["subnet"],
            "compartment_id": "compartment",
            "availability_domain": AD,
            "machine_types": {payload["shape"]: 1},
            "shape_config": payload["shape_config"],
            "capacity_type": "preemptible",
            "fallback_to_ondemand": True,
            "count": 2,
        },
    )

    result = await handler.create_instances(operation)

    assert not result.success
    expected = "OCI_PREFLIGHT_REJECTED" if failure == "fixed-vm" else "OCI_PREFLIGHT_UNAVAILABLE"
    assert result.error_code == expected
    assert calls
    assert all(cmd[1] != "raw-request" for cmd in calls)
    assert not any(cmd[1:4] == ["compute", "instance", "terminate"] for cmd in calls)
