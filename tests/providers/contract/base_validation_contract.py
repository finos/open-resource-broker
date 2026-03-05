"""Base validation contract — scenarios every provider must satisfy.

Required fixtures (implement in provider conftest.py):
    validation_adapter  — object implementing ProviderValidationPort
    known_provider_api  — a string the provider recognises (e.g. "ASG" for AWS)
"""

import pytest


class BaseValidationContract:
    """Provider-agnostic validation contract scenarios."""

    # ------------------------------------------------------------------
    # Required fixtures (implement in provider conftest.py):
    #   validation_adapter, known_provider_api
    # ------------------------------------------------------------------
    # Contract scenarios
    # ------------------------------------------------------------------

    @pytest.mark.provider_contract
    def test_get_supported_apis_returns_non_empty_list(self, validation_adapter):
        """get_supported_provider_apis must return at least one entry."""
        apis = validation_adapter.get_supported_provider_apis()
        assert isinstance(apis, list), "get_supported_provider_apis must return a list"
        assert len(apis) >= 1, "get_supported_provider_apis must return at least one API"

    @pytest.mark.provider_contract
    def test_validate_known_api_returns_true(self, validation_adapter, known_provider_api):
        """validate_provider_api must return True for a known API."""
        result = validation_adapter.validate_provider_api(known_provider_api)
        assert result is True, (
            f"expected validate_provider_api({known_provider_api!r}) to return True"
        )

    @pytest.mark.provider_contract
    def test_validate_unknown_api_returns_false(self, validation_adapter):
        """validate_provider_api must return False for an unknown API."""
        result = validation_adapter.validate_provider_api("NONEXISTENT_API_XYZ")
        assert result is False, (
            "expected validate_provider_api('NONEXISTENT_API_XYZ') to return False"
        )

    @pytest.mark.provider_contract
    def test_get_provider_type_returns_string(self, validation_adapter):
        """get_provider_type must return a non-empty string."""
        provider_type = validation_adapter.get_provider_type()
        assert isinstance(provider_type, str), "get_provider_type must return a string"
        assert len(provider_type) > 0, "get_provider_type must return a non-empty string"

    @pytest.mark.provider_contract
    def test_validate_template_config_returns_result_shape(
        self, validation_adapter, known_provider_api
    ):
        """validate_template_configuration must return a dict with valid, errors, warnings keys."""
        result = validation_adapter.validate_template_configuration(
            {"provider_api": known_provider_api}
        )
        assert isinstance(result, dict), "validate_template_configuration must return a dict"
        assert "valid" in result, "result missing 'valid' key"
        assert "errors" in result, "result missing 'errors' key"
        assert "warnings" in result, "result missing 'warnings' key"
        assert isinstance(result["errors"], list), "'errors' must be a list"
        assert isinstance(result["warnings"], list), "'warnings' must be a list"
