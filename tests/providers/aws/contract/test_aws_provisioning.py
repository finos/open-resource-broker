"""AWS provisioning contract tests — inherits all scenarios from BaseProvisioningContract."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from tests.providers.contract.base_provisioning_contract import BaseProvisioningContract


@pytest.mark.provider_contract
class TestAWSProvisioningContract(BaseProvisioningContract):
    """AWS provider satisfies the provisioning contract (moto-backed)."""
