"""K8s validation contract tests — inherits all scenarios from BaseValidationContract."""

import pytest

from tests.providers.contract.base_validation_contract import BaseValidationContract


@pytest.mark.provider_contract
class TestK8sValidationContract(BaseValidationContract):
    """K8s provider satisfies the validation contract."""
