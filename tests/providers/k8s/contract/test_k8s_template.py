"""K8s template contract tests — inherits all scenarios from BaseTemplateContract."""

import pytest

from tests.providers.contract.base_template_contract import BaseTemplateContract


@pytest.mark.provider_contract
class TestK8sTemplateContract(BaseTemplateContract):
    """K8s provider satisfies the template contract (mock-backed)."""
