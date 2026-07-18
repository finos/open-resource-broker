"""Unit tests for orb.providers.aws.infrastructure.utils.

The paginate helper, list_all_instances, list_all_subnets, and
list_all_security_groups are tested with mocked boto3 clients.
No real AWS connections are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from orb.providers.aws.infrastructure.utils import (
    list_all_instances,
    list_all_security_groups,
    list_all_subnets,
    paginate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paginator(pages: list[dict]) -> MagicMock:
    """Return a mock paginator that yields the given pages."""
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    return paginator


def _make_client_method(paginator: MagicMock, method_name: str = "describe_instances"):
    """Create a bound-method-like mock that responds to __self__ / __name__."""
    method = MagicMock()
    method.__self__ = MagicMock()
    method.__self__.get_paginator.return_value = paginator
    method.__name__ = method_name
    return method


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPaginate:
    def test_collects_results_across_pages(self):
        pages = [
            {"Reservations": [{"id": 1}, {"id": 2}]},
            {"Reservations": [{"id": 3}]},
        ]
        paginator = _make_paginator(pages)
        method = _make_client_method(paginator)
        result = paginate(method, "Reservations")
        assert result == [{"id": 1}, {"id": 2}, {"id": 3}]

    def test_returns_empty_list_when_no_results(self):
        paginator = _make_paginator([{"Reservations": []}])
        method = _make_client_method(paginator)
        result = paginate(method, "Reservations")
        assert result == []

    def test_raises_runtime_error_on_client_error(self):
        paginator = MagicMock()
        err = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "describe")
        paginator.paginate.return_value.__iter__ = MagicMock(side_effect=err)
        method = _make_client_method(paginator)
        with pytest.raises(RuntimeError, match="AccessDenied"):
            paginate(method, "Reservations")

    def test_passes_kwargs_to_paginator(self):
        paginator = _make_paginator([{"Subnets": [{"id": "s-1"}]}])
        method = _make_client_method(paginator, "describe_subnets")
        paginate(method, "Subnets", Filters=[{"Name": "vpc-id", "Values": ["vpc-1"]}])
        paginator.paginate.assert_called_once_with(
            Filters=[{"Name": "vpc-id", "Values": ["vpc-1"]}]
        )

    def test_missing_key_returns_empty_list_for_page(self):
        paginator = _make_paginator([{}])  # No "Reservations" key in page
        method = _make_client_method(paginator)
        result = paginate(method, "Reservations")
        assert result == []


# ---------------------------------------------------------------------------
# list_all_instances
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListAllInstances:
    def test_flattens_instances_from_reservations(self):
        reservation_pages = [
            {
                "Reservations": [
                    {"Instances": [{"InstanceId": "i-aaa"}, {"InstanceId": "i-bbb"}]},
                    {"Instances": [{"InstanceId": "i-ccc"}]},
                ]
            }
        ]
        ec2 = MagicMock()
        paginator = _make_paginator(reservation_pages)
        ec2.describe_instances.__self__ = ec2
        ec2.describe_instances.__name__ = "describe_instances"
        ec2.get_paginator.return_value = paginator

        result = list_all_instances(ec2)
        instance_ids = [i["InstanceId"] for i in result]
        assert set(instance_ids) == {"i-aaa", "i-bbb", "i-ccc"}

    def test_passes_filters_to_paginate(self):
        ec2 = MagicMock()
        paginator = _make_paginator([{"Reservations": []}])
        ec2.describe_instances.__self__ = ec2
        ec2.describe_instances.__name__ = "describe_instances"
        ec2.get_paginator.return_value = paginator

        list_all_instances(ec2, filters=[{"Name": "state", "Values": ["running"]}])
        paginator.paginate.assert_called_once_with(
            Filters=[{"Name": "state", "Values": ["running"]}]
        )

    def test_empty_reservations_returns_empty_list(self):
        ec2 = MagicMock()
        paginator = _make_paginator([{"Reservations": []}])
        ec2.describe_instances.__self__ = ec2
        ec2.describe_instances.__name__ = "describe_instances"
        ec2.get_paginator.return_value = paginator

        result = list_all_instances(ec2)
        assert result == []


# ---------------------------------------------------------------------------
# list_all_subnets
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListAllSubnets:
    def test_returns_subnet_list(self):
        ec2 = MagicMock()
        paginator = _make_paginator([{"Subnets": [{"SubnetId": "subnet-1"}]}])
        ec2.describe_subnets.__self__ = ec2
        ec2.describe_subnets.__name__ = "describe_subnets"
        ec2.get_paginator.return_value = paginator

        result = list_all_subnets(ec2)
        assert result == [{"SubnetId": "subnet-1"}]

    def test_passes_none_filters_as_empty_list(self):
        ec2 = MagicMock()
        paginator = _make_paginator([{"Subnets": []}])
        ec2.describe_subnets.__self__ = ec2
        ec2.describe_subnets.__name__ = "describe_subnets"
        ec2.get_paginator.return_value = paginator

        list_all_subnets(ec2, filters=None)
        paginator.paginate.assert_called_once_with(Filters=[])


# ---------------------------------------------------------------------------
# list_all_security_groups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListAllSecurityGroups:
    def test_returns_security_group_list(self):
        ec2 = MagicMock()
        paginator = _make_paginator([{"SecurityGroups": [{"GroupId": "sg-abc"}]}])
        ec2.describe_security_groups.__self__ = ec2
        ec2.describe_security_groups.__name__ = "describe_security_groups"
        ec2.get_paginator.return_value = paginator

        result = list_all_security_groups(ec2)
        assert result == [{"GroupId": "sg-abc"}]

    def test_passes_filters(self):
        ec2 = MagicMock()
        paginator = _make_paginator([{"SecurityGroups": []}])
        ec2.describe_security_groups.__self__ = ec2
        ec2.describe_security_groups.__name__ = "describe_security_groups"
        ec2.get_paginator.return_value = paginator

        list_all_security_groups(ec2, filters=[{"Name": "group-name", "Values": ["web"]}])
        paginator.paginate.assert_called_once_with(
            Filters=[{"Name": "group-name", "Values": ["web"]}]
        )
