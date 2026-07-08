"""Tests for the consolidated _resolve_provider_api in AWSHandler base class.

Covers all 4 priority levels:
  1. aws_template.provider_api takes highest priority
  2. request.metadata["provider_api"] is next
  3. request.provider_api is next
  4. handler default (_default_provider_api) is the fallback

Tests cover EC2FleetHandler and SpotFleetHandler (two concrete handlers as required).
"""

from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_request(metadata=None, provider_api=None):
    """Build a minimal mock Request with controllable metadata and provider_api."""
    req = MagicMock()
    req.metadata = metadata if metadata is not None else {}
    req.provider_api = provider_api
    return req


def _make_aws_template(provider_api=None):
    """Build a minimal mock AWSTemplate with a controllable provider_api attribute."""
    t = MagicMock()
    t.provider_api = provider_api
    return t


def _make_aws_template_with_enum(value: str):
    """Build a mock AWSTemplate where provider_api is an enum-like object with .value."""
    enum_val = MagicMock()
    enum_val.value = value
    t = MagicMock()
    t.provider_api = enum_val
    return t


def _make_handler(handler_class):
    """Instantiate a concrete AWSHandler subclass with all deps mocked."""
    aws_client = MagicMock()
    logger = MagicMock()
    aws_ops = MagicMock()
    aws_ops.set_retry_method = MagicMock()
    aws_ops.set_pagination_method = MagicMock()
    launch_template_manager = MagicMock()
    return handler_class(
        aws_client=aws_client,
        logger=logger,
        aws_ops=aws_ops,
        launch_template_manager=launch_template_manager,
    )


# ---------------------------------------------------------------------------
# EC2FleetHandler — priority 1: aws_template.provider_api
# ---------------------------------------------------------------------------


class TestEC2FleetHandlerResolveProviderApi:
    """_resolve_provider_api priority tests for EC2FleetHandler."""

    def _make(self):
        from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler

        return _make_handler(EC2FleetHandler)

    # Priority 1: aws_template.provider_api (plain string)
    def test_template_provider_api_plain_string_takes_priority(self):
        handler = self._make()
        request = _make_request(metadata={"provider_api": "FromMeta"}, provider_api="FromReq")
        template = _make_aws_template(provider_api="FromTemplate")
        assert handler._resolve_provider_api(request, template) == "FromTemplate"

    # Priority 1: aws_template.provider_api (enum-like with .value)
    def test_template_provider_api_enum_value_extracted(self):
        handler = self._make()
        request = _make_request()
        template = _make_aws_template_with_enum("EC2Fleet-Enum")
        assert handler._resolve_provider_api(request, template) == "EC2Fleet-Enum"

    # Priority 1 skipped when template is None
    def test_none_template_falls_through_to_metadata(self):
        handler = self._make()
        request = _make_request(metadata={"provider_api": "FromMeta"})
        assert handler._resolve_provider_api(request, None) == "FromMeta"

    # Priority 1 skipped when template.provider_api is None
    def test_template_none_provider_api_falls_through_to_metadata(self):
        handler = self._make()
        request = _make_request(metadata={"provider_api": "FromMeta"})
        template = _make_aws_template(provider_api=None)
        assert handler._resolve_provider_api(request, template) == "FromMeta"

    # Priority 2: metadata["provider_api"]
    def test_metadata_provider_api_used_when_no_template(self):
        handler = self._make()
        request = _make_request(metadata={"provider_api": "FromMeta"}, provider_api="FromReq")
        assert handler._resolve_provider_api(request) == "FromMeta"

    # Priority 3: request.provider_api
    def test_request_provider_api_used_when_no_template_no_metadata(self):
        handler = self._make()
        request = _make_request(metadata={}, provider_api="FromReq")
        assert handler._resolve_provider_api(request) == "FromReq"

    # Priority 4: default
    def test_default_returned_when_all_sources_absent(self):
        handler = self._make()
        request = _make_request(metadata={}, provider_api=None)
        assert handler._resolve_provider_api(request) == "EC2Fleet"

    # Priority 4: default with no template arg
    def test_default_returned_with_empty_metadata_and_no_request_provider_api(self):
        handler = self._make()
        request = _make_request()
        request.provider_api = None
        assert handler._resolve_provider_api(request) == "EC2Fleet"


# ---------------------------------------------------------------------------
# SpotFleetHandler — priority tests
# ---------------------------------------------------------------------------


class TestSpotFleetHandlerResolveProviderApi:
    """_resolve_provider_api priority tests for SpotFleetHandler."""

    def _make(self):
        from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

        return _make_handler(SpotFleetHandler)

    # Priority 1: aws_template.provider_api takes highest priority
    def test_template_provider_api_takes_priority_over_all(self):
        handler = self._make()
        request = _make_request(metadata={"provider_api": "FromMeta"}, provider_api="FromReq")
        template = _make_aws_template(provider_api="TemplateOverride")
        assert handler._resolve_provider_api(request, template) == "TemplateOverride"

    # Priority 1 with enum-like value
    def test_template_enum_provider_api_extracted(self):
        handler = self._make()
        request = _make_request()
        template = _make_aws_template_with_enum("SpotFleet-Enum")
        assert handler._resolve_provider_api(request, template) == "SpotFleet-Enum"

    # Priority 2: metadata["provider_api"]
    def test_metadata_provider_api_overrides_request_provider_api(self):
        handler = self._make()
        request = _make_request(metadata={"provider_api": "MetaSpot"}, provider_api="ReqSpot")
        assert handler._resolve_provider_api(request) == "MetaSpot"

    # Priority 3: request.provider_api
    def test_request_provider_api_used_when_metadata_empty(self):
        handler = self._make()
        request = _make_request(metadata={}, provider_api="ReqSpot")
        assert handler._resolve_provider_api(request) == "ReqSpot"

    # Priority 4: handler default
    def test_default_is_spot_fleet(self):
        handler = self._make()
        request = _make_request(metadata={}, provider_api=None)
        assert handler._resolve_provider_api(request) == "SpotFleet"

    # metadata present but key absent — falls through to request.provider_api
    def test_unrelated_metadata_keys_fall_through_to_request_provider_api(self):
        handler = self._make()
        request = _make_request(metadata={"other_key": "value"}, provider_api="ReqSpot2")
        assert handler._resolve_provider_api(request) == "ReqSpot2"

    # metadata absent entirely (None) — gracefully handled
    def test_none_metadata_does_not_raise(self):
        handler = self._make()
        request = MagicMock()
        request.metadata = None
        request.provider_api = "FromReqDirectly"
        assert handler._resolve_provider_api(request) == "FromReqDirectly"
