"""Tests asserting AWS-specific fields are not top-level attributes on TemplateDTO."""

import ast
import os

import pytest
from pydantic import ValidationError

from orb.infrastructure.template.dtos import TemplateDTO
from orb.providers.aws.domain.template.aws_template_dto_config import AWSTemplateDTOConfig
from orb.providers.aws.domain.template.aws_template_aggregate import (
    ABISInstanceRequirements,
    AWSFleetType,
    AWSRequiredIntegerRange,
    AWSTemplate,
)

# ---------------------------------------------------------------------------
# AST scan — no top-level AWS fields on TemplateDTO
# ---------------------------------------------------------------------------

_DTOS_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../../src/orb/infrastructure/template/dtos.py",
)
_AWS_FIELDS = {"fleet_role", "fleet_type", "percent_on_demand", "abis_instance_requirements"}


def _get_template_dto_field_names() -> set[str]:
    """Parse dtos.py with AST and return all annotated field names on TemplateDTO."""
    with open(os.path.abspath(_DTOS_PATH)) as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TemplateDTO":
            return {
                target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                for target in [stmt.target]
            }
    return set()


class TestTemplateDTONoAWSFields:
    """TemplateDTO must not declare AWS-specific fields as top-level attributes."""

    def test_fleet_role_not_a_top_level_field(self):
        fields = _get_template_dto_field_names()
        assert "fleet_role" not in fields, (
            "fleet_role is an AWS-specific field and must not be a top-level TemplateDTO attribute"
        )

    def test_fleet_type_not_a_top_level_field(self):
        fields = _get_template_dto_field_names()
        assert "fleet_type" not in fields, (
            "fleet_type is an AWS-specific field and must not be a top-level TemplateDTO attribute"
        )

    def test_percent_on_demand_not_a_top_level_field(self):
        fields = _get_template_dto_field_names()
        assert "percent_on_demand" not in fields, (
            "percent_on_demand is an AWS-specific field and must not be a top-level TemplateDTO attribute"
        )

    def test_abis_instance_requirements_not_a_top_level_field(self):
        fields = _get_template_dto_field_names()
        assert "abis_instance_requirements" not in fields, (
            "abis_instance_requirements is an AWS-specific field and must not be a top-level TemplateDTO attribute"
        )

    def test_no_aws_fields_at_all(self):
        fields = _get_template_dto_field_names()
        present = _AWS_FIELDS & fields
        assert not present, f"AWS-specific fields still declared on TemplateDTO: {present}"


# ---------------------------------------------------------------------------
# from_domain() packs AWS fields into provider_config
# ---------------------------------------------------------------------------


def _make_aws_template(**kwargs) -> AWSTemplate:
    defaults = dict(
        template_id="tpl-1",
        image_id="ami-abc",
        instance_type="m5.large",
        subnet_ids=["subnet-1"],
        security_group_ids=["sg-1"],
        provider_api="EC2Fleet",
    )
    defaults.update(kwargs)
    return AWSTemplate(**defaults)


def _provider_config_dict(dto: TemplateDTO) -> dict:
    assert isinstance(dto.provider_config, AWSTemplateDTOConfig)
    return dto.provider_config.model_dump(exclude_none=True)


class TestFromDomainPacksAWSFieldsIntoProviderConfig:
    """TemplateDTO.from_domain() must move AWS fields into provider_config."""

    def test_provider_config_is_typed(self):
        template = _make_aws_template(fleet_type=AWSFleetType.MAINTAIN)
        dto = TemplateDTO.from_domain(template)
        assert isinstance(dto.provider_config, AWSTemplateDTOConfig)

    def test_fleet_type_in_metadata(self):
        template = _make_aws_template(fleet_type=AWSFleetType.MAINTAIN)
        dto = TemplateDTO.from_domain(template)
        assert _provider_config_dict(dto).get("fleet_type") == "maintain", (
            "fleet_type must be stored in provider_config, not as a top-level field"
        )

    def test_fleet_role_in_metadata(self):
        template = _make_aws_template(fleet_role="arn:aws:iam::123:role/MyRole")
        dto = TemplateDTO.from_domain(template)
        assert _provider_config_dict(dto).get("fleet_role") == "arn:aws:iam::123:role/MyRole"

    def test_percent_on_demand_in_metadata(self):
        template = _make_aws_template(price_type="heterogeneous", percent_on_demand=40)
        dto = TemplateDTO.from_domain(template)
        assert _provider_config_dict(dto).get("percent_on_demand") == 40

    def test_abis_instance_requirements_in_metadata(self):
        abis = ABISInstanceRequirements(
            VCpuCount=AWSRequiredIntegerRange(Min=2, Max=8),
            MemoryMiB=AWSRequiredIntegerRange(Min=4096, Max=16384),
        )
        template = _make_aws_template(abis_instance_requirements=abis)
        dto = TemplateDTO.from_domain(template)
        stored = _provider_config_dict(dto).get("abis_instance_requirements")
        assert stored is not None, "abis_instance_requirements must be stored in provider_config"
        assert stored["vcpu_count"]["min"] == 2

    def test_none_fleet_type_not_in_provider_config(self):
        """When fleet_type is None it must not pollute provider_config."""
        template = _make_aws_template(provider_api="RunInstances")
        dto = TemplateDTO.from_domain(template)
        assert "fleet_type" not in _provider_config_dict(dto)
        assert "fleet_type" not in dto.to_template_config()
        assert not hasattr(dto, "fleet_type") or "fleet_type" not in dto.model_fields

    def test_existing_metadata_preserved(self):
        """from_domain must not discard pre-existing metadata on the domain object."""
        template = _make_aws_template(
            fleet_type=AWSFleetType.REQUEST,
            metadata={"custom_key": "custom_value"},
        )
        dto = TemplateDTO.from_domain(template)
        assert dto.metadata.get("custom_key") == "custom_value"
        assert _provider_config_dict(dto).get("fleet_type") == "request"


