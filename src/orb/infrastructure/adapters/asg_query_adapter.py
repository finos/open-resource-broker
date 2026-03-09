"""Infrastructure adapter implementing ASGQueryPort via AWSClient."""

import logging
from typing import Any

from botocore.exceptions import ClientError

from orb.domain.base.ports.asg_query_port import ASGQueryPort


class ASGQueryAdapter(ASGQueryPort):
    """Queries Auto Scaling Group state via the AWS autoscaling API."""

    def __init__(self, aws_client: Any) -> None:
        self._aws_client = aws_client
        self._logger = logging.getLogger(__name__)

    async def get_asg_details(self, asg_name: str) -> dict[str, Any]:
        """Return current details for the named ASG, or an empty dict if not found."""
        try:
            response = self._aws_client.autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )
            groups = response.get("AutoScalingGroups", [])
            return groups[0] if groups else {}
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ValidationError",):
                return {}
            self._logger.warning("AWS API error querying ASG %s: %s", asg_name, e)
            raise
        except Exception as e:
            self._logger.error("Unexpected error querying ASG %s: %s", asg_name, e)
            raise
