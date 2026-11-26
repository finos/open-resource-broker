"""AWS template contract tests — inherits all scenarios from BaseTemplateContract."""

import pytest

from tests.providers.contract.base_template_contract import BaseTemplateContract


@pytest.mark.provider_contract
class TestAWSTemplateContract(BaseTemplateContract):
    """AWS provider satisfies the template contract (moto-backed)."""
