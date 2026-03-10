"""AWS health checks — registered with the application HealthCheck instance."""

from typing import TYPE_CHECKING

from botocore.config import Config
from botocore.exceptions import ClientError

from orb.monitoring.health import HealthCheck, HealthStatus

if TYPE_CHECKING:
    from orb.providers.aws.infrastructure.aws_client import AWSClient


def register_aws_health_checks(health_check: HealthCheck, aws_client: "AWSClient") -> None:
    """Register AWS-specific health checks with the given HealthCheck instance.

    Args:
        health_check: The application HealthCheck to register checks on.
        aws_client: Authenticated AWS client used by the checks.
    """

    def _check_aws_health() -> HealthStatus:
        try:
            response = aws_client.sts_client.get_caller_identity()
            return HealthStatus(
                name="aws",
                status="healthy",
                details={
                    "account_id": response["Account"],
                    "user_id": response["UserId"],
                    "arn": response["Arn"],
                },
                dependencies=["aws"],
            )
        except Exception as e:
            return HealthStatus(
                name="aws",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["aws"],
            )

    def _check_ec2_health() -> HealthStatus:
        try:
            response = aws_client.ec2_client.describe_instances(MaxResults=5)
            instance_count = sum(len(r["Instances"]) for r in response.get("Reservations", []))
            return HealthStatus(
                name="ec2",
                status="healthy",
                details={"instance_count": instance_count, "api_status": "available"},
                dependencies=["aws", "ec2"],
            )
        except ClientError as e:
            return HealthStatus(
                name="ec2",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["aws", "ec2"],
            )

    def _check_dynamodb_health() -> HealthStatus:
        try:
            repo_config = health_check.config["REPOSITORY_CONFIG"]["dynamodb"]
            table_prefix = repo_config["table_prefix"]
            tables = aws_client.session.client(
                "dynamodb",
                config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3}),
            ).list_tables()
            project_tables = [t for t in tables["TableNames"] if t.startswith(table_prefix)]
            return HealthStatus(
                name="database",
                status="healthy",
                details={
                    "type": "dynamodb",
                    "table_count": len(project_tables),
                    "tables": project_tables,
                },
                dependencies=["database", "dynamodb"],
            )
        except Exception as e:
            return HealthStatus(
                name="database",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["database", "dynamodb"],
            )

    health_check.register_check("aws", _check_aws_health)
    health_check.register_check("ec2", _check_ec2_health)
    health_check.register_check("dynamodb", _check_dynamodb_health)
