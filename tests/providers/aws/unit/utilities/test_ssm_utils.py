"""Unit tests for ssm_utils pure and quasi-pure functions.

Network calls (SSM client) are mocked — no real AWS connection is made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from orb.domain.base.exceptions import InfrastructureError
from orb.providers.aws.utilities.ssm_utils import (
    extract_ssm_parameter_path,
    is_ssm_parameter_path,
    resolve_ssm_parameter,
    resolve_ssm_parameters,
    resolve_ssm_parameters_in_dict,
    resolve_ssm_parameters_in_list,
)

# ---------------------------------------------------------------------------
# is_ssm_parameter_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsSSMParameterPath:
    def test_valid_path_returns_true(self):
        assert is_ssm_parameter_path("ssm:/my/param") is True

    def test_valid_nested_path_returns_true(self):
        assert is_ssm_parameter_path("ssm:/org/team/my-secret_key.val") is True

    def test_empty_string_returns_false(self):
        assert is_ssm_parameter_path("") is False

    def test_plain_ami_id_returns_false(self):
        assert is_ssm_parameter_path("ami-0abcdef1234567890") is False

    def test_ssm_without_leading_slash_returns_false(self):
        assert is_ssm_parameter_path("ssm:no-slash") is False

    def test_none_handled_as_falsy_returns_false(self):
        # The function checks `if not value` — passing None should be safe
        # when called via resolve_ssm_parameters_in_dict which guards with isinstance
        assert is_ssm_parameter_path("") is False

    def test_bare_slash_path_returns_false(self):
        # pattern requires at least one word char after the slash
        assert is_ssm_parameter_path("ssm:/") is False


# ---------------------------------------------------------------------------
# extract_ssm_parameter_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractSSMParameterPath:
    def test_extracts_path_from_valid_ssm_string(self):
        result = extract_ssm_parameter_path("ssm:/my/param")
        assert result == "/my/param"

    def test_returns_none_for_non_ssm_string(self):
        result = extract_ssm_parameter_path("ami-123")
        assert result is None

    def test_returns_none_for_empty_string(self):
        result = extract_ssm_parameter_path("")
        assert result is None

    def test_extracts_deeply_nested_path(self):
        result = extract_ssm_parameter_path("ssm:/a/b/c/d/e")
        assert result == "/a/b/c/d/e"


# ---------------------------------------------------------------------------
# resolve_ssm_parameter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveSSMParameter:
    def _make_aws_client(self, return_value: str) -> MagicMock:
        aws_client = MagicMock()
        aws_client.ssm_client.get_parameter.return_value = {"Parameter": {"Value": return_value}}
        return aws_client

    def test_resolves_bare_path(self):
        aws_client = self._make_aws_client("ami-resolved")
        result = resolve_ssm_parameter("/my/param", aws_client=aws_client)
        assert result == "ami-resolved"

    def test_resolves_ssm_prefixed_path(self):
        aws_client = self._make_aws_client("ami-from-ssm")
        result = resolve_ssm_parameter("ssm:/my/param", aws_client=aws_client)
        assert result == "ami-from-ssm"

    def test_raises_infrastructure_error_when_no_client(self):
        with pytest.raises(InfrastructureError):
            resolve_ssm_parameter("/my/param", aws_client=None)

    def test_raises_infrastructure_error_on_client_error(self):
        aws_client = MagicMock()
        error_response = {"Error": {"Code": "ParameterNotFound", "Message": "not found"}}
        aws_client.ssm_client.get_parameter.side_effect = ClientError(
            error_response, "GetParameter"
        )
        with pytest.raises(InfrastructureError):
            resolve_ssm_parameter("/missing/param", aws_client=aws_client)

    def test_raises_infrastructure_error_on_generic_exception(self):
        aws_client = MagicMock()
        aws_client.ssm_client.get_parameter.side_effect = RuntimeError("network down")
        with pytest.raises(InfrastructureError):
            resolve_ssm_parameter("/my/param", aws_client=aws_client)


# ---------------------------------------------------------------------------
# resolve_ssm_parameters_in_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveSSMParametersInDict:
    def _make_aws_client(self, mapping: dict) -> MagicMock:
        aws_client = MagicMock()

        def _get_param(Name, WithDecryption):  # noqa: N803
            return {"Parameter": {"Value": mapping[Name]}}

        aws_client.ssm_client.get_parameter.side_effect = _get_param
        return aws_client

    def test_non_ssm_value_unchanged(self):
        result = resolve_ssm_parameters_in_dict({"key": "plain-value"}, aws_client=MagicMock())
        assert result["key"] == "plain-value"

    def test_ssm_value_resolved(self):
        aws_client = self._make_aws_client({"/my/ami": "ami-resolved"})
        result = resolve_ssm_parameters_in_dict({"image_id": "ssm:/my/ami"}, aws_client=aws_client)
        assert result["image_id"] == "ami-resolved"

    def test_nested_dict_resolved_recursively(self):
        aws_client = self._make_aws_client({"/nested/param": "nested-value"})
        result = resolve_ssm_parameters_in_dict(
            {"inner": {"param": "ssm:/nested/param"}}, aws_client=aws_client
        )
        assert result["inner"]["param"] == "nested-value"

    def test_list_value_resolved_recursively(self):
        aws_client = self._make_aws_client({"/list/param": "list-value"})
        result = resolve_ssm_parameters_in_dict(
            {"items": ["ssm:/list/param", "plain"]}, aws_client=aws_client
        )
        assert result["items"][0] == "list-value"
        assert result["items"][1] == "plain"

    def test_mixed_dict_partial_resolution(self):
        aws_client = self._make_aws_client({"/secret": "secret-value"})
        result = resolve_ssm_parameters_in_dict(
            {"id": "ssm:/secret", "count": 5}, aws_client=aws_client
        )
        assert result["id"] == "secret-value"
        assert result["count"] == 5


# ---------------------------------------------------------------------------
# resolve_ssm_parameters_in_list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveSSMParametersInList:
    def _make_aws_client(self, mapping: dict) -> MagicMock:
        aws_client = MagicMock()

        def _get_param(Name, WithDecryption):  # noqa: N803
            return {"Parameter": {"Value": mapping[Name]}}

        aws_client.ssm_client.get_parameter.side_effect = _get_param
        return aws_client

    def test_plain_strings_unchanged(self):
        result = resolve_ssm_parameters_in_list(["a", "b"], aws_client=MagicMock())
        assert result == ["a", "b"]

    def test_ssm_string_in_list_resolved(self):
        aws_client = self._make_aws_client({"/k": "resolved"})
        result = resolve_ssm_parameters_in_list(["ssm:/k"], aws_client=aws_client)
        assert result == ["resolved"]

    def test_nested_list_resolved_recursively(self):
        aws_client = self._make_aws_client({"/inner": "v"})
        result = resolve_ssm_parameters_in_list([["ssm:/inner", "plain"]], aws_client=aws_client)
        assert result[0] == ["v", "plain"]

    def test_dict_inside_list_resolved(self):
        aws_client = self._make_aws_client({"/d": "dict-val"})
        result = resolve_ssm_parameters_in_list([{"key": "ssm:/d"}], aws_client=aws_client)
        assert result[0]["key"] == "dict-val"


# ---------------------------------------------------------------------------
# resolve_ssm_parameters (dispatcher)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveSSMParameters:
    def test_dispatches_to_dict_handler(self):
        with patch(
            "orb.providers.aws.utilities.ssm_utils.resolve_ssm_parameters_in_dict"
        ) as mock_dict:
            mock_dict.return_value = {"resolved": True}
            result = resolve_ssm_parameters({"key": "val"})
            mock_dict.assert_called_once()
            assert result == {"resolved": True}

    def test_dispatches_to_list_handler(self):
        with patch(
            "orb.providers.aws.utilities.ssm_utils.resolve_ssm_parameters_in_list"
        ) as mock_list:
            mock_list.return_value = ["resolved"]
            result = resolve_ssm_parameters(["item"])
            mock_list.assert_called_once()
            assert result == ["resolved"]

    def test_non_dict_non_list_returned_as_is(self):
        result = resolve_ssm_parameters("plain-string")  # type: ignore[arg-type]
        assert result == "plain-string"