class TestProviderConfigValidation:
    """Provider config must be backed by a registered provider extension model."""

    def test_unregistered_provider_config_dict_fails_validation(self):
        with pytest.raises(ValidationError):
            TemplateDTO.model_validate(
                {
                    "template_id": "tpl-unregistered",
                    "provider_type": "unregistered-provider",
                    "provider_config": {"provider_specific_field": "value"},
                }
            )

    def test_typed_provider_config_must_match_registered_provider_type(self):
        with pytest.raises(ValidationError):
            TemplateDTO(
                template_id="tpl-unregistered",
                provider_type="unregistered-provider",
                provider_config=AWSTemplateDTOConfig.model_validate({}),
            )


# ---------------------------------------------------------------------------
# AWSTemplate round-trip via TemplateDTO
# ---------------------------------------------------------------------------


class TestAWSTemplateRoundTrip:
    """AWSTemplate → TemplateDTO → AWSTemplate must preserve AWS fields."""

    def test_fleet_type_maintain_roundtrip(self):
        original = _make_aws_template(fleet_type=AWSFleetType.MAINTAIN)
        dto = TemplateDTO.from_domain(original)
        restored = AWSTemplate.model_validate(dto.to_template_config())
        assert restored.fleet_type == AWSFleetType.MAINTAIN

    def test_fleet_type_request_roundtrip(self):
        original = _make_aws_template(fleet_type=AWSFleetType.REQUEST)
        dto = TemplateDTO.from_domain(original)
        restored = AWSTemplate.model_validate(dto.to_template_config())
        assert restored.fleet_type == AWSFleetType.REQUEST

    def test_fleet_role_roundtrip(self):
        role = "arn:aws:iam::123456789:role/SpotFleetRole"
        original = _make_aws_template(fleet_role=role)
        dto = TemplateDTO.from_domain(original)
        restored = AWSTemplate.model_validate(dto.to_template_config())
        assert restored.fleet_role == role

    def test_percent_on_demand_roundtrip(self):
        original = _make_aws_template(price_type="heterogeneous", percent_on_demand=60)
        dto = TemplateDTO.from_domain(original)
        restored = AWSTemplate.model_validate(dto.to_template_config())
        assert restored.percent_on_demand == 60

    def test_abis_roundtrip(self):
        abis = ABISInstanceRequirements(
            VCpuCount=AWSRequiredIntegerRange(Min=4, Max=16),
            MemoryMiB=AWSRequiredIntegerRange(Min=8192, Max=32768),
        )
        original = _make_aws_template(abis_instance_requirements=abis)
        dto = TemplateDTO.from_domain(original)
        restored = AWSTemplate.model_validate(dto.to_template_config())
        assert restored.abis_instance_requirements is not None
        assert restored.abis_instance_requirements.vcpu_count.min == 4


# ---------------------------------------------------------------------------
# application/dto/template_dto.py must not exist
# ---------------------------------------------------------------------------

_APP_DTO_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../../src/orb/application/dto/template_dto.py",
)


class TestApplicationDTODeleted:
    """application/dto/template_dto.py is dead code and must be deleted."""

    def test_application_template_dto_file_does_not_exist(self):
        assert not os.path.exists(os.path.abspath(_APP_DTO_PATH)), (
            "src/orb/application/dto/template_dto.py is dead code (zero importers) "
            "and must be deleted"
        )
