"""Unit tests for provider domain events."""

import pytest
from pydantic import ValidationError

from orb.domain.base.events.provider_events import (
    ProviderConfigurationEvent,
    ProviderCredentialsEvent,
    ProviderHealthChangedEvent,
    ProviderHealthCheckEvent,
    ProviderOperationEvent,
    ProviderOperationExecutedEvent,
    ProviderRateLimitEvent,
    ProviderResourceStateChangedEvent,
    ProviderStrategyRegisteredEvent,
    ProviderStrategySelectedEvent,
)
from orb.domain.base.provider_interfaces import ProviderInstanceState

# All DomainEvent subclasses require aggregate_id and aggregate_type because
# DomainEvent.aggregate_id / aggregate_type are non-optional Pydantic fields.
# The model_post_init hooks set them via object.__setattr__ for the provider-
# specific events, but Pydantic still validates presence in the initial parse.
# We therefore always pass placeholder strings and verify that model_post_init
# overwrites them correctly.

_PLACEHOLDER = "placeholder"


@pytest.mark.unit
class TestProviderOperationEvent:
    def test_creates_valid_event(self):
        event = ProviderOperationEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            operation_type="create_instance",
            provider_resource_type="instance",
        )
        assert event.provider_type == "aws"
        assert event.operation_type == "create_instance"
        assert event.operation_status == "started"

    def test_aggregate_id_defaults_to_uuid_when_no_resource_id(self):
        import uuid

        event = ProviderOperationEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            operation_type="create_instance",
            provider_resource_type="instance",
        )
        # model_post_init overwrites the placeholder with a fresh uuid4 since
        # no provider_resource_id was supplied.
        assert event.aggregate_id != _PLACEHOLDER
        # The generated value must be a valid UUID string.
        parsed = uuid.UUID(event.aggregate_id)
        assert str(parsed) == event.aggregate_id

    def test_aggregate_id_uses_resource_id_when_provided(self):
        event = ProviderOperationEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            operation_type="create_instance",
            provider_resource_type="instance",
            provider_resource_id="i-abc123",
        )
        assert event.aggregate_id == "i-abc123"

    def test_aggregate_type_is_provider_resource(self):
        event = ProviderOperationEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="k8s",
            operation_type="create_pod",
            provider_resource_type="pod",
        )
        assert event.aggregate_type == "k8s_resource"

    def test_empty_operation_type_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderOperationEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                operation_type="",
                provider_resource_type="instance",
            )

    def test_empty_resource_type_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderOperationEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                operation_type="create_instance",
                provider_resource_type="",
            )


@pytest.mark.unit
class TestProviderRateLimitEvent:
    def test_creates_valid_event(self):
        event = ProviderRateLimitEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            service_name="ec2",
            operation_name="DescribeInstances",
            retry_after=30,
        )
        assert event.service_name == "ec2"
        assert event.retry_after == 30
        assert event.aggregate_id == "aws_ec2"

    def test_aggregate_type_is_provider_service(self):
        event = ProviderRateLimitEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            service_name="s3",
            operation_name="ListObjects",
        )
        assert event.aggregate_type == "aws_service"

    def test_empty_service_name_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderRateLimitEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                service_name="",
                operation_name="Op",
            )

    def test_empty_operation_name_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderRateLimitEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                service_name="ec2",
                operation_name="",
            )


@pytest.mark.unit
class TestProviderCredentialsEvent:
    def test_creates_valid_event(self):
        event = ProviderCredentialsEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            credential_type="access_key",
            operation="refresh",
            status="success",
        )
        assert event.aggregate_id == "aws_credentials"
        assert event.aggregate_type == "aws_auth"

    def test_empty_credential_type_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderCredentialsEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                credential_type="",
                operation="refresh",
                status="success",
            )

    def test_empty_operation_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderCredentialsEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                credential_type="access_key",
                operation="",
                status="success",
            )

    def test_empty_status_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderCredentialsEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                credential_type="access_key",
                operation="refresh",
                status="",
            )


@pytest.mark.unit
class TestProviderResourceStateChangedEvent:
    def test_creates_valid_event(self):
        event = ProviderResourceStateChangedEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            resource_type="instance",
            resource_id="i-abc123",
            new_state=ProviderInstanceState.RUNNING,
        )
        assert event.aggregate_id == "i-abc123"
        assert event.aggregate_type == "aws_instance"
        assert event.previous_state is None

    def test_with_previous_state(self):
        event = ProviderResourceStateChangedEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            resource_type="instance",
            resource_id="i-abc123",
            previous_state=ProviderInstanceState.PENDING,
            new_state=ProviderInstanceState.RUNNING,
        )
        assert event.previous_state == ProviderInstanceState.PENDING

    def test_empty_resource_type_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderResourceStateChangedEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                resource_type="",
                resource_id="i-abc123",
                new_state=ProviderInstanceState.RUNNING,
            )

    def test_empty_resource_id_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderResourceStateChangedEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                resource_type="instance",
                resource_id="",
                new_state=ProviderInstanceState.RUNNING,
            )


