"""AWS health checks — registered with the application HealthCheck instance."""

import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from botocore.exceptions import BotoCoreError, ClientError

from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.infrastructure.logging.logger import get_logger
from orb.monitoring.health import HealthStatus, failure_details

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from orb.providers.aws.infrastructure.aws_client import AWSClient


def lazy_aws_client_getter(
    get_client: Callable[[], Optional["AWSClient"]], retry_after_seconds: float = 60.0
) -> Callable[[], Optional["AWSClient"]]:
    """Wrap ``get_client`` so health checks build the client once, on first use.

    Calls are serialised so concurrent checks build one client, and after a
    failed build the wrapper returns ``None`` without retrying for
    ``retry_after_seconds`` so every health poll does not repeat the failure.
    """
    lock = threading.Lock()
    failed_at: list[float] = []

    def _get() -> Optional["AWSClient"]:
        with lock:
            if failed_at and time.monotonic() - failed_at[0] < retry_after_seconds:
                return None
            client = get_client()
            failed_at.clear()
            if client is None:
                failed_at.append(time.monotonic())
            return client

    return _get


def register_aws_health_checks(
    health_check: HealthCheckPort,
    get_aws_client: Callable[[], Optional["AWSClient"]],
    storage_strategy: str = "json",  # kept for back-compat with callers
    *,
    kind: str = "provider",
) -> None:
    """Register AWS-specific health checks (``aws``, ``ec2``).

    The ``database`` check is registered provider-agnostically by
    ``monitoring/health.py::register_storage_health_checks`` from the
    monitoring bootstrap, so it no longer needs to be wired here for
    DynamoDB. The ``storage_strategy`` parameter is retained for caller
    compatibility but is intentionally unused.

    Args:
        health_check: The application HealthCheckPort to register checks on.
        get_aws_client: Returns the authenticated AWS client used by the checks,
            or ``None`` when it cannot be built.  It is called each time a check
            runs, so the client is only created once a check first executes.
        storage_strategy: Ignored; retained for back-compat.
        kind: Readiness classification for the connectivity checks
            (``"core"`` when AWS is the sole enabled provider so a broken
            provider gates ``/readyz``; ``"provider"`` otherwise). See
            ``providers.health_scoping.connectivity_check_kind``.
    """
    _ = storage_strategy

    # A client that cannot be built means connectivity cannot be assessed.
    # "unknown" is not a failing status, so it cannot make readiness report
    # not-ready the way "degraded" would for a core-kind check.
    def _unavailable(name: str, dependencies: list[str], error: object) -> HealthStatus:
        return HealthStatus(
            name=name,
            status="unknown",
            details=failure_details(name, error),
            dependencies=dependencies,
        )

    def _resolve_client() -> tuple[Optional["AWSClient"], str]:
        try:
            client = get_aws_client()
        except Exception as e:
            return None, str(e)
        return client, "AWS client could not be created"

    def _check_aws_health() -> HealthStatus:
        aws_client, error = _resolve_client()
        if aws_client is None:
            return _unavailable("aws", ["aws"], error)
        try:
            response = aws_client.sts_client.get_caller_identity()
            # Identity is useful to operators but must not be served by the
            # unauthenticated /health endpoint, so it is logged instead, at
            # DEBUG: liveness probes run this every few seconds.
            _logger.debug(
                "AWS identity verified: account %s, arn %s",
                response.get("Account"),
                response.get("Arn"),
            )
            return HealthStatus(
                name="aws",
                status="healthy",
                details={"api_status": "available"},
                dependencies=["aws"],
            )
        except Exception as e:
            # An unreachable provider API is a degraded signal, not a hard
            # failure of the process. /health maps degraded -> 200, so a
            # single unreachable provider no longer forces the endpoint to 503.
            return HealthStatus(
                name="aws",
                status="degraded",
                details=failure_details("aws", e),
                dependencies=["aws"],
            )

    def _check_ec2_health() -> HealthStatus:
        aws_client, error = _resolve_client()
        if aws_client is None:
            return _unavailable("ec2", ["aws", "ec2"], error)
        try:
            response = aws_client.ec2_client.describe_instances(MaxResults=5)
            instance_count = sum(len(r["Instances"]) for r in response.get("Reservations", []))
            return HealthStatus(
                name="ec2",
                status="healthy",
                details={"instance_count": instance_count, "api_status": "available"},
                dependencies=["aws", "ec2"],
            )
        except (ClientError, BotoCoreError) as e:
            # Both API errors (ClientError, e.g. AuthFailure) and connection
            # failures (BotoCoreError subclasses such as EndpointConnectionError
            # / ConnectTimeoutError raised by an unreachable endpoint) are
            # connectivity/permission signals — degraded, not a hard failure.
            # /health maps degraded -> 200, so an unreachable EC2 endpoint no
            # longer forces the endpoint to 503.
            return HealthStatus(
                name="ec2",
                status="degraded",
                details=failure_details("ec2", e),
                dependencies=["aws", "ec2"],
            )

    health_check.register_check("aws", _check_aws_health, kind=kind)
    health_check.register_check("ec2", _check_ec2_health, kind=kind)
