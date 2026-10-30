"""Template provider_name / provider_type format validation."""

import pytest

from orb.domain.template.template_aggregate import Template


@pytest.mark.unit
class TestTemplateProviderFieldFormat:
    def test_valid_provider_fields_accepted(self) -> None:
        template = Template(template_id="t1", provider_name="aws-us_east-1", provider_type="aws")
        assert template.provider_name == "aws-us_east-1"
        assert template.provider_type == "aws"

    @pytest.mark.parametrize("value", ["aws\n", "aws-us-east-1\n", "\n"])
    def test_provider_name_with_trailing_newline_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="provider_name"):
            Template(template_id="t1", provider_name=value, provider_type="aws")

    @pytest.mark.parametrize("value", ["aws\n", "aws2\n"])
    def test_provider_type_with_trailing_newline_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="provider_type"):
            Template(template_id="t1", provider_type=value)

    def test_derived_provider_type_with_trailing_newline_rejected(self) -> None:
        with pytest.raises(ValueError):
            Template(template_id="t1", provider_name="aws\n")

    @pytest.mark.parametrize("name", ["-us-east-1", "-", "--a"])
    def test_provider_name_starting_with_hyphen_does_not_recurse(self, name: str) -> None:
        template = Template(template_id="t1", provider_name=name)
        assert template.provider_name == name
        assert template.provider_type is None

    def test_provider_type_derived_from_name(self) -> None:
        assert Template(template_id="t1", provider_name="aws-us-east-1").provider_type == "aws"
        assert Template(template_id="t1", provider_name="aws").provider_type == "aws"
