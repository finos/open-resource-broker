"""TDD tests asserting AWS concerns are moved out of monitoring/health.py."""

import inspect
from pathlib import Path


def _read_source(rel_path: str) -> str:
    root = Path(__file__).parent.parent.parent
    return (root / rel_path).read_text()


def test_no_botocore_import_in_monitoring_health():
    source = _read_source("src/orb/monitoring/health.py")
    assert "botocore" not in source, (
        "monitoring/health.py must not import botocore — AWS checks belong in providers/aws/health.py"
    )


def test_no_awsclient_import_in_monitoring_health():
    source = _read_source("src/orb/monitoring/health.py")
    assert "AWSClient" not in source, (
        "monitoring/health.py must not import AWSClient — AWS checks belong in providers/aws/health.py"
    )


def test_no_aws_client_param_in_healthcheck_init():
    from orb.monitoring.health import HealthCheck

    sig = inspect.signature(HealthCheck.__init__)
    assert "aws_client" not in sig.parameters, (
        "HealthCheck.__init__ must not have an aws_client parameter"
    )


def test_register_aws_health_checks_callable():
    from orb.providers.aws.health import register_aws_health_checks

    assert callable(register_aws_health_checks), (
        "register_aws_health_checks must be a callable in orb.providers.aws.health"
    )


# --- task 1719: register_aws_health_checks registers checks on HealthCheck ---


def test_register_aws_health_checks_registers_checks():
    """register_aws_health_checks must add aws, ec2, and dynamodb checks."""
    from unittest.mock import MagicMock

    from orb.providers.aws.health import register_aws_health_checks

    health_check = MagicMock()
    aws_client = MagicMock()

    register_aws_health_checks(health_check, aws_client)

    registered_names = {call.args[0] for call in health_check.register_check.call_args_list}
    assert "aws" in registered_names
    assert "ec2" in registered_names
    assert "dynamodb" in registered_names
    assert health_check.register_check.call_count == 3
