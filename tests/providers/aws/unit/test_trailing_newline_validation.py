"""Identifier validators must reject a trailing newline.

``$`` matches before a trailing ``\\n`` under ``re.match``, so values like
``"t3.micro\\n"`` used to pass validation. These tests pin ``fullmatch``
behaviour for the AWS format checks.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from orb.infrastructure.validation.input_validator import ValidationError
from orb.providers.aws.domain.template.value_objects import AWSARN, AWSInstanceType
from orb.providers.aws.infrastructure.adapters.template_adapter import AWSTemplateAdapter
from orb.providers.aws.strategy.aws_provider_adapter import AWSResourceValidator
from orb.providers.aws.validation.region_validator import validate_aws_region

_VALID_ARN = "arn:aws:iam::123456789012:role/example"


@pytest.mark.unit
class TestAWSValueObjectsRejectTrailingNewline:
    def test_instance_type_with_trailing_newline_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AWSInstanceType(value="t3.micro\n")

    def test_instance_type_valid_accepted(self) -> None:
        assert AWSInstanceType(value="t3.micro").value == "t3.micro"

    def test_arn_with_trailing_newline_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            AWSARN(value=_VALID_ARN + "\n")

    def test_arn_valid_accepted(self) -> None:
        assert AWSARN(value=_VALID_ARN).value == _VALID_ARN


@pytest.mark.unit
class TestAWSTemplateAdapterFormatChecks:
    @pytest.fixture
    def adapter(self) -> AWSTemplateAdapter:
        return AWSTemplateAdapter.__new__(AWSTemplateAdapter)

    @pytest.mark.parametrize(
        "method,valid",
        [
            ("_is_valid_ami_format", "ami-0123456789abcdef0"),
            ("_is_valid_instance_type", "t3.micro"),
            ("_is_valid_subnet_format", "subnet-0123456789abcdef0"),
            ("_is_valid_security_group_format", "sg-0123456789abcdef0"),
        ],
    )
    def test_trailing_newline_rejected(
        self, adapter: AWSTemplateAdapter, method: str, valid: str
    ) -> None:
        check = getattr(adapter, method)
        assert check(valid) is True
        assert check(valid + "\n") is False


@pytest.mark.unit
class TestAWSRegionValidator:
    def test_valid_region_accepted(self) -> None:
        assert validate_aws_region("us-east-1") == "us-east-1"

    def test_trailing_newline_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_aws_region("us-east-1\n")


@pytest.mark.unit
class TestAWSResourceValidator:
    @pytest.mark.parametrize(
        "resource_type,valid",
        [
            ("instance", "i-0123456789abcdef0"),
            ("volume", "vol-0123456789abcdef0"),
            ("subnet", "subnet-0123456789abcdef0"),
            ("security_group", "sg-0123456789abcdef0"),
            ("vpc", "vpc-0123456789abcdef0"),
            ("ami", "ami-0123456789abcdef0"),
            ("launch_template", "lt-0123456789abcdef0"),
            ("key_pair", "my-key_1"),
            ("arn", "arn:aws:s3:::example-bucket"),
        ],
    )
    def test_trailing_newline_rejected(self, resource_type: str, valid: str) -> None:
        validator = AWSResourceValidator()
        assert validator.validate_resource_identifier(valid, resource_type) is True
        assert validator.validate_resource_identifier(valid + "\n", resource_type) is False
