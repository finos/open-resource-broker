"""A retry after a lost response must not create a second set of resources.

moto does not implement ``ClientToken`` idempotency, so the creating calls are
wrapped here with the behaviour AWS documents: the first call executes, a
timeout hides its response, and a later call with the same token returns the
original result instead of creating again.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import boto3
from botocore.exceptions import ReadTimeoutError

from tests.providers.aws.mocked.test_ec2fleet_price_types import (
    _ec2_fleet_template,
    _make_handler as _make_ec2_fleet_handler,
    _make_request,
)
from tests.providers.aws.mocked.test_run_instances_gaps import (
    _make_aws_client,
    _make_config_port,
    _make_launch_template_manager,
    _make_logger,
    _run_instances_template,
)
from tests.providers.aws.mocked.test_spot_fleet_gaps import (
    _base_template,
    _make_moto_aws_client,
    _make_spot_fleet_handler,
)

REGION = "eu-west-2"
SLEEP = "orb.infrastructure.resilience.retry_decorator.time.sleep"


class _LostResponseOnce:
    """Wrap a creating API call: execute it, lose the first response, honour the token."""

    def __init__(self, real, token_of) -> None:
        self._real = real
        self._token_of = token_of
        self.tokens: list[str] = []
        self.payloads: list[Any] = []
        self._by_token: dict[str, Any] = {}
        self._lost = False

    def __call__(self, **kwargs: Any) -> Any:
        token = self._token_of(kwargs)
        self.tokens.append(token)
        self.payloads.append(kwargs)
        if token in self._by_token:
            response = self._by_token[token]
        else:
            response = self._real(**kwargs)
            self._by_token[token] = response
        if not self._lost:
            self._lost = True
            raise ReadTimeoutError(endpoint_url="https://ec2.example")
        return response


class TestCreatingCallsReuseOneToken:
    def test_run_instances_launches_one_reservation_across_a_retry(
        self, moto_aws, moto_vpc_resources
    ):
        aws_client = _make_aws_client()
        logger = _make_logger()
        from orb.providers.aws.infrastructure.handlers.run_instances.handler import (
            RunInstancesHandler,
        )
        from orb.providers.aws.utilities.aws_operations import AWSOperations

        config_port = _make_config_port()
        handler = RunInstancesHandler(
            aws_client=aws_client,
            logger=logger,
            aws_ops=AWSOperations(aws_client, logger, config_port),
            launch_template_manager=_make_launch_template_manager(aws_client),
            config_port=config_port,
        )
        template = _run_instances_template(
            moto_vpc_resources["subnet_ids"][0], moto_vpc_resources["sg_id"]
        )
        request = _make_request(request_id="req-token-ri")

        fake = _LostResponseOnce(
            aws_client.ec2_client.run_instances, lambda kwargs: kwargs["ClientToken"]
        )
        with patch.object(aws_client.ec2_client, "run_instances", side_effect=fake), patch(SLEEP):
            result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert len(fake.tokens) == 2
        assert fake.tokens[0] == fake.tokens[1]
        assert 0 < len(fake.tokens[0]) <= 64
        reservations = boto3.client("ec2", region_name=REGION).describe_instances()["Reservations"]
        assert len(reservations) == 1

    def test_ec2_fleet_creates_one_fleet_across_a_retry(self, moto_vpc_resources):
        handler = _make_ec2_fleet_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            moto_vpc_resources["subnet_ids"], moto_vpc_resources["sg_id"], fleet_type="request"
        )
        request = _make_request(request_id="req-token-fleet")
        client = handler.aws_client.ec2_client

        fake = _LostResponseOnce(client.create_fleet, lambda kwargs: kwargs["ClientToken"])
        with patch.object(client, "create_fleet", side_effect=fake), patch(SLEEP):
            result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert fake.tokens[0] == fake.tokens[1]
        assert len(fake.tokens[0]) <= 64
        fleets = client.describe_fleets()["Fleets"]
        assert len(fleets) == 1

    def test_spot_fleet_token_lives_inside_the_request_config(self, moto_aws, moto_vpc_resources):
        logger = _make_logger()
        config_port = _make_config_port()
        aws_client = _make_moto_aws_client()
        handler = _make_spot_fleet_handler(aws_client, logger, config_port)
        template = _base_template(moto_vpc_resources["subnet_ids"][0], moto_vpc_resources["sg_id"])
        request = _make_request(request_id="req-token-spot")
        client = aws_client.ec2_client

        fake = _LostResponseOnce(
            client.request_spot_fleet,
            lambda kwargs: kwargs["SpotFleetRequestConfig"]["ClientToken"],
        )
        with patch.object(client, "request_spot_fleet", side_effect=fake), patch(SLEEP):
            result = handler.acquire_hosts(request, template)

        assert result["success"] is True
        assert fake.tokens[0] == fake.tokens[1]
        # Same payload object on both attempts, and no top-level ClientToken.
        assert (
            fake.payloads[0]["SpotFleetRequestConfig"] is fake.payloads[1]["SpotFleetRequestConfig"]
        )
        assert all("ClientToken" not in payload for payload in fake.payloads)
        configs = client.describe_spot_fleet_requests()["SpotFleetRequestConfigs"]
        assert len(configs) == 1

    def test_each_logical_call_gets_a_new_token(self, moto_vpc_resources):
        handler = _make_ec2_fleet_handler(moto_vpc_resources)
        template = _ec2_fleet_template(
            moto_vpc_resources["subnet_ids"], moto_vpc_resources["sg_id"], fleet_type="request"
        )
        client = handler.aws_client.ec2_client
        seen: list[str] = []
        real = client.create_fleet

        def record(**kwargs: Any) -> Any:
            seen.append(kwargs["ClientToken"])
            return real(**kwargs)

        with patch.object(client, "create_fleet", side_effect=record):
            handler.acquire_hosts(_make_request(request_id="req-a"), template)
            handler.acquire_hosts(_make_request(request_id="req-b"), template)

        assert len(seen) == 2
        assert seen[0] != seen[1]
