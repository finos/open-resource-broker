"""AWS validation contract tests — inherits all scenarios from BaseValidationContract."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from tests.providers.contract.base_validation_contract import BaseValidationContract


@pytest.mark.provider_contract
class TestAWSValidationContract(BaseValidationContract):
    """AWS provider satisfies the validation contract."""
