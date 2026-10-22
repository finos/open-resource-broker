"""Verify usable worker addresses survive OCI status normalization."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler


def make_handler():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    handler._oci_cli_available = True
    return handler


def test_running_worker_uses_primary_vnic_and_real_dns_suffix():
    handler = make_handler()
    handler._run_oci = MagicMock(
        side_effect=[
            {"data": {"lifecycle-state": "RUNNING", "shape": "VM.Standard.E4.Flex"}},
            {
                "data": [
                    {"is-primary": False, "private-ip": "10.1.0.2"},
                    {
                        "is-primary": True,
                        "private-ip": "10.0.0.2",
                        "public-ip": None,
                        "hostname-label": "worker",
                        "subnet-id": "subnet-1",
                        "nsg-ids": ["nsg-1"],
                    },
                ]
            },
            {"data": {"subnet-domain-name": "workers.example.oraclevcn.com"}},
        ]
    )
    result = handler.get_instance_status(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["instance-1"]},
        )
    )
    assert result.success
    worker = result.data["instances"][0]
    assert worker["private_ip"] == "10.0.0.2"
    assert worker["private_dns_name"] == "worker.workers.example.oraclevcn.com"
    assert worker["public_ip"] is None
    assert worker["security_group_ids"] == ["nsg-1"]
    assert worker["subnet_id"] == "subnet-1"
    assert result.metadata["provider_fulfilment"].state == "fulfilled"


@pytest.mark.parametrize("state", ["PROVISIONING", "TERMINATING", "TERMINATED"])
def test_non_running_worker_does_not_require_an_attached_vnic(state):
    handler = make_handler()
    handler._run_oci = MagicMock(return_value={"data": {"lifecycle-state": state}})
    result = handler.get_instance_status(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["instance-1"]},
        )
    )
    assert result.success
    handler._run_oci.assert_called_once()


def test_network_permission_error_is_reported_instead_of_hiding_missing_addresses():
    handler = make_handler()
    handler._run_oci = MagicMock(
        side_effect=[{"data": {"lifecycle-state": "RUNNING"}}, RuntimeError("access denied")]
    )
    result = handler.get_instance_status(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["instance-1"]},
        )
    )
    assert not result.success
    assert result.error_code == "OCI_STATUS_FAILED"


def test_fulfilment_includes_requested_capacity_not_yet_discovered():
    handler = make_handler()
    handler._run_oci = MagicMock(return_value={"data": {"lifecycle-state": "RUNNING"}})
    handler._get_instance_network = MagicMock(return_value={"private_ip": "10.0.0.2"})
    result = handler.get_instance_status(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["instance-1"], "requested_count": 2},
        )
    )
    fulfilment = result.metadata["provider_fulfilment"]
    assert fulfilment.state == "in_progress"
    assert fulfilment.target_units == 2
    assert fulfilment.running_count == 1
    assert fulfilment.pending_count == 1


def test_termination_fulfils_return_instead_of_failing_acquisition():
    handler = make_handler()
    handler._run_oci = MagicMock(return_value={"data": {"lifecycle-state": "TERMINATED"}})
    result = handler.get_instance_status(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "instance_ids": ["instance-1"],
                "request": SimpleNamespace(
                    requested_count=1, request_type=SimpleNamespace(value="return")
                ),
            },
        )
    )
    fulfilment = result.metadata["provider_fulfilment"]
    assert fulfilment.state == "fulfilled"
    assert fulfilment.fulfilled_units == 1
    assert fulfilment.running_count == 0
