"""Launch preflight checks current OCI compatibility without mutating resources."""

import copy

import pytest

from orb.providers.oci.cli_client import OCICommandError
from orb.providers.oci.services.launch_preflight import OCILaunchPreflight, OCIPreflightError

AD = "test:PHX-AD-1"
OTHER_AD = "test:PHX-AD-2"
SHAPE = "VM.Standard.E5.Flex"
IMAGE_GET = ("compute", "image", "get")
SUBNET_GET = ("network", "subnet", "get")
SHAPE_LIST = ("compute", "shape", "list")
COMPATIBILITY_GET = ("compute", "image-shape-compatibility-entry", "get")
AD_LIST = ("iam", "availability-domain", "list")


@pytest.fixture
def payload():
    return {
        "compartment_id": "test-compartment",
        "availability_domain": AD,
        "shape": SHAPE,
        "shape_config": {"ocpus": 4, "memoryInGBs": 32},
        "source_details": {"image_id": "test-image"},
        "create_vnic_details": {"subnet_id": "test-subnet"},
    }


@pytest.fixture
def responses():
    return {
        IMAGE_GET: {"data": {"id": "test-image", "lifecycle-state": "AVAILABLE"}},
        SUBNET_GET: {
            "data": {
                "id": "test-subnet",
                "lifecycle-state": "AVAILABLE",
                "availability-domain": None,
            }
        },
        SHAPE_LIST: {
            "data": [
                {
                    "shape": SHAPE,
                    "is-flexible": True,
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
        COMPATIBILITY_GET: {
            "data": {
                "image-id": "test-image",
                "shape": SHAPE,
                "ocpu-constraints": {"min": 1, "max": 64},
                "memory-constraints": {"min-in-gbs": 1, "max-in-gbs": 1024},
            }
        },
        AD_LIST: {"data": [{"name": AD}, {"name": OTHER_AD}]},
    }


class ReadOnlyOCI:
    """Return CLI-shaped responses and reject any unexpected command."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, args):
        self.calls.append(copy.deepcopy(args))
        key = tuple(args[:3])
        assert key in self.responses, f"Unexpected OCI command: {key}"
        result = self.responses[key]
        if isinstance(result, Exception):
            raise result
        return result

    def command(self, key):
        return next(args for args in self.calls if tuple(args[:3]) == key)


def assert_preflight_error(payload, responses, code):
    runner = ReadOnlyOCI(responses)
    with pytest.raises(OCIPreflightError) as raised:
        OCILaunchPreflight(runner).validate(payload)
    assert raised.value.error_code == code
    return raised.value, runner


@pytest.mark.parametrize("command", [IMAGE_GET, SUBNET_GET])
@pytest.mark.parametrize("identity", [None, "another-resource"])
def test_unverified_resource_identity_is_unavailable(payload, responses, command, identity):
    responses[command]["data"]["id"] = identity
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


def test_missing_subnet_ad_metadata_is_unavailable(payload, responses):
    del responses[SUBNET_GET]["data"]["availability-domain"]
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


def test_regional_subnet_with_explicit_ad_checks_requested_image_and_shape(payload, responses):
    runner = ReadOnlyOCI(responses)
    assert OCILaunchPreflight(runner).validate(payload) == AD
    assert not any(tuple(args[:3]) == AD_LIST for args in runner.calls)
    shape_args = runner.command(SHAPE_LIST)
    for flag, expected in (
        ("--compartment-id", "test-compartment"),
        ("--availability-domain", AD),
        ("--image-id", "test-image"),
        ("--shape", SHAPE),
    ):
        assert shape_args[shape_args.index(flag) + 1] == expected
    assert "--all" in shape_args
    compatibility_args = runner.command(COMPATIBILITY_GET)
    assert compatibility_args[compatibility_args.index("--image-id") + 1] == "test-image"
    assert compatibility_args[compatibility_args.index("--shape-name") + 1] == SHAPE


def test_regional_subnet_resolves_ad_when_not_authored(payload, responses):
    payload["availability_domain"] = None
    runner = ReadOnlyOCI(responses)
    assert OCILaunchPreflight(runner).validate(payload) == AD
    domain_args = runner.command(AD_LIST)
    assert domain_args[domain_args.index("--compartment-id") + 1] == "test-compartment"
    shape_args = runner.command(SHAPE_LIST)
    assert shape_args[shape_args.index("--availability-domain") + 1] == AD


def test_ad_specific_subnet_resolves_its_ad_without_iam_discovery(payload, responses):
    payload.pop("availability_domain")
    responses[SUBNET_GET]["data"]["availability-domain"] = OTHER_AD
    runner = ReadOnlyOCI(responses)
    assert OCILaunchPreflight(runner).validate(payload) == OTHER_AD
    assert not any(tuple(args[:3]) == AD_LIST for args in runner.calls)


def test_subnet_and_requested_ad_must_match(payload, responses):
    responses[SUBNET_GET]["data"]["availability-domain"] = OTHER_AD
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize("command", [IMAGE_GET, SUBNET_GET])
@pytest.mark.parametrize("state", ["PROVISIONING", "TERMINATING", "TERMINATED"])
def test_image_and_subnet_must_be_available(payload, responses, command, state):
    responses[command]["data"]["lifecycle-state"] = state
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


def test_shape_absent_from_image_and_ad_listing_is_rejected(payload, responses):
    responses[SHAPE_LIST]["data"] = []
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


def test_unrelated_shape_does_not_make_requested_shape_available(payload, responses):
    responses[SHAPE_LIST]["data"][0]["shape"] = "VM.Standard.A1.Flex"
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize(
    ("ocpus", "memory"), [(0.5, 32), (65, 32), (4, 0.5), (4, 1025), (4, 3), (4, 257)]
)
def test_flex_must_fit_shape_cpu_memory_and_ratio_bounds(payload, responses, ocpus, memory):
    payload["shape_config"] = {"ocpus": ocpus, "memoryInGBs": memory}
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize(("ocpus", "memory"), [(1, 1), (1, 64), (64, 64), (64, 1024)])
def test_shape_bounds_are_inclusive(payload, responses, ocpus, memory):
    payload["shape_config"] = {"ocpus": ocpus, "memoryInGBs": memory}
    assert OCILaunchPreflight(ReadOnlyOCI(responses)).validate(payload) == AD


@pytest.mark.parametrize(("ocpus", "memory"), [(1, 32), (9, 32), (4, 8), (4, 129)])
def test_image_constraints_narrow_valid_shape_sizes(payload, responses, ocpus, memory):
    compatibility = responses[COMPATIBILITY_GET]["data"]
    compatibility["ocpu-constraints"] = {"min": 2, "max": 8}
    compatibility["memory-constraints"] = {"min-in-gbs": 16, "max-in-gbs": 128}
    payload["shape_config"] = {"ocpus": ocpus, "memoryInGBs": memory}
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize(("ocpus", "memory"), [(2, 16), (8, 128)])
def test_intersection_of_image_and_shape_constraints_accepts_boundaries(
    payload, responses, ocpus, memory
):
    compatibility = responses[COMPATIBILITY_GET]["data"]
    compatibility["ocpu-constraints"] = {"min": 2, "max": 8}
    compatibility["memory-constraints"] = {"min-in-gbs": 16, "max-in-gbs": 128}
    payload["shape_config"] = {"ocpus": ocpus, "memoryInGBs": memory}
    assert OCILaunchPreflight(ReadOnlyOCI(responses)).validate(payload) == AD


def test_null_image_constraints_add_no_sizing_restrictions(payload, responses):
    compatibility = responses[COMPATIBILITY_GET]["data"]
    compatibility["ocpu-constraints"] = None
    compatibility["memory-constraints"] = None
    assert OCILaunchPreflight(ReadOnlyOCI(responses)).validate(payload) == AD


def test_fixed_shape_with_null_flex_options_uses_its_reported_capacity(payload, responses):
    payload["shape"] = "BM.Optimized3.36"
    payload.pop("shape_config")
    responses[SHAPE_LIST]["data"] = [
        {
            "shape": "BM.Optimized3.36",
            "is-flexible": False,
            "ocpus": 36,
            "memory-in-gbs": 512,
            "ocpu-options": None,
            "memory-options": None,
        }
    ]
    responses[COMPATIBILITY_GET]["data"]["shape"] = "BM.Optimized3.36"
    assert OCILaunchPreflight(ReadOnlyOCI(responses)).validate(payload) == AD


def test_image_constraints_also_apply_to_fixed_shape_capacity(payload, responses):
    payload["shape"] = "BM.Optimized3.36"
    payload.pop("shape_config")
    responses[SHAPE_LIST]["data"] = [
        {
            "shape": "BM.Optimized3.36",
            "is-flexible": False,
            "ocpus": 36,
            "memory-in-gbs": 512,
            "ocpu-options": None,
            "memory-options": None,
        }
    ]
    compatibility = responses[COMPATIBILITY_GET]["data"]
    compatibility["shape"] = "BM.Optimized3.36"
    compatibility["memory-constraints"]["max-in-gbs"] = 256
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_requested_sizing_is_rejected(payload, responses, value):
    payload["shape_config"]["ocpus"] = value
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_REJECTED")


@pytest.mark.parametrize("sizing", [{"ocpus": 4}, {"memoryInGBs": 32}])
def test_partial_flex_sizing_is_unavailable_and_requests_explicit_cpu_and_ram(
    payload, responses, sizing
):
    payload["shape_config"] = sizing
    error, _ = assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")
    message = str(error).lower()
    assert "explicit" in message
    assert "cpu" in message
    assert "memory" in message or "ram" in message


def test_empty_flex_sizing_preserves_existing_one_ocpu_sixteen_gb_defaults(payload, responses):
    payload["shape_config"] = {}
    responses[COMPATIBILITY_GET]["data"]["ocpu-constraints"] = {"min": 1, "max": 1}
    responses[COMPATIBILITY_GET]["data"]["memory-constraints"] = {
        "min-in-gbs": 16,
        "max-in-gbs": 16,
    }
    assert OCILaunchPreflight(ReadOnlyOCI(responses)).validate(payload) == AD


@pytest.mark.parametrize("command", [IMAGE_GET, SUBNET_GET, SHAPE_LIST, COMPATIBILITY_GET])
@pytest.mark.parametrize(
    "failure",
    [
        OCICommandError("private-response-marker", code="NotAuthorizedOrNotFound", status=404),
        OCICommandError("private-response-marker", code="NotAuthorized", status=403),
        OCICommandError("private-response-marker", code="InternalError", status=500),
        TimeoutError("private-response-marker"),
    ],
)
def test_read_failures_are_unavailable_and_do_not_expose_provider_error_text(
    payload, responses, command, failure
):
    responses[command] = failure
    error, _ = assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")
    assert "private-response-marker" not in str(error)


@pytest.mark.parametrize("command", [IMAGE_GET, SUBNET_GET, SHAPE_LIST, COMPATIBILITY_GET])
@pytest.mark.parametrize("invalid", [{}, {"data": None}, {"data": "private-response-marker"}])
def test_malformed_cli_read_is_unavailable(payload, responses, command, invalid):
    responses[command] = invalid
    error, _ = assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")
    assert "private-response-marker" not in str(error)


@pytest.mark.parametrize("command", [IMAGE_GET, SUBNET_GET])
def test_missing_lifecycle_state_is_unknown_rather_than_known_incompatibility(
    payload, responses, command
):
    responses[command]["data"].pop("lifecycle-state")
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


def test_unavailable_ad_discovery_does_not_guess_a_domain(payload, responses):
    payload.pop("availability_domain")
    responses[AD_LIST]["data"] = []
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


def test_missing_flex_bounds_are_unavailable(payload, responses):
    responses[SHAPE_LIST]["data"][0]["memory-options"] = None
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


@pytest.mark.parametrize("value", [None, "true", 1])
def test_unreadable_shape_flexibility_is_unavailable(payload, responses, value):
    responses[SHAPE_LIST]["data"][0]["is-flexible"] = value
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


def test_inverted_read_bounds_are_unavailable_rather_than_requested_sizing_error(
    payload, responses
):
    responses[SHAPE_LIST]["data"][0]["ocpu-options"] = {"min": 8, "max": 2}
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


@pytest.mark.parametrize("field", ["image-id", "shape"])
def test_compatibility_entry_must_describe_the_requested_image_and_shape(payload, responses, field):
    responses[COMPATIBILITY_GET]["data"][field] = "another-resource"
    assert_preflight_error(payload, responses, "OCI_PREFLIGHT_UNAVAILABLE")


def test_preflight_leaves_launch_payload_and_cli_response_data_unchanged(payload, responses):
    original_payload = copy.deepcopy(payload)
    original_responses = copy.deepcopy(responses)
    runner = ReadOnlyOCI(responses)
    assert OCILaunchPreflight(runner).validate(payload) == AD
    assert payload == original_payload
    assert responses == original_responses


def test_reused_service_reads_fresh_state_for_each_validation(payload, responses):
    runner = ReadOnlyOCI(responses)
    service = OCILaunchPreflight(runner)
    assert service.validate(payload) == AD
    first_reads = len(runner.calls)
    responses[IMAGE_GET]["data"]["lifecycle-state"] = "DELETED"
    with pytest.raises(OCIPreflightError) as raised:
        service.validate(payload)
    assert raised.value.error_code == "OCI_PREFLIGHT_REJECTED"
    assert len(runner.calls) > first_reads
    assert sum(tuple(args[:3]) == IMAGE_GET for args in runner.calls) == 2
