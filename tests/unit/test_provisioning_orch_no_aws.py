"""Verify ProvisioningOrchestrationService has no AWS-specific code."""

import pathlib


def _get_source():
    return pathlib.Path(
        "src/orb/application/services/provisioning_orchestration_service.py"
    ).read_text()


def test_no_aws_error_codes():
    """No hardcoded AWS error code strings."""
    source = _get_source()
    aws_codes = [
        "InsufficientInstanceCapacity",
        "SpotMaxPriceTooLow",
        "MaxSpotInstanceCountExceeded",
    ]
    for code in aws_codes:
        assert code not in source, (
            f"AWS error code '{code}' still in provisioning_orchestration_service.py"
        )


def test_no_direct_circuit_states_access():
    """No direct access to CircuitBreakerStrategy._circuit_states."""
    source = _get_source()
    assert "_circuit_states" not in source, "_circuit_states direct access still present"


def test_circuit_breaker_record_failure_is_public():
    """CircuitBreakerStrategy must have a public record_failure method."""
    from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy

    assert hasattr(CircuitBreakerStrategy, "record_failure"), (
        "record_failure not public on CircuitBreakerStrategy"
    )
    # Verify it's not the private version by checking the name in the class dict
    assert not CircuitBreakerStrategy.__dict__["record_failure"].__name__.startswith("_"), (
        "record_failure is still private"
    )
