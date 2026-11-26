"""AWS provisioning contract tests — inherits all scenarios from BaseProvisioningContract."""

import pytest

from tests.providers.contract.base_provisioning_contract import BaseProvisioningContract


@pytest.mark.provider_contract
class TestAWSProvisioningContract(BaseProvisioningContract):
    """AWS provider satisfies the provisioning contract (moto-backed)."""
