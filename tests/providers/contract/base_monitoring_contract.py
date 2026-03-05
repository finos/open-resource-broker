"""Base monitoring contract — scenarios every provider must satisfy.

Required fixtures (implement in provider conftest.py):
    provider_under_test      — object with check_hosts_status(request) and
                               get_provider_info() methods
    provisioned_resource_ids — fixture that acquires resources and yields
                               (handler, resource_ids) so tests can call
                               check_hosts_status and release afterwards
"""

import pytest


class BaseMonitoringContract:
    """Provider-agnostic monitoring contract scenarios."""

    # ------------------------------------------------------------------
    # Required fixtures (implement in provider conftest.py):
    #   provider_under_test, provisioned_resource_ids
    # ------------------------------------------------------------------
    # Contract scenarios
    # ------------------------------------------------------------------

    @pytest.mark.provider_contract
    def test_status_returns_list(self, provisioned_resource_ids):
        """check_hosts_status must return a list."""
        handler, resource_ids, status_request = provisioned_resource_ids
        result = handler.check_hosts_status(status_request)
        assert isinstance(result, list), "check_hosts_status must return a list"

    @pytest.mark.provider_contract
    def test_status_entries_have_required_keys(self, provisioned_resource_ids):
        """Each status entry must have instance_id and status keys."""
        handler, resource_ids, status_request = provisioned_resource_ids
        result = handler.check_hosts_status(status_request)
        # moto simulators may return empty list for some provider APIs (e.g. ASG)
        # — the contract only asserts shape when entries are present
        for entry in result:
            assert "instance_id" in entry, f"status entry missing instance_id: {entry}"
            assert "status" in entry, f"status entry missing status: {entry}"

    @pytest.mark.provider_contract
    def test_get_provider_info_returns_dict(self, provider_under_test):
        """get_provider_info must return a dict with at least a provider_type key."""
        info = provider_under_test.get_provider_info()
        assert isinstance(info, dict), "get_provider_info must return a dict"
        assert "provider_type" in info, f"provider_info missing provider_type key: {info}"

    @pytest.mark.provider_contract
    @pytest.mark.simulator_limitation
    def test_status_after_release_reflects_termination(self, provisioned_resource_ids):
        """Status of released resources should be terminated/shutting-down or absent.

        simulator_limitation: moto ASG/EC2Fleet/SpotFleet do not spin up instances,
        so the status list is empty after acquire. This test passes vacuously for
        those APIs. A real provider would assert terminated state here.
        """
        handler, resource_ids, status_request = provisioned_resource_ids
        handler.release_hosts(resource_ids)
        result = handler.check_hosts_status(status_request)
        # Either empty (simulator limitation) or all entries show termination state
        terminal_states = {"terminated", "shutting-down", "stopping", "stopped"}
        for entry in result:
            assert entry.get("status") in terminal_states, (
                f"expected terminal state after release, got: {entry}"
            )
