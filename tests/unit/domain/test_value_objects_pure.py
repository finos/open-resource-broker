"""Unit tests for pure domain value objects."""

import pytest

from orb.domain.base.value_objects_pure import (
    ARN,
    InstanceId,
    InstanceType,
    IPAddress,
    PriceType,
    ResourceId,
    ResourceQuota,
    Tags,
)


class TestResourceId:
    def test_valid_id(self):
        rid = ResourceId(value="my-resource-123")
        assert rid.value == "my-resource-123"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ResourceId(value="")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ResourceId(value="   ")

    def test_value_stripped(self):
        rid = ResourceId(value="  abc  ")
        assert rid.value == "abc"

    def test_str(self):
        rid = ResourceId(value="res-1")
        assert str(rid) == "res-1"

    def test_repr(self):
        rid = ResourceId(value="res-1")
        assert "ResourceId" in repr(rid)
        assert "res-1" in repr(rid)

    def test_immutable(self):
        rid = ResourceId(value="res-1")
        with pytest.raises(Exception):
            rid.value = "other"  # type: ignore[misc]


class TestResourceQuota:
    def test_valid_quota(self):
        q = ResourceQuota(resource_type="instances", limit=100, used=40, available=60)
        assert q.limit == 100
        assert q.used == 40
        assert q.available == 60

    def test_available_auto_corrected(self):
        # available should be corrected to limit - used
        q = ResourceQuota(resource_type="instances", limit=100, used=40, available=999)
        assert q.available == 60

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError):
            ResourceQuota(resource_type="instances", limit=-1, used=0, available=0)

    def test_negative_used_raises(self):
        with pytest.raises(ValueError):
            ResourceQuota(resource_type="instances", limit=10, used=-1, available=10)

    def test_utilization_percentage(self):
        q = ResourceQuota(resource_type="instances", limit=100, used=25, available=75)
        assert q.utilization_percentage == 25.0

    def test_utilization_zero_limit(self):
        q = ResourceQuota(resource_type="instances", limit=0, used=0, available=0)
        assert q.utilization_percentage == 0.0

    def test_is_at_limit_true(self):
        q = ResourceQuota(resource_type="instances", limit=10, used=10, available=0)
        assert q.is_at_limit is True

    def test_is_at_limit_false(self):
        q = ResourceQuota(resource_type="instances", limit=10, used=5, available=5)
        assert q.is_at_limit is False

    def test_str(self):
        q = ResourceQuota(resource_type="instances", limit=100, used=50, available=50)
        s = str(q)
        assert "instances" in s
        assert "50/100" in s
        assert "50.0%" in s


class TestIPAddress:
    def test_valid_ipv4(self):
        ip = IPAddress(value="192.168.1.1")
        assert ip.value == "192.168.1.1"

    def test_valid_ipv6(self):
        ip = IPAddress(value="::1")
        assert ip.value == "::1"

    def test_invalid_ip_raises(self):
        with pytest.raises(ValueError, match="Invalid IP address"):
            IPAddress(value="999.999.999.999")

    def test_non_ip_string_raises(self):
        with pytest.raises(ValueError):
            IPAddress(value="not-an-ip")

    def test_str(self):
        ip = IPAddress(value="10.0.0.1")
        assert str(ip) == "10.0.0.1"


class TestInstanceType:
    def test_valid(self):
        it = InstanceType(value="t3.medium")
        assert it.value == "t3.medium"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            InstanceType(value="")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            InstanceType(value="   ")

    def test_value_stripped(self):
        it = InstanceType(value="  m5.large  ")
        assert it.value == "m5.large"

    def test_str(self):
        it = InstanceType(value="c5.xlarge")
        assert str(it) == "c5.xlarge"


class TestInstanceId:
    def test_valid(self):
        iid = InstanceId(value="i-1234567890abcdef0")
        assert iid.value == "i-1234567890abcdef0"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            InstanceId(value="")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            InstanceId(value="   ")

    def test_value_stripped(self):
        iid = InstanceId(value="  i-abc  ")
        assert iid.value == "i-abc"

    def test_str(self):
        iid = InstanceId(value="i-abc123")
        assert str(iid) == "i-abc123"


class TestTags:
    def test_empty_tags(self):
        t = Tags()
        assert t.tags == {}
        assert str(t) == "{}"

    def test_tags_with_values(self):
        t = Tags(tags={"env": "prod", "team": "platform"})
        assert t.get("env") == "prod"

    def test_get_missing_key_returns_default(self):
        t = Tags(tags={"env": "prod"})
        assert t.get("missing") is None
        assert t.get("missing", "default") == "default"

    def test_add_returns_new_instance(self):
        t = Tags(tags={"env": "prod"})
        t2 = t.add("team", "platform")
        assert t2.get("team") == "platform"
        assert t.get("team") is None  # original unchanged

    def test_remove_returns_new_instance(self):
        t = Tags(tags={"env": "prod", "team": "platform"})
        t2 = t.remove("team")
        assert t2.get("team") is None
        assert t.get("team") == "platform"  # original unchanged

    def test_remove_missing_key_no_error(self):
        t = Tags(tags={"env": "prod"})
        t2 = t.remove("nonexistent")
        assert t2.tags == {"env": "prod"}

    def test_to_dict(self):
        t = Tags(tags={"a": "1", "b": "2"})
        d = t.to_dict()
        assert d == {"a": "1", "b": "2"}

    def test_from_dict(self):
        t = Tags.from_dict({"x": "1"})
        assert t.get("x") == "1"

    def test_merge(self):
        t1 = Tags(tags={"a": "1", "b": "2"})
        t2 = Tags(tags={"b": "override", "c": "3"})
        merged = t1.merge(t2)
        assert merged.get("a") == "1"
        assert merged.get("b") == "override"
        assert merged.get("c") == "3"
        # originals unchanged
        assert t1.get("b") == "2"

    def test_immutable_model(self):
        # Pydantic frozen model prevents reassigning the field itself
        t = Tags(tags={"env": "prod"})
        with pytest.raises(Exception):
            t.tags = {"new": "value"}  # type: ignore[misc]


class TestARN:
    def test_valid_arn(self):
        arn = ARN(value="arn:aws:iam::123456789012:role/my-role")
        assert str(arn) == "arn:aws:iam::123456789012:role/my-role"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            ARN(value="")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            ARN(value="   ")


class TestPriceType:
    def test_values(self):
        assert PriceType.ONDEMAND.value == "ondemand"
        assert PriceType.SPOT.value == "spot"
        assert PriceType.RESERVED.value == "reserved"
        assert PriceType.HETEROGENEOUS.value == "heterogeneous"

    def test_is_str(self):
        assert isinstance(PriceType.SPOT, str)
        assert PriceType.SPOT == "spot"