@pytest.mark.unit
class TestProviderConfigurationEvent:
    def test_creates_valid_event(self):
        event = ProviderConfigurationEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            configuration_type="region",
            new_value="eu-west-1",
        )
        assert event.aggregate_id == "aws_config"
        assert event.aggregate_type == "aws_configuration"

    def test_empty_configuration_type_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderConfigurationEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                configuration_type="",
            )


@pytest.mark.unit
class TestProviderHealthCheckEvent:
    def test_creates_valid_event(self):
        event = ProviderHealthCheckEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_type="aws",
            service_name="ec2",
            health_status="healthy",
            response_time_ms=42,
        )
        assert event.aggregate_id == "aws_ec2"
        assert event.aggregate_type == "aws_health"

    def test_empty_service_name_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderHealthCheckEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                service_name="",
                health_status="healthy",
            )

    def test_empty_health_status_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ProviderHealthCheckEvent(
                aggregate_id=_PLACEHOLDER,
                aggregate_type=_PLACEHOLDER,
                provider_type="aws",
                service_name="ec2",
                health_status="",
            )


@pytest.mark.unit
class TestProviderStrategySelectedEvent:
    def test_creates_valid_event(self):
        event = ProviderStrategySelectedEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            strategy_name="lowest_cost",
            operation_type="create_instances",
        )
        # model_post_init does NOT overwrite aggregate_id when it is already non-empty
        assert event.aggregate_id == _PLACEHOLDER
        assert event.aggregate_type == _PLACEHOLDER

    def test_aggregate_id_set_by_post_init_when_empty(self):
        # Passing empty aggregate_id / aggregate_type exercises the falsy
        # branches in model_post_init, which derive both from strategy_name.
        event = ProviderStrategySelectedEvent(
            aggregate_id="",
            aggregate_type="",
            strategy_name="lowest_cost",
            operation_type="create_instances",
        )
        assert event.aggregate_id == "strategy_lowest_cost"
        assert event.aggregate_type == "provider_strategy"


@pytest.mark.unit
class TestProviderOperationExecutedEvent:
    def test_creates_valid_event(self):
        event = ProviderOperationExecutedEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            operation_type="create_instances",
            strategy_name="ec2fleet",
            success=True,
            execution_time_ms=150.5,
        )
        assert event.success is True
        # aggregate_type keeps placeholder since model_post_init only sets when empty
        assert event.aggregate_type == _PLACEHOLDER

    def test_aggregate_id_derived_from_operation_and_execution_time(self):
        # Empty aggregate_id / aggregate_type trigger the falsy branches in
        # model_post_init, which build aggregate_id from operation_type and the
        # int-truncated execution_time_ms.
        event = ProviderOperationExecutedEvent(
            aggregate_id="",
            aggregate_type="",
            operation_type="health_check",
            strategy_name="mock",
            success=False,
            execution_time_ms=200.0,
        )
        assert event.aggregate_id == "operation_health_check_200"
        assert event.aggregate_type == "provider_operation"


@pytest.mark.unit
class TestProviderHealthChangedEvent:
    def test_creates_valid_event(self):
        event = ProviderHealthChangedEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_name="aws-us-east-1",
            new_status="healthy",
        )
        # model_post_init only sets when empty; placeholder is truthy so kept
        assert event.aggregate_id == _PLACEHOLDER

    def test_aggregate_id_derived_from_provider_name_via_post_init(self):
        # Empty aggregate_id / aggregate_type exercise the falsy branches in
        # model_post_init, which derive both from provider_name.
        event = ProviderHealthChangedEvent(
            aggregate_id="",
            aggregate_type="",
            provider_name="aws-us-east-1",
            new_status="healthy",
        )
        assert event.aggregate_id == "health_aws-us-east-1"
        assert event.aggregate_type == "provider_health"

    def test_with_old_status(self):
        event = ProviderHealthChangedEvent(
            aggregate_id=_PLACEHOLDER,
            aggregate_type=_PLACEHOLDER,
            provider_name="aws-us-east-1",
            old_status="degraded",
            new_status="healthy",
        )
        assert event.old_status == "degraded"


@pytest.mark.unit
class TestProviderStrategyRegisteredEvent:
    def test_creates_valid_event(self):
        # Empty aggregate_id / aggregate_type exercise the falsy branches in
        # model_post_init, which derive both from the registration.
        event = ProviderStrategyRegisteredEvent(
            aggregate_id="",
            aggregate_type="",
            strategy_name="ec2fleet",
            provider_type="aws",
            priority=5,
        )
        assert event.aggregate_id == "registration_ec2fleet"
        assert event.aggregate_type == "provider_registration"
        assert event.priority == 5
