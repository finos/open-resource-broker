"""AWS validation contract tests — inherits all scenarios from BaseValidationContract."""

import pytest

from tests.providers.contract.base_validation_contract import BaseValidationContract


@pytest.mark.provider_contract
class TestAWSValidationContract(BaseValidationContract):
    """AWS provider satisfies the validation contract."""
