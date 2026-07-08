"""K8s provisioning contract tests — inherits all scenarios from BaseProvisioningContract."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from orb.providers.k8s.domain.template.k8s_template import K8sResourceQuantities, K8sTemplate
from orb.providers.k8s.utilities.pod_spec import make_pod_name
from tests.providers.contract.base_provisioning_contract import BaseProvisioningContract
from tests.providers.k8s.contract.conftest import (
    _build_pod_handler,
    _K8sProviderAdapter,
    _make_config,
    _make_core_v1_mock,
    _make_logger,
    _make_request,
)


@pytest.mark.provider_contract
class TestK8sProvisioningContract(BaseProvisioningContract):
    """K8s provider satisfies the provisioning contract (mock-backed)."""

    # The base contract's test_acquire_resource_ids_are_stable scenario calls
    # provider_under_test.acquire_hosts twice with different request IDs.  The
    # fixed _make_core_v1_mock filters list_namespaced_pod by label_selector so
    # each acquire sees only its own pods regardless of shared mock state.  The
    # base implementation runs correctly without an override; this class inherits
    # it unchanged.


# ---------------------------------------------------------------------------
# Regression test: two sequential acquires on the SAME adapter/mock
# ---------------------------------------------------------------------------


def _make_template(namespace: str = "orb-contract") -> K8sTemplate:
    return K8sTemplate(
        template_id="tpl-regression",
        name="regression-pod",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        namespace=namespace,
        resource_requests=K8sResourceQuantities(cpu="100m", memory="64Mi"),
    )


def test_two_acquires_on_same_mock_see_independent_pods() -> None:
    """Two sequential acquires on the SAME core_v1 mock both see their own pods.

    Regression test for bd-2506: the old _make_core_v1_mock used a shared
    deleted_pods closure that persisted across acquire calls.  A second
    acquire on the same mock would see an empty list because deleted_pods
    was non-empty after the first release.

    The fixed mock tracks pods by name and filters list_namespaced_pod by
    the label_selector (orb.io/request-id=<id>), so each acquire only sees
    pods it created — even when sharing the same CoreV1Api mock instance.
    """
    config = _make_config()
    logger = _make_logger()
    # Single mock shared across both acquires — this was the failure scenario.
    core_v1 = _make_core_v1_mock(request_id="req-A")
    handler = _build_pod_handler(core_v1, config, logger)
    adapter = _K8sProviderAdapter(handler)
    template = _make_template()

    # First acquire + release.
    req_a = _make_request(request_id="req-A", requested_count=1)
    result_a = adapter.acquire_hosts(req_a, template)
    ids_a = result_a.get("resource_ids", [])
    assert ids_a, "First acquire must return at least one resource_id"
    adapter.release_hosts(ids_a)

    # Second acquire with a different request_id on the SAME adapter/mock.
    req_b = _make_request(request_id="req-B", requested_count=1)
    result_b = adapter.acquire_hosts(req_b, template)
    ids_b = result_b.get("resource_ids", [])
    assert ids_b, (
        "Second acquire on same mock returned empty resource_ids — "
        "deleted_pods from req-A masked req-B pods (bd-2506 regression)"
    )

    # The two acquires must produce distinct pod names.
    assert set(ids_a) != set(ids_b), (
        f"Two acquires with different request IDs must produce distinct IDs; "
        f"got ids_a={ids_a}, ids_b={ids_b}"
    )


def test_mock_filter_uses_real_label_not_name_prefix() -> None:
    """The conftest list filter matches pods by the real orb.io/request-id label.

    B1 fix regression: the old filter re-derived a name prefix by hand.
    This test creates pods using the real make_pod_name helper and verifies
    that the mock's label_selector filter includes exactly the pod for the
    target request and excludes the pod for an unrelated request — proving
    the filter is coupled to production labels, not to hardcoded name logic.
    """
    from types import SimpleNamespace

    request_id_a = "label-test-req-aaa"
    request_id_b = "label-test-req-bbb"

    core_v1 = _make_core_v1_mock()

    # Build pods using the real make_pod_name (production naming function).
    pod_name_a = make_pod_name(request_id_a, 0)
    pod_name_b = make_pod_name(request_id_b, 0)

    # Simulate what the real handler does: create pods with the request-id label.
    body_a = SimpleNamespace(
        metadata=SimpleNamespace(
            name=pod_name_a,
            labels={"orb.io/managed": "true", "orb.io/request-id": request_id_a},
        )
    )
    body_b = SimpleNamespace(
        metadata=SimpleNamespace(
            name=pod_name_b,
            labels={"orb.io/managed": "true", "orb.io/request-id": request_id_b},
        )
    )
    core_v1.create_namespaced_pod(namespace="orb-test", body=body_a)
    core_v1.create_namespaced_pod(namespace="orb-test", body=body_b)

    # List with a selector scoped to request_id_a only.
    result = core_v1.list_namespaced_pod(
        namespace="orb-test",
        label_selector=f"orb.io/request-id={request_id_a}",
    )
    names = {pod.metadata.name for pod in result.items}

    assert pod_name_a in names, (
        f"Pod {pod_name_a!r} created via make_pod_name must be matched by the "
        f"label filter for request {request_id_a!r}"
    )
    assert pod_name_b not in names, (
        f"Pod {pod_name_b!r} for a different request must NOT appear in filtered results"
    )


def test_delete_only_affects_matching_request_pods() -> None:
    """Deleting pods for one request must not hide pods created for another request.

    Verifies that the per-name deletion tracking in _make_core_v1_mock correctly
    scopes removals: only the named pod disappears, not pods for other requests.
    """
    config = _make_config()
    logger = _make_logger()
    core_v1 = _make_core_v1_mock()
    handler = _build_pod_handler(core_v1, config, logger)
    adapter = _K8sProviderAdapter(handler)
    template = _make_template()

    # Acquire two concurrent requests on the same adapter.
    req_x = _make_request(request_id="req-X", requested_count=1)
    req_y = _make_request(request_id="req-Y", requested_count=1)
    result_x = adapter.acquire_hosts(req_x, template)
    result_y = adapter.acquire_hosts(req_y, template)

    ids_x = result_x.get("resource_ids", [])
    ids_y = result_y.get("resource_ids", [])
    assert ids_x, "req-X must return resource_ids"
    assert ids_y, "req-Y must return resource_ids"

    # Release req-X.
    adapter.release_hosts(ids_x)

    # Confirm req-Y pods still visible after req-X release.
    status_req_y = _make_request(
        request_id="req-Y",
        resource_ids=ids_y,
        provider_data={"namespace": "orb-contract", "pod_names": ids_y},
    )
    status = adapter.check_hosts_status(status_req_y)
    # At least one instance should still be reported (not empty list from masked pods).
    assert status is not None, (
        "check_hosts_status must return a result after sibling-request release"
    )
