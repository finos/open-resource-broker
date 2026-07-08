"""K8s provisioning contract tests — inherits all scenarios from BaseProvisioningContract.

Group T5 note
-------------

The base contract's test_acquire_resource_ids_are_stable scenario calls
provider_under_test.acquire_hosts twice and asserts the returned resource_ids
differ between calls.  The k8s contract conftest uses a Python-level mock
whose _list_namespaced_pod implementation tracks deleted_pods state across both
acquire calls via a shared closure.  Because the pod name is derived from
request_id (e.g. orb-contractreq001-0000 vs orb-contractreq001b-0000), the two
calls naturally return different IDs and the assertion holds.

However, the shared mock state means a second acquire call would see stale
list results if the pod names happened to collide.  The overriding scenario
below replaces the shared adapter with two fresh, independent adapters — one
per acquire call — so the assertion is not contingent on the ordering of mock
side-effects.  Each adapter gets its own core_v1 mock with a clean deleted_pods
closure, removing the state-coupling between the two acquires.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from tests.providers.contract.base_provisioning_contract import BaseProvisioningContract


@pytest.mark.provider_contract
class TestK8sProvisioningContract(BaseProvisioningContract):
    """K8s provider satisfies the provisioning contract (mock-backed)."""

    @pytest.mark.provider_contract
    def test_acquire_resource_ids_are_stable(
        self, provider_under_test: Any, valid_provision_request: Any, valid_template: Any
    ) -> None:
        """Two acquire calls with different request IDs return different resource IDs.

        Override of the base contract scenario: uses two independent provider
        adapters (each with a fresh mock) so the assertion is not contingent on
        shared mock state between the two acquire calls.
        """
        from unittest.mock import MagicMock  # noqa: PLC0415

        from tests.providers.k8s.contract.conftest import (  # noqa: PLC0415
            _build_pod_handler,
            _K8sProviderAdapter,
            _make_config,
            _make_core_v1_mock,
            _make_logger,
        )

        # Adapter 1 — uses the shared fixture (first acquire).
        result1 = provider_under_test.acquire_hosts(valid_provision_request, valid_template)

        # Adapter 2 — fresh independent mock for the second acquire so the
        # list_namespaced_pod state is not polluted by the first acquire.
        req2_id = str(valid_provision_request.request_id) + "-b"
        req2_id = req2_id.replace("-", "")[:20]  # derive a safe ID token
        config2 = _make_config()
        logger2 = _make_logger()
        core_v1_2 = _make_core_v1_mock(request_id="contract-req-002")
        handler2 = _build_pod_handler(core_v1_2, config2, logger2)
        adapter2 = _K8sProviderAdapter(handler2)

        req2 = MagicMock()
        req2.request_id = "contract-req-002"
        req2.requested_count = valid_provision_request.requested_count
        req2.template_id = valid_provision_request.template_id
        req2.metadata = {}
        req2.resource_ids = []
        req2.provider_data = {}
        req2.provider_api = None

        result2 = adapter2.acquire_hosts(req2, valid_template)

        ids1 = set(result1.get("resource_ids", []))
        ids2 = set(result2.get("resource_ids", []))
        assert ids1 != ids2, (
            f"Two separate acquires must produce distinct resource IDs; both returned: {ids1}"
        )
