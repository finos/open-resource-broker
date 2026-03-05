"""AWS template contract tests — inherits all scenarios from BaseTemplateContract."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from tests.providers.contract.base_template_contract import BaseTemplateContract


@pytest.mark.provider_contract
class TestAWSTemplateContract(BaseTemplateContract):
    """AWS provider satisfies the template contract (moto-backed)."""
