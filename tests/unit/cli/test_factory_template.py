"""Unit tests for orb.cli.factories.template_command_factory.TemplateCommandFactory.

Verifies that each factory method produces the correct CQRS query/command
with the right field values.
"""

from __future__ import annotations

import pytest

from orb.cli.factories.template_command_factory import TemplateCommandFactory


@pytest.fixture
def factory() -> TemplateCommandFactory:
    return TemplateCommandFactory()


@pytest.mark.unit
class TestCreateListTemplatesQuery:
    def test_defaults(self, factory):
        q = factory.create_list_templates_query()
        assert q.limit == 50
        assert q.offset == 0
        assert q.active_only is True
        assert q.filter_expressions == []

    def test_limit_capped_at_1000(self, factory):
        q = factory.create_list_templates_query(limit=5000)
        assert q.limit == 1000

    def test_provider_name_passed_through(self, factory):
        q = factory.create_list_templates_query(provider_name="aws-dev")
        assert q.provider_name == "aws-dev"

    def test_filter_expressions_normalised_to_list(self, factory):
        q = factory.create_list_templates_query(filter_expressions=None)
        assert q.filter_expressions == []

    def test_offset_set(self, factory):
        q = factory.create_list_templates_query(offset=10)
        assert q.offset == 10


@pytest.mark.unit
class TestCreateGetTemplateQuery:
    def test_template_id_set(self, factory):
        q = factory.create_get_template_query(template_id="tmpl-1")
        assert q.template_id == "tmpl-1"

    def test_provider_from_kwargs_provider_name(self, factory):
        q = factory.create_get_template_query(template_id="t1", provider_name="aws-prod")
        assert q.provider_name == "aws-prod"

    def test_provider_positional_arg_used_as_fallback(self, factory):
        q = factory.create_get_template_query(template_id="t1", provider="aws-fallback")
        assert q.provider_name == "aws-fallback"

    def test_provider_name_kwarg_takes_precedence_over_provider(self, factory):
        q = factory.create_get_template_query(
            template_id="t1", provider_name="main", provider="fallback"
        )
        assert q.provider_name == "main"


@pytest.mark.unit
class TestCreateCreateTemplateCommand:
    def test_basic_fields(self, factory):
        cmd = factory.create_create_template_command(
            template_id="tmpl-new",
            provider_name="EC2",
            handler_type="ami-123",
            configuration={"key": "val"},
        )
        assert cmd.template_id == "tmpl-new"
        assert cmd.configuration == {"key": "val"}
        # provider_name and handler_type are remapped onto the command:
        # provider_name -> provider_api, handler_type -> image_id.
        assert cmd.provider_api == "EC2"
        assert cmd.image_id == "ami-123"

    def test_description_passed_through(self, factory):
        cmd = factory.create_create_template_command(
            template_id="t",
            provider_name="EC2",
            handler_type="h",
            configuration={},
            description="My desc",
        )
        assert cmd.description == "My desc"


@pytest.mark.unit
class TestCreateUpdateTemplateCommand:
    def test_template_id_set(self, factory):
        cmd = factory.create_update_template_command(template_id="tmpl-upd")
        assert cmd.template_id == "tmpl-upd"

    def test_configuration_defaults_to_empty_dict(self, factory):
        cmd = factory.create_update_template_command(template_id="t")
        assert cmd.configuration == {}

    def test_configuration_passed_through(self, factory):
        cmd = factory.create_update_template_command(template_id="t", configuration={"x": 1})
        assert cmd.configuration == {"x": 1}

    def test_description_optional(self, factory):
        cmd = factory.create_update_template_command(template_id="t", description="desc")
        assert cmd.description == "desc"


@pytest.mark.unit
class TestCreateDeleteTemplateCommand:
    def test_template_id_set(self, factory):
        cmd = factory.create_delete_template_command(template_id="tmpl-del")
        assert cmd.template_id == "tmpl-del"


@pytest.mark.unit
class TestCreateValidateTemplateQuery:
    def test_template_config_required(self, factory):
        q = factory.create_validate_template_query(template_config={"instance_type": "t3.micro"})
        assert q.template_config == {"instance_type": "t3.micro"}

    def test_template_id_optional(self, factory):
        q = factory.create_validate_template_query(template_config={}, template_id="tmpl-val")
        assert q.template_id == "tmpl-val"

    def test_template_id_defaults_to_none(self, factory):
        q = factory.create_validate_template_query(template_config={})
        assert q.template_id is None


@pytest.mark.unit
class TestCreateGetMultipleTemplatesQuery:
    def test_template_ids_set(self, factory):
        q = factory.create_get_multiple_templates_query(template_ids=["t1", "t2"])
        assert set(q.template_ids) == {"t1", "t2"}

    def test_provider_name_optional(self, factory):
        q = factory.create_get_multiple_templates_query(template_ids=["t1"], provider_name="prov")
        assert q.provider_name == "prov"

    def test_active_only_default_true(self, factory):
        q = factory.create_get_multiple_templates_query(template_ids=["t1"])
        assert q.active_only is True

    def test_active_only_false(self, factory):
        q = factory.create_get_multiple_templates_query(template_ids=["t1"], active_only=False)
        assert q.active_only is False
