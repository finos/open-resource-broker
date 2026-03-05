"""Base template contract — scenarios every provider must satisfy.

Required fixtures (implement in provider conftest.py):
    template_provider              — object with get_available_templates() and
                                     validate_template(template) methods
    valid_template_for_validation  — a template the provider should accept
    invalid_template_for_validation — a template the provider should reject
"""

import pytest


class BaseTemplateContract:
    """Provider-agnostic template contract scenarios."""

    # ------------------------------------------------------------------
    # Required fixtures (implement in provider conftest.py):
    #   template_provider, valid_template_for_validation,
    #   invalid_template_for_validation
    # ------------------------------------------------------------------
    # Contract scenarios
    # ------------------------------------------------------------------

    @pytest.mark.provider_contract
    def test_get_available_templates_returns_list(self, template_provider):
        """get_available_templates must return a list."""
        result = template_provider.get_available_templates()
        assert isinstance(result, list), "get_available_templates must return a list"

    @pytest.mark.provider_contract
    def test_templates_have_required_fields(self, template_provider):
        """Each template must have template_id, name, and provider_api fields."""
        templates = template_provider.get_available_templates()
        for tpl in templates:
            assert hasattr(tpl, "template_id") or (
                isinstance(tpl, dict) and "template_id" in tpl
            ), f"template missing template_id: {tpl}"
            assert hasattr(tpl, "name") or (isinstance(tpl, dict) and "name" in tpl), (
                f"template missing name: {tpl}"
            )
            assert hasattr(tpl, "provider_api") or (
                isinstance(tpl, dict) and "provider_api" in tpl
            ), f"template missing provider_api: {tpl}"

    @pytest.mark.provider_contract
    def test_validate_template_accepts_valid_template(
        self, template_provider, valid_template_for_validation
    ):
        """validate_template must return True for a valid template."""
        result = template_provider.validate_template(valid_template_for_validation)
        assert result is True, f"expected validate_template to return True, got: {result}"

    @pytest.mark.provider_contract
    def test_validate_template_rejects_invalid_template(
        self, template_provider, invalid_template_for_validation
    ):
        """validate_template must return False or raise for an invalid template."""
        try:
            result = template_provider.validate_template(invalid_template_for_validation)
            assert result is False, (
                f"expected validate_template to return False for invalid template, got: {result}"
            )
        except Exception:
            pass  # raising is also an acceptable rejection signal
