"""Unit tests for application/events/aws_handlers.py.

Tests verify each handler function emits the correct log level and message
format for all code paths, including metadata fallback.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.events import DomainEvent
from orb.providers.aws.application.events.aws_handlers import (
    AWS_EVENT_HANDLERS,
    handle_aws_client_operation,
    handle_aws_credentials_event,
    handle_aws_rate_limit,
)

# ---------------------------------------------------------------------------
# Helper: build a minimal DomainEvent-like object
# ---------------------------------------------------------------------------


def _event(**kwargs) -> DomainEvent:
    """Create a real DomainEvent with extra fields passed as metadata."""
    metadata = {k: v for k, v in kwargs.items()}
    return DomainEvent(
        aggregate_id="agg-1",
        aggregate_type="Test",
        metadata=metadata,
    )


def _event_with_attrs(**attrs) -> DomainEvent:
    """Create an event where fields are real attributes (via subclass)."""
    mock_event = MagicMock(spec=DomainEvent)
    mock_event.metadata = {}
    for k, v in attrs.items():
        setattr(mock_event, k, v)
    return mock_event  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# handle_aws_client_operation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleAWSClientOperation:
    def test_logs_info_on_success(self):
        event = _event(service="ec2", operation="RunInstances", success=True)
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_client_operation(event)
            mock_log.info.assert_called_once()
            message = mock_log.info.call_args[0][0]
            assert "ec2.RunInstances" in message
            assert "True" in message

    def test_logs_warning_on_failure(self):
        event = _event(service="s3", operation="PutObject", success=False)
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_client_operation(event)
            mock_log.warning.assert_called_once()
            message = mock_log.warning.call_args[0][0]
            assert "s3.PutObject" in message

    def test_region_included_when_present(self):
        event = _event(
            service="ec2", operation="DescribeInstances", success=True, region="us-east-1"
        )
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_client_operation(event)
            message = mock_log.info.call_args[0][0]
            assert "us-east-1" in message

    def test_region_absent_when_not_in_event(self):
        event = _event(service="ec2", operation="DescribeFleets", success=True)
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_client_operation(event)
            message = mock_log.info.call_args[0][0]
            assert "Region" not in message

    def test_request_id_included_when_present(self):
        event = _event(
            service="ec2",
            operation="TerminateInstances",
            success=True,
            request_id="req-xyz",
        )
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_client_operation(event)
            message = mock_log.info.call_args[0][0]
            assert "req-xyz" in message

    def test_defaults_used_when_fields_missing(self):
        # Use a mock where all attributes return None to force metadata fallback
        # which also has nothing → defaults kick in.
        mock_event = MagicMock(spec=DomainEvent)
        mock_event.service = None
        mock_event.operation = None
        mock_event.success = None
        mock_event.region = None
        mock_event.request_id = None
        mock_event.metadata = {}
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_client_operation(mock_event)
            # success defaults to False → warning
            mock_log.warning.assert_called_once()
            message = mock_log.warning.call_args[0][0]
            assert "unknown.unknown" in message


# ---------------------------------------------------------------------------
# handle_aws_rate_limit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleAWSRateLimit:
    def test_logs_warning_with_service_operation(self):
        event = _event(service="ec2", operation="RunInstances", retry_after=5)
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_rate_limit(event)
            mock_log.warning.assert_called_once()
            message = mock_log.warning.call_args[0][0]
            assert "ec2.RunInstances" in message

    def test_retry_after_included_in_message(self):
        event = _event(service="ec2", operation="DescribeFleets", retry_after=30)
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_rate_limit(event)
            message = mock_log.warning.call_args[0][0]
            assert "30s" in message

    def test_request_id_included_when_present(self):
        event = _event(
            service="ec2",
            operation="CreateFleet",
            retry_after=10,
            request_id="rq-123",
        )
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_rate_limit(event)
            message = mock_log.warning.call_args[0][0]
            assert "rq-123" in message

    def test_request_id_absent_when_not_in_event(self):
        event = _event(service="ec2", operation="CreateFleet", retry_after=10)
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_rate_limit(event)
            message = mock_log.warning.call_args[0][0]
            assert "RequestId" not in message


# ---------------------------------------------------------------------------
# handle_aws_credentials_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleAWSCredentialsEvent:
    def test_logs_info_with_event_type(self):
        # _get_field checks attribute first, then metadata;
        # DomainEvent.event_type is a real field set to the class name by
        # model_post_init — so we must put it in the metadata to test our
        # specific value going through the metadata fallback.
        # Bypass the attribute lookup by using a mock whose getattr returns None
        # for event_type so the metadata path is exercised.
        mock_event = MagicMock(spec=DomainEvent)
        mock_event.event_type = None  # attribute returns None → falls back to metadata
        mock_event.profile = None
        mock_event.region = None
        mock_event.metadata = {"event_type": "credentials_refreshed"}
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_credentials_event(mock_event)
            mock_log.info.assert_called_once()
            message = mock_log.info.call_args[0][0]
            assert "credentials_refreshed" in message

    def test_profile_included_when_present(self):
        event = _event(event_type="login", profile="my-profile")
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_credentials_event(event)
            message = mock_log.info.call_args[0][0]
            assert "my-profile" in message

    def test_region_included_when_present(self):
        event = _event(event_type="login", region="eu-west-1")
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_credentials_event(event)
            message = mock_log.info.call_args[0][0]
            assert "eu-west-1" in message

    def test_profile_absent_when_not_provided(self):
        event = _event(event_type="logout")
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_credentials_event(event)
            message = mock_log.info.call_args[0][0]
            assert "Profile" not in message

    def test_unknown_event_type_uses_default(self):
        # All attributes None, no metadata → "unknown" default
        mock_event = MagicMock(spec=DomainEvent)
        mock_event.event_type = None
        mock_event.profile = None
        mock_event.region = None
        mock_event.metadata = {}
        with patch("orb.providers.aws.application.events.aws_handlers._logger") as mock_log:
            handle_aws_credentials_event(mock_event)
            mock_log.info.assert_called_once()
            message = mock_log.info.call_args[0][0]
            assert "unknown" in message


# ---------------------------------------------------------------------------
# AWS_EVENT_HANDLERS registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSEventHandlersRegistry:
    def test_registry_contains_expected_keys(self):
        assert "AWSClientOperationEvent" in AWS_EVENT_HANDLERS
        assert "AWSRateLimitEvent" in AWS_EVENT_HANDLERS
        assert "AWSCredentialsEvent" in AWS_EVENT_HANDLERS

    def test_registry_maps_to_correct_functions(self):
        assert AWS_EVENT_HANDLERS["AWSClientOperationEvent"] is handle_aws_client_operation
        assert AWS_EVENT_HANDLERS["AWSRateLimitEvent"] is handle_aws_rate_limit
        assert AWS_EVENT_HANDLERS["AWSCredentialsEvent"] is handle_aws_credentials_event
