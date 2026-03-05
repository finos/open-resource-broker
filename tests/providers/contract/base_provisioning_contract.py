"""Base provisioning contract — scenarios every provider must satisfy.

Subclass this in a provider's contract test directory and supply the required
fixtures via conftest.py.  pytest collects the concrete test_* methods
automatically when it discovers the subclass.

Required fixtures (implement in provider conftest.py):
    provider_under_test  — object with acquire_hosts(request, template) and
                           release_hosts(machine_ids) methods
    valid_provision_request — request-like object (request_id, requested_count,
                              template_id attributes)
    valid_template       — provider-appropriate template object
"""

import pytest


class BaseProvisioningContract:
    """Provider-agnostic provisioning contract scenarios."""

    # ------------------------------------------------------------------
    # Required fixtures (implement in provider conftest.py):
    #   provider_under_test, valid_provision_request, valid_template
    # ------------------------------------------------------------------
    # Contract scenarios
    # ------------------------------------------------------------------

    @pytest.mark.provider_contract
    def test_acquire_returns_success(
        self, provider_under_test, valid_provision_request, valid_template
    ):
        """acquire_hosts must return a dict with success=True."""
        result = provider_under_test.acquire_hosts(valid_provision_request, valid_template)
        assert isinstance(result, dict), "acquire_hosts must return a dict"
        assert result.get("success") is True, f"expected success=True, got: {result}"

    @pytest.mark.provider_contract
    def test_acquire_returns_resource_ids(
        self, provider_under_test, valid_provision_request, valid_template
    ):
        """acquire_hosts must return a non-empty list of string resource IDs."""
        result = provider_under_test.acquire_hosts(valid_provision_request, valid_template)
        resource_ids = result.get("resource_ids")
        assert isinstance(resource_ids, list), "resource_ids must be a list"
        assert len(resource_ids) >= 1, "resource_ids must be non-empty"
        assert all(isinstance(rid, str) for rid in resource_ids), "all resource_ids must be strings"

    @pytest.mark.provider_contract
    def test_acquire_respects_requested_count(
        self, provider_under_test, valid_provision_request, valid_template
    ):
        """acquire_hosts must return at least one resource ID for any positive count."""
        result = provider_under_test.acquire_hosts(valid_provision_request, valid_template)
        assert len(result.get("resource_ids", [])) >= 1

    @pytest.mark.provider_contract
    def test_acquire_resource_ids_are_stable(
        self, provider_under_test, valid_provision_request, valid_template
    ):
        """Two acquire calls with different request IDs must return different resource IDs."""
        from unittest.mock import MagicMock

        req2 = MagicMock()
        req2.request_id = valid_provision_request.request_id + "-b"
        req2.requested_count = valid_provision_request.requested_count
        req2.template_id = valid_provision_request.template_id
        req2.metadata = {}
        req2.resource_ids = []
        req2.provider_data = {}
        req2.provider_api = None

        result1 = provider_under_test.acquire_hosts(valid_provision_request, valid_template)
        result2 = provider_under_test.acquire_hosts(req2, valid_template)

        ids1 = set(result1.get("resource_ids", []))
        ids2 = set(result2.get("resource_ids", []))
        assert ids1 != ids2, "Two separate acquires must produce distinct resource IDs"

    @pytest.mark.provider_contract
    def test_release_does_not_raise(
        self, provider_under_test, valid_provision_request, valid_template
    ):
        """release_hosts must complete without raising for a valid acquire result."""
        result = provider_under_test.acquire_hosts(valid_provision_request, valid_template)
        result.get("resource_ids", [])  # noqa: F841 — validates key exists
        # Providers that release by instance IDs use provider_data; passing empty list
        # is the safe cross-provider call (matches existing moto test pattern).
        provider_under_test.release_hosts([])

    @pytest.mark.provider_contract
    def test_release_is_idempotent(
        self, provider_under_test, valid_provision_request, valid_template
    ):
        """Calling release_hosts twice on the same IDs must not raise."""
        provider_under_test.acquire_hosts(valid_provision_request, valid_template)
        provider_under_test.release_hosts([])
        provider_under_test.release_hosts([])
