"""Unit tests for template contracts, exceptions, and value objects."""

import pytest

from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.template.exceptions import (
    InvalidTemplateConfigurationError,
    TemplateAlreadyExistsError,
    TemplateException,
    TemplateNotFoundError,
    TemplateValidationError,
)
from orb.domain.template.value_objects import ProviderConfiguration, TemplateId

# ---------------------------------------------------------------------------
# TemplateNotFoundError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateNotFoundError:
    def test_message_contains_id(self):
        exc = TemplateNotFoundError("tpl-001")
        assert "tpl-001" in str(exc)

    def test_is_entity_not_found_error(self):
        exc = TemplateNotFoundError("tpl-001")
        assert isinstance(exc, EntityNotFoundError)

    def test_error_code(self):
        exc = TemplateNotFoundError("tpl-001")
        assert exc.error_code == "ENTITY_NOT_FOUND"


# ---------------------------------------------------------------------------
# TemplateAlreadyExistsError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateAlreadyExistsError:
    def test_message_contains_id(self):
        exc = TemplateAlreadyExistsError("tpl-dup")
        assert "tpl-dup" in str(exc)

    def test_error_code(self):
        exc = TemplateAlreadyExistsError("tpl-dup")
        assert exc.error_code == "TEMPLATE_ALREADY_EXISTS"

    def test_details_contain_template_id(self):
        exc = TemplateAlreadyExistsError("tpl-dup")
        assert exc.details["template_id"] == "tpl-dup"

    def test_is_template_exception(self):
        exc = TemplateAlreadyExistsError("x")
        assert isinstance(exc, TemplateException)


# ---------------------------------------------------------------------------
# TemplateValidationError and InvalidTemplateConfigurationError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateSubExceptions:
    def test_template_validation_error_is_domain_exception(self):
        exc = TemplateValidationError("bad schema")
        assert exc.message == "bad schema"

    def test_invalid_template_configuration_error_is_template_exception(self):
        exc = InvalidTemplateConfigurationError("missing key")
        assert isinstance(exc, TemplateException)


# ---------------------------------------------------------------------------
# TemplateContract / TemplateValidationResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateContract:
    def _make_contract(self, **kwargs):
        from orb.domain.base.contracts.template_contract import TemplateContract

        defaults: dict = dict(
            template_id="tpl-001",
            name="My Template",
            provider_api="EC2Fleet",
            configuration={"key": "value"},
        )
        defaults.update(kwargs)
        return TemplateContract(**defaults)  # type: ignore[arg-type]

    def test_creates_valid_contract(self):
        contract = self._make_contract()
        assert contract.template_id == "tpl-001"

    def test_empty_template_id_raises(self):
        with pytest.raises(ValueError, match="template_id"):
            self._make_contract(template_id="")

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            self._make_contract(name="")

    def test_empty_provider_api_raises(self):
        with pytest.raises(ValueError, match="provider_api"):
            self._make_contract(provider_api="")

    def test_optional_fields_default_to_none(self):
        contract = self._make_contract()
        assert contract.created_at is None
        assert contract.version is None
        assert contract.tags is None


@pytest.mark.unit
class TestTemplateValidationResult:
    def _make_result(self, **kwargs):
        from orb.domain.base.contracts.template_contract import TemplateValidationResult

        defaults: dict = dict(
            is_valid=True,
            errors=[],
            warnings=[],
            template_id="tpl-001",
        )
        defaults.update(kwargs)
        return TemplateValidationResult(**defaults)  # type: ignore[arg-type]

    def test_has_errors_returns_true_when_errors_present(self):
        r = self._make_result(is_valid=False, errors=["error1"])
        assert r.has_errors() is True

    def test_has_errors_returns_false_when_no_errors(self):
        r = self._make_result()
        assert r.has_errors() is False

    def test_has_warnings_returns_true_when_warnings_present(self):
        r = self._make_result(warnings=["warn1"])
        assert r.has_warnings() is True

    def test_has_warnings_returns_false_when_no_warnings(self):
        r = self._make_result()
        assert r.has_warnings() is False


# ---------------------------------------------------------------------------
# TemplateId
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateId:
    def test_creates_from_string(self):
        tid = TemplateId(value="tpl-001")
        assert str(tid) == "tpl-001"

    def test_empty_value_raises(self):
        with pytest.raises(Exception):
            TemplateId(value="")

    def test_whitespace_only_raises(self):
        with pytest.raises(Exception):
            TemplateId(value="   ")


# ---------------------------------------------------------------------------
# ProviderConfiguration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderConfiguration:
    def test_get_returns_value(self):
        cfg = ProviderConfiguration({"region": "us-east-1"})
        assert cfg.get("region") == "us-east-1"

    def test_get_returns_default_for_missing_key(self):
        cfg = ProviderConfiguration({})
        assert cfg.get("missing", "fallback") == "fallback"

    def test_to_dict_returns_copy(self):
        data = {"region": "us-east-1"}
        cfg = ProviderConfiguration(data)
        d = cfg.to_dict()
        assert d == data
        d["new_key"] = "val"
        assert "new_key" not in cfg.to_dict()
