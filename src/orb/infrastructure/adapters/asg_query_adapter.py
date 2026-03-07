"""Infrastructure adapter implementing ASGQueryPort via AWSClient."""

from typing import Any

from orb.domain.base.ports.asg_query_port import ASGQueryPort


class ASGQueryAdapter(ASGQueryPort):
    """Queries Auto Scaling Group state via the AWS autoscaling API."""

    def __init__(self, aws_client: Any) -> None:
        self._aws_client = aws_client

    async def get_asg_details(self, asg_name: str) -> dict[str, Any]:
        """Return current details for the named ASG, or an empty dict if not found."""
        try:
            response = self._aws_client.autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )
            groups = response.get("AutoScalingGroups", [])
            return groups[0] if groups else {}
        except Exception:
            return {}
