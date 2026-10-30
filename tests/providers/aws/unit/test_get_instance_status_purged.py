"""get_instance_status for instance IDs EC2 has already purged.

EC2 stops returning a terminated instance after about an hour and answers a
describe call that names it with ``InvalidInstanceID.NotFound``. A purged ID is
absent, not an error: the remaining IDs must still be reported.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from orb.providers.aws.services.instance_operation_service import AWSInstanceOperationService
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType


class _FakeEC2:
    """EC2 stand-in: naming a purged ID raises NotFound, filters never do."""

    def __init__(self, live: set[str], names_all_missing: bool = True) -> None:
        # Every instance in the account; a describe call with no IDs returns all of them.
        self.live = live
        self.names_all_missing = names_all_missing
        self.calls: list[dict] = []

    def describe_instances(self, **kwargs):
        self.calls.append(kwargs)
        if "InstanceIds" in kwargs and not kwargs["InstanceIds"]:
            ids = sorted(self.live)
        elif "InstanceIds" in kwargs:
            missing = [i for i in kwargs["InstanceIds"] if i not in self.live]
            if missing:
                named = missing if self.names_all_missing else missing[:1]
                raise ClientError(
                    {
                        "Error": {
                            "Code": "InvalidInstanceID.NotFound",
                            "Message": f"The instance IDs '{', '.join(named)}' do not exist",
                        },
                        "ResponseMetadata": {"HTTPStatusCode": 400},
                    },
                    "DescribeInstances",
                )
            ids = kwargs["InstanceIds"]
        elif "Filters" in kwargs:
            ids = [
                i
                for f in kwargs["Filters"]
                if f["Name"] == "instance-id"
                for i in f["Values"]
                if i in self.live
            ]
        else:
            ids = sorted(self.live)
        return {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": i,
                            "InstanceType": "t3.micro",
                            "ImageId": "ami-12345678",
                            "State": {"Name": "running", "Code": 16},
                        }
                    ]
                }
                for i in ids
            ]
        }


def _service(ec2_client: object) -> AWSInstanceOperationService:
    aws_client = MagicMock()
    aws_client.ec2_client = ec2_client
    machine_adapter = MagicMock()
    machine_adapter.create_machine_from_aws_instance.side_effect = lambda aws_instance, **_kwargs: {
        "instance_id": aws_instance["InstanceId"],
        "status": aws_instance["State"]["Name"],
    }
    return AWSInstanceOperationService(
        aws_client=aws_client,
        logger=MagicMock(),
        provisioning_adapter=MagicMock(),
        machine_adapter=machine_adapter,
    )


def _operation(*instance_ids: str) -> ProviderOperation:
    return ProviderOperation(
        operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
        parameters={"instance_ids": list(instance_ids)},
    )


@pytest.fixture
def ec2_client():
    return boto3.client(
        "ec2",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106  # dummy value for a stubbed client
    )


LIVE = [f"i-0live{n:012d}" for n in range(3)]
PURGED = [f"i-0purged{n:010d}" for n in range(60)]


@pytest.mark.unit
def test_all_ids_purged_is_an_empty_success() -> None:
    ec2 = _FakeEC2(live=set())
    result = _service(ec2).get_instance_status(_operation(*PURGED[:2]))

    assert result.success is True
    assert result.data["instances"] == []
    assert len(ec2.calls) <= 3


@pytest.mark.unit
def test_all_requested_ids_purged_does_not_report_other_instances() -> None:
    # An unfiltered describe returns every instance in the account, so an empty
    # remaining list must never be sent to EC2.
    ec2 = _FakeEC2(live=set(LIVE))

    result = _service(ec2).get_instance_status(_operation(*PURGED[:3]))

    assert result.success is True
    assert result.data["instances"] == []


@pytest.mark.unit
@pytest.mark.parametrize("names_all_missing", [True, False])
@pytest.mark.parametrize("purged_count", [2, 7, 60])
def test_purged_ids_do_not_hide_the_live_ones(purged_count, names_all_missing) -> None:
    ids = [LIVE[0], *PURGED[:purged_count], *LIVE[1:]]
    ec2 = _FakeEC2(live=set(LIVE), names_all_missing=names_all_missing)

    result = _service(ec2).get_instance_status(_operation(*ids))

    assert result.success is True
    assert sorted(i["instance_id"] for i in result.data["instances"]) == sorted(LIVE)
    # Bounded: never one call per purged ID.
    assert len(ec2.calls) <= 2 + -(-len(ids) // 25)


@pytest.mark.unit
def test_unparseable_not_found_message_falls_back_to_filters() -> None:
    ec2 = _FakeEC2(live=set(LIVE))
    original = ec2.describe_instances

    def describe(**kwargs):
        try:
            return original(**kwargs)
        except ClientError as exc:
            exc.response["Error"]["Message"] = "does not exist"
            raise

    ec2.describe_instances = describe  # type: ignore[method-assign]
    result = _service(ec2).get_instance_status(_operation(*LIVE, *PURGED[:5]))

    assert sorted(i["instance_id"] for i in result.data["instances"]) == sorted(LIVE)


@pytest.mark.unit
def test_other_client_errors_still_fail_the_call(ec2_client) -> None:
    with Stubber(ec2_client) as stubber:
        stubber.add_client_error(
            "describe_instances",
            service_error_code="RequestLimitExceeded",
            http_status_code=503,
        )
        result = _service(ec2_client).get_instance_status(_operation("i-0live000000000001"))

    assert result.success is False
    assert result.error_code == "GET_INSTANCE_STATUS_ERROR"
