"""Coverage for spot eviction state transitions and 429/Retry-After throttling.

These exercise the end-to-end CREATE_INSTANCES / GET_INSTANCE_STATUS /
TERMINATE_INSTANCES flows through ``AzureProviderStrategy.execute_operation``,
with the handler layer mocked at the same seam used throughout
``test_azure_strategy_lifecycle.py`` and ``test_azure_strategy_status.py``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from azure.core.exceptions import HttpResponseError

from orb.providers.azure.infrastructure.handlers.azure_status import resolve_power_state
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from tests.providers.azure.strategy_test_support import build_strategy_harness, run_operation


def _spot_template_config(**overrides) -> dict:
    config = {
        "template_id": "azure-spot-vmss",
        "provider_api": "VMSS",
        "resource_group": "test-rg",
        "location": "eastus2",
        "vm_size": "Standard_D4s_v5",
        "price_type": "spot",
        "priority": "Spot",
        "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
        "image": {
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
    }
    config.update(overrides)
    return config


def _retry_after_http_error(retry_after_seconds: str = "30") -> HttpResponseError:
    response = MagicMock()
    response.status_code = 429
    response.headers = {"Retry-After": retry_after_seconds}
    return HttpResponseError(message="Too many requests", response=response)


class TestSpotEvictionStateTransitions:
    """PowerState mapping for the Azure spot-eviction teardown sequence."""

    def test_resolve_power_state_tracks_full_eviction_sequence(self) -> None:
        """Azure evicts a spot VM by cycling it through PowerState/*.

        running -> stopping -> deallocating -> deallocated is the observed
        sequence for a spot eviction (Azure stops, then deallocates, the
        VM on the host's behalf). Each step must map to the matching ORB
        domain status.
        """
        eviction_sequence = [
            ("PowerState/running", "running"),
            ("PowerState/stopping", "stopping"),
            ("PowerState/deallocating", "shutting-down"),
            ("PowerState/deallocated", "stopped"),
        ]

        for code, expected_status in eviction_sequence:
            statuses = [SimpleNamespace(code=code)]
            assert resolve_power_state(statuses) == expected_status

    def test_create_then_poll_then_terminate_handles_mid_lifecycle_eviction(
        self, azure_config, logger
    ) -> None:
        """End-to-end: create a spot VM, observe it get evicted mid-poll, then
        terminate it — terminate must still succeed once the VM is already
        deallocated by Azure's eviction process.
        """
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy

        create_handler = MagicMock()
        create_handler.acquire_hosts_async = AsyncMock(
            return_value={
                "success": True,
                "resource_ids": ["vmss-spot-a"],
                "instances": [{"instance_id": "3"}],
            }
        )
        strategy_harness.handlers["VMSS"] = create_handler

        create_op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1, "template_config": _spot_template_config()},
        )
        create_result = run_operation(strategy.execute_operation(create_op))
        assert create_result.success

        # Simulate successive status polls as Azure evicts the spot VM.
        status_handler = MagicMock()
        status_handler.check_hosts_status_async = AsyncMock(
            side_effect=[
                [{"instance_id": "3", "status": "running"}],
                [{"instance_id": "3", "status": "stopping"}],
                [{"instance_id": "3", "status": "stopped"}],
            ]
        )
        strategy_harness.handlers["VMSS"] = status_handler

        observed_statuses = []
        for _ in range(3):
            status_op = ProviderOperation(
                operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
                parameters={
                    "instance_ids": ["3"],
                    "provider_api": "VMSS",
                    "request_metadata": {"resource_group": "test-rg"},
                    "resource_mapping": {"3": ("vmss-spot-a", 3)},
                },
            )
            status_result = run_operation(strategy.execute_operation(status_op))
            assert status_result.success
            observed_statuses.append(status_result.data["instances"][0]["status"])

        assert observed_statuses == ["running", "stopping", "stopped"]

        # Once evicted (deallocated/stopped), terminate must still succeed —
        # the resource is already gone from Azure's perspective.
        terminate_handler = MagicMock()
        terminate_handler.release_hosts_async = AsyncMock(return_value=None)
        strategy_harness.handlers["VMSS"] = terminate_handler

        terminate_op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["3"],
                "provider_api": "VMSS",
                "resource_mapping": {"3": ("vmss-spot-a", 3)},
            },
        )
        terminate_result = run_operation(strategy.execute_operation(terminate_op))

        assert terminate_result.success
        terminate_handler.release_hosts_async.assert_awaited_once()


class TestThrottlingInCreateAndTerminateFlows:
    """429/Retry-After propagation through end-to-end create/terminate flows."""

    def test_create_instances_surfaces_429_as_too_many_requests(self, azure_config, logger) -> None:
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy

        handler = MagicMock()
        handler.acquire_hosts_async = AsyncMock(side_effect=_retry_after_http_error("30"))
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"count": 1, "template_config": _spot_template_config()},
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        fleet_errors = result.metadata["provider_data"]["fleet_errors"]
        assert fleet_errors[0]["error_code"] == "TooManyRequests"
        assert fleet_errors[0]["status_code"] == 429

    def test_terminate_instances_surfaces_429_throttling_failure(
        self, azure_config, logger
    ) -> None:
        strategy_harness = build_strategy_harness(config=azure_config, logger=logger)
        strategy = strategy_harness.strategy

        handler = MagicMock()
        handler.release_hosts_async = AsyncMock(side_effect=_retry_after_http_error("15"))
        strategy_harness.handlers["VMSS"] = handler

        op = ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "instance_ids": ["orb-1"],
                "provider_api": "VMSS",
                "resource_mapping": {"orb-1": ("vmss-prod-b", 1)},
            },
        )

        result = run_operation(strategy.execute_operation(op))

        assert not result.success
        assert result.metadata["error_class"] == "HttpResponseError"
        handler.release_hosts_async.assert_awaited_once()
