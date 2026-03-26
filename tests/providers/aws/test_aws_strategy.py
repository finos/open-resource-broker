import asyncio
from unittest.mock import MagicMock

from orb.application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
)
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.services.spot_placement_score_adapter import (
    AWSSpotPlacementScoreAdapter,
)
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_create_instances_uses_planned_handler_path(monkeypatch):
    strategy = AWSProviderStrategy(
        config=AWSProviderConfig(region="eu-west-1", profile="default"),
        logger=MagicMock(),
    )
    strategy.initialize()

    handler = MagicMock()
    handler.acquire_hosts.side_effect = [
        {
            "success": False,
            "error_message": "AWS Error: InsufficientInstanceCapacity - no capacity available",
            "provider_data": {"error_codes": ["InsufficientInstanceCapacity"]},
        },
        {"success": True, "resource_ids": ["fleet-b"], "instances": []},
    ]
    monkeypatch.setattr(strategy, "get_handler", lambda provider_api: handler)

    monkeypatch.setattr(
        strategy,
        "_build_spot_placement_plan",
        lambda template, count: [
            PlacementPlanEntry(
                score=PlacementScore(
                    candidate=PlacementCandidate(
                        candidate_id="aws:eu-west-1:m7i.large",
                        instance_type="m7i.large",
                        region="eu-west-1",
                    ),
                    raw_score=9,
                    normalized_score=0.9,
                    approximate=True,
                ),
                planned_count=2,
            ),
            PlacementPlanEntry(
                score=PlacementScore(
                    candidate=PlacementCandidate(
                        candidate_id="aws:eu-west-1:m7i.xlarge",
                        instance_type="m7i.xlarge",
                        region="eu-west-1",
                    ),
                    raw_score=7,
                    normalized_score=0.7,
                    approximate=True,
                ),
                planned_count=1,
            ),
        ],
    )

    op = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={
            "count": 2,
            "template_config": {
                "template_id": "tmpl-aws",
                "provider_api": "EC2Fleet",
                "image_id": "ami-12345678",
                "subnet_ids": ["subnet-12345678"],
                "security_group_ids": ["sg-12345678"],
                "price_type": "spot",
                "allocation_strategy": "spotPlacementScore",
                "machine_types": {
                    "m7i.large": 1,
                    "m7i.xlarge": 1,
                },
                "fleet_type": "request",
            },
        },
    )

    result = _run(strategy.execute_operation(op))

    assert result.success
    assert result.data["resource_ids"] == ["fleet-b"]
    assert result.metadata["method"] == "planned_handler"
    assert result.metadata["provider_data"]["unfulfilled_count"] == 0
    assert len(result.metadata["provider_data"]["child_results"]) == 2


def test_spot_placement_score_adapter_uses_canonical_machine_types():
    ec2_client = MagicMock()
    ec2_client.get_spot_placement_scores.return_value = {
        "SpotPlacementScores": [{"Region": "eu-west-1", "Score": 8}]
    }
    aws_client = MagicMock()
    aws_client.ec2_client = ec2_client

    template = AWSTemplate.model_validate(
        {
            "template_id": "tmpl-aws",
            "provider_api": "EC2Fleet",
            "provider_type": "aws",
            "provider_name": "aws-default",
            "image_id": "ami-12345678",
            "subnet_ids": ["subnet-12345678"],
            "security_group_ids": ["sg-12345678"],
            "price_type": "spot",
            "allocation_strategy": "spotPlacementScore",
            "machine_types": {
                "m7i.large": 1,
                "m7i.xlarge": 1,
            },
            "fleet_type": "request",
        }
    )

    adapter = AWSSpotPlacementScoreAdapter(
        aws_client=aws_client,
        logger=MagicMock(),
        region="eu-west-1",
    )

    scores = adapter.score_candidates(requested_count=2, template=template)

    assert [score.candidate.instance_type for score in scores] == ["m7i.large", "m7i.xlarge"]
    assert ec2_client.get_spot_placement_scores.call_count == 2
