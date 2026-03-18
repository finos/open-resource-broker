import asyncio
from unittest.mock import MagicMock

from application.services.spot_placement_planner import (
    PlacementCandidate,
    PlacementPlanEntry,
    PlacementScore,
)
from providers.aws.configuration.config import AWSProviderConfig
from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from providers.base.strategy import ProviderOperation, ProviderOperationType


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
        },
        {"success": True, "resource_ids": ["fleet-b"], "instances": []},
    ]
    strategy._handlers["EC2Fleet"] = handler

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
                "instance_types": {
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
