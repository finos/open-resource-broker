"""AWS monitoring contract tests — inherits all scenarios from BaseMonitoringContract."""

import pytest

from tests.providers.contract.base_monitoring_contract import BaseMonitoringContract


@pytest.mark.provider_contract
class TestAWSMonitoringContract(BaseMonitoringContract):
    """AWS provider satisfies the monitoring contract (moto-backed)."""
