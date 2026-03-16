"""Tests for ValidateTemplateQuery DTO."""

from orb.application.dto.queries import ValidateTemplateQuery


def test_validate_template_query_without_template_config() -> None:
    query = ValidateTemplateQuery(template_id="my-template")
    assert query.template_config == {}


def test_validate_template_query_with_template_config() -> None:
    query = ValidateTemplateQuery(template_id="my-template", template_config={"key": "val"})
    assert query.template_config == {"key": "val"}
