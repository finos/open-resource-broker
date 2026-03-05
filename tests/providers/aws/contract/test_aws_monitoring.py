"""AWS monitoring contract tests — inherits all scenarios from BaseMonitoringContract."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from tests.providers.contract.base_monitoring_contract import BaseMonitoringContract


@pytest.mark.provider_contract
class TestAWSMonitoringContract(BaseMonitoringContract):
    """AWS provider satisfies the monitoring contract (moto-backed)."""
