"""Static example templates for the Lambda MicroVM handler."""

from __future__ import annotations

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate


def build_microvm_example_templates() -> list[AWSTemplate]:
    """Build the list of example MicroVM templates."""
    return [
        AWSTemplate(
            template_id="MicroVM-Worker",
            name="Lambda MicroVM Worker",
            description="Pull-based MicroVM worker for batch processing (polls SQS/Kafka for tasks)",
            provider_api="MicroVM",
            image_id="arn:aws:lambda:us-east-1:123456789012:microvm-image:my-worker",
            machine_types={},
            max_instances=100,
            subnet_ids=[],
            security_group_ids=[],
            tags={"Environment": "prod"},
            metadata={
                "execution_role_arn": "arn:aws:iam::123456789012:role/MicroVMRole",
                "idle_policy": {
                    "maxIdleDurationSeconds": 3600,
                    "suspendedDurationSeconds": 3600,
                    "autoResumeEnabled": True,
                },
                "maximum_duration_in_seconds": 3600,
                "logging": {
                    "cloudWatch": {
                        "logGroup": "/aws/lambda-microvms/my-worker",
                    }
                },
            },
        ),
    ]


MICROVM_EXAMPLE_TEMPLATES: list[AWSTemplate] = build_microvm_example_templates()
