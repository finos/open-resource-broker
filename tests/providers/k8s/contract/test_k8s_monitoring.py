"""K8s monitoring contract tests — inherits all scenarios from BaseMonitoringContract."""

import pytest

from tests.providers.contract.base_monitoring_contract import BaseMonitoringContract


@pytest.mark.provider_contract
class TestK8sMonitoringContract(BaseMonitoringContract):
    """K8s provider satisfies the monitoring contract (mock-backed)."""
