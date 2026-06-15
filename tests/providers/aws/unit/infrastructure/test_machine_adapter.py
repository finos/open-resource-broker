"""Unit tests for AWSMachineAdapter — snake_case input path."""

from unittest.mock import MagicMock

from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter


def _make_adapter(region: str = "us-east-1") -> AWSMachineAdapter:
    aws_client = MagicMock()
    aws_client.region_name = region
    return AWSMachineAdapter(aws_client=aws_client, logger=MagicMock())


_SNAKE_BASE = {
    "instance_id": "i-0abc123",
    "instance_type": "t3.medium",
    "private_ip": "10.0.0.1",
    "status": "running",
    "image_id": "ami-0abc123",
    "subnet_id": "subnet-111",
    "vpc_id": "vpc-111",
    "placement": {"availability_zone": "us-east-1a"},
    "security_groups": [],
    "launch_time": "2026-01-01T00:00:00+00:00",
}


def _call(adapter, data, **kwargs):
    return adapter.create_machine_from_aws_instance(
        aws_instance_data=data,
        request_id=kwargs.get("request_id", "req-001"),
        provider_api=kwargs.get("provider_api", "EC2Fleet"),
        resource_id=kwargs.get("resource_id", "fleet-001"),
    )


class TestSnakeCasePrivateDnsName:
    def test_snake_case_key_is_resolved(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "private_dns_name": "ip-10-0-0-1.ec2.internal"}
        result = _call(adapter, data)
        assert result["private_dns_name"] == "ip-10-0-0-1.ec2.internal"

    def test_snake_takes_priority_over_pascal(self):
        adapter = _make_adapter()
        data = {
            **_SNAKE_BASE,
            "private_dns_name": "snake.internal",
            "PrivateDnsName": "pascal.internal",
        }
        result = _call(adapter, data)
        assert result["private_dns_name"] == "snake.internal"

    def test_falls_back_to_pascal_when_snake_absent(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "PrivateDnsName": "pascal.internal"}
        result = _call(adapter, data)
        assert result["private_dns_name"] == "pascal.internal"

    def test_none_when_both_absent(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_SNAKE_BASE))
        assert result["private_dns_name"] is None


class TestSnakeCasePublicDnsName:
    def test_snake_case_key_is_resolved(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "public_dns_name": "ec2-1-2-3-4.compute-1.amazonaws.com"}
        result = _call(adapter, data)
        assert result["public_dns_name"] == "ec2-1-2-3-4.compute-1.amazonaws.com"

    def test_falls_back_to_pascal_when_snake_absent(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "PublicDnsName": "ec2-pascal.compute-1.amazonaws.com"}
        result = _call(adapter, data)
        assert result["public_dns_name"] == "ec2-pascal.compute-1.amazonaws.com"

    def test_none_when_both_absent(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_SNAKE_BASE))
        assert result["public_dns_name"] is None


class TestSnakeCaseInstanceId:
    def test_instance_id_preserved(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_SNAKE_BASE))
        assert result["instance_id"] == "i-0abc123"

    def test_request_id_injected(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_SNAKE_BASE), request_id="req-xyz")
        assert result["request_id"] == "req-xyz"


class TestSnakeCaseTags:
    def test_name_tag_list_format(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "tags": [{"Key": "Name", "Value": "my-machine"}]}
        result = _call(adapter, data)
        assert result["name"] == "my-machine"

    def test_name_tag_dict_format(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "tags": {"Name": "dict-machine"}}
        result = _call(adapter, data)
        assert result["name"] == "dict-machine"

    def test_pascal_tags_fallback(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "Tags": [{"Key": "Name", "Value": "pascal-machine"}]}
        result = _call(adapter, data)
        assert result["name"] == "pascal-machine"

    def test_no_tags_falls_back_to_private_dns(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "private_dns_name": "ip-10-0-0-1.ec2.internal"}
        result = _call(adapter, data)
        assert result["name"] == "ip-10-0-0-1.ec2.internal"

    def test_no_tags_no_dns_falls_back_to_instance_id(self):
        adapter = _make_adapter()
        data = {k: v for k, v in _SNAKE_BASE.items() if k != "private_ip"}
        result = _call(adapter, data)
        assert result["name"] == "i-0abc123"


class TestResolveMachineName:
    def test_snake_private_dns(self):
        adapter = _make_adapter()
        data = {"instance_id": "i-001", "private_dns_name": "ip-10.ec2.internal"}
        assert adapter._resolve_machine_name(data) == "ip-10.ec2.internal"

    def test_pascal_private_dns_fallback(self):
        adapter = _make_adapter()
        data = {"instance_id": "i-001", "PrivateDnsName": "ip-10.ec2.internal"}
        assert adapter._resolve_machine_name(data) == "ip-10.ec2.internal"

    def test_snake_instance_id_fallback(self):
        adapter = _make_adapter()
        data = {"instance_id": "i-fallback"}
        assert adapter._resolve_machine_name(data) == "i-fallback"

    def test_pascal_instance_id_fallback(self):
        adapter = _make_adapter()
        data = {"InstanceId": "i-pascal-fallback"}
        assert adapter._resolve_machine_name(data) == "i-pascal-fallback"

    def test_snake_private_ip_fallback(self):
        adapter = _make_adapter()
        data = {"instance_id": "i-001", "private_ip": "10.0.0.5"}
        assert adapter._resolve_machine_name(data) == "10.0.0.5"


class TestCloudHostId:
    def test_snake_case_sets_cloud_host_id(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_SNAKE_BASE))
        assert result["provider_data"]["cloud_host_id"] == "i-0abc123"

    def test_pascal_case_sets_cloud_host_id(self):
        adapter = _make_adapter()
        data = {
            "InstanceId": "i-pascal",
            "InstanceType": "t3.medium",
            "State": {"Name": "running"},
            "Placement": {"AvailabilityZone": "us-east-1a"},
            "SubnetId": "subnet-111",
            "VpcId": "vpc-111",
            "ImageId": "ami-0abc123",
            "PrivateIpAddress": "10.0.0.1",
            "SecurityGroups": [],
        }
        result = _call(adapter, data)
        assert result["provider_data"]["cloud_host_id"] == "i-pascal"


class TestTerminalStatePascalCase:
    """Terminal-state PascalCase instances must surface tags at the top level."""

    _TERMINAL_BASE = {
        "InstanceId": "i-term001",
        "InstanceType": "t3.medium",
        "State": {"Name": "terminated"},
        "ImageId": "ami-0abc123",
    }

    def test_terminal_state_instance_produces_top_level_tags(self):
        """Terminated instances must put tags at the top level, not in metadata."""
        adapter = _make_adapter()
        data = {
            **self._TERMINAL_BASE,
            "Tags": [
                {"Key": "Name", "Value": "my-terminated-machine"},
                {"Key": "env", "Value": "prod"},
            ],
        }
        result = _call(adapter, data)

        assert result["tags"] == {"Name": "my-terminated-machine", "env": "prod"}
        assert "tags" not in result.get("metadata", {})

    def test_terminal_state_empty_tags_produces_empty_dict(self):
        """Terminated instance with no Tags key yields an empty tags dict."""
        adapter = _make_adapter()
        result = _call(adapter, dict(self._TERMINAL_BASE))

        assert result["tags"] == {}
        assert "tags" not in result.get("metadata", {})

    def test_terminal_state_metadata_is_empty(self):
        """Terminal-state path returns an empty metadata dict (no placement data)."""
        adapter = _make_adapter()
        result = _call(adapter, dict(self._TERMINAL_BASE))

        assert result.get("metadata") == {}

    def test_stopping_state_also_produces_top_level_tags(self):
        """The 'stopping' state follows the same terminal-state code path."""
        adapter = _make_adapter()
        data = {
            "InstanceId": "i-stop001",
            "InstanceType": "t3.medium",
            "State": {"Name": "stopping"},
            "ImageId": "ami-0abc123",
            "Tags": [{"Key": "team", "Value": "infra"}],
        }
        result = _call(adapter, data)

        assert result["tags"] == {"team": "infra"}
        assert "tags" not in result.get("metadata", {})


# ---------------------------------------------------------------------------
# provider_data field migration tests
# ---------------------------------------------------------------------------

_PASCAL_RUNNING = {
    "InstanceId": "i-pascal-run",
    "InstanceType": "t3.medium",
    "State": {"Name": "running"},
    "Placement": {"AvailabilityZone": "us-east-1a"},
    "SubnetId": "subnet-111",
    "VpcId": "vpc-111",
    "ImageId": "ami-0abc123",
    "PrivateIpAddress": "10.0.0.1",
    "SecurityGroups": [],
}


class TestProviderDataFieldsSnakeCase:
    """Snake-case (already-processed) path writes vcpus/AZ/region to provider_data."""

    def test_vcpus_in_provider_data(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "placement": {"availability_zone": "us-east-1b"}}
        result = _call(adapter, data)
        assert "vcpus" in result["provider_data"]
        assert "vcpus" not in result.get("metadata", {})

    def test_availability_zone_in_provider_data(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "placement": {"availability_zone": "us-east-1a"}}
        result = _call(adapter, data)
        assert result["provider_data"]["availability_zone"] == "us-east-1a"
        assert "availability_zone" not in result.get("metadata", {})

    def test_region_derived_from_az(self):
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "placement": {"availability_zone": "us-east-1a"}}
        result = _call(adapter, data)
        assert result["provider_data"]["region"] == "us-east-1"

    def test_region_falls_back_to_client_region_when_no_az(self):
        adapter = _make_adapter(region="eu-west-1")
        data = {k: v for k, v in _SNAKE_BASE.items() if k != "placement"}
        result = _call(adapter, data)
        assert result["provider_data"]["region"] == "eu-west-1"


class TestProviderDataFieldsPascalCase:
    """PascalCase (raw AWS API) running-instance path writes vcpus/AZ/region to provider_data."""

    def test_vcpus_in_provider_data(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_PASCAL_RUNNING))
        assert "vcpus" in result["provider_data"]
        assert "vcpus" not in result.get("metadata", {})

    def test_availability_zone_in_provider_data(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_PASCAL_RUNNING))
        assert result["provider_data"]["availability_zone"] == "us-east-1a"
        assert "availability_zone" not in result.get("metadata", {})

    def test_region_derived_from_az(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(_PASCAL_RUNNING))
        assert result["provider_data"]["region"] == "us-east-1"

    def test_region_falls_back_to_client_region_when_no_placement(self):
        adapter = _make_adapter(region="ap-southeast-1")
        data = {k: v for k, v in _PASCAL_RUNNING.items() if k != "Placement"}
        data["Placement"] = {}
        result = _call(adapter, data)
        assert result["provider_data"]["region"] == "ap-southeast-1"


class TestProviderDataFieldsTerminalState:
    """Terminal-state PascalCase path also writes vcpus/AZ/region to provider_data."""

    _TERMINAL = {
        "InstanceId": "i-term-pd",
        "InstanceType": "t3.medium",
        "State": {"Name": "terminated"},
        "Placement": {"AvailabilityZone": "us-west-2b"},
        "ImageId": "ami-0abc123",
    }

    def test_vcpus_in_provider_data(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(self._TERMINAL))
        assert "vcpus" in result["provider_data"]
        assert "vcpus" not in result.get("metadata", {})

    def test_availability_zone_in_provider_data(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(self._TERMINAL))
        assert result["provider_data"]["availability_zone"] == "us-west-2b"
        assert "availability_zone" not in result.get("metadata", {})

    def test_region_derived_from_az(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(self._TERMINAL))
        assert result["provider_data"]["region"] == "us-west-2"

    def test_metadata_is_empty(self):
        adapter = _make_adapter()
        result = _call(adapter, dict(self._TERMINAL))
        assert result.get("metadata") == {}


# ---------------------------------------------------------------------------
# health_checks synthesis tests
# ---------------------------------------------------------------------------


class TestHealthChecksSynthesis:
    """health_checks is synthesised from describe_instances data — no extra API call."""

    def test_health_checks_populated_for_running_instance(self):
        """Running instance with no state_reason → status ok, details None."""
        adapter = _make_adapter()
        result = _call(adapter, dict(_SNAKE_BASE))
        hc = result["provider_data"]["health_checks"]
        assert hc["status"] == "ok"
        assert hc["source"] == "describe_instances"
        assert hc["details"] is None

    def test_health_checks_impaired_when_state_reason_present(self):
        """Running instance with a state_reason → status impaired, details populated."""
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "state_reason": "Client.UserInitiatedShutdown"}
        result = _call(adapter, data)
        hc = result["provider_data"]["health_checks"]
        assert hc["status"] == "impaired"
        assert hc["source"] == "describe_instances"
        assert hc["details"]["state_reason"] == "Client.UserInitiatedShutdown"

    def test_health_checks_impaired_when_state_transition_reason_present(self):
        """Running instance with only state_transition_reason → status impaired."""
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "state_transition_reason": "User initiated (2026-01-01)"}
        result = _call(adapter, data)
        hc = result["provider_data"]["health_checks"]
        assert hc["status"] == "impaired"
        assert hc["details"]["state_transition_reason"] == "User initiated (2026-01-01)"

    def test_health_checks_omitted_for_stopped_instance(self):
        """Stopped instance → no health_checks key in provider_data."""
        adapter = _make_adapter()
        data = {**_SNAKE_BASE, "status": "stopped"}
        result = _call(adapter, data)
        assert "health_checks" not in result["provider_data"]

    def test_health_checks_omitted_for_terminated_instance(self):
        """Terminated instance → no health_checks key in provider_data."""
        adapter = _make_adapter()
        data = {
            "InstanceId": "i-term-hc",
            "InstanceType": "t3.medium",
            "State": {"Name": "terminated"},
            "ImageId": "ami-0abc123",
        }
        result = _call(adapter, data)
        assert "health_checks" not in result["provider_data"]

    def test_health_checks_pascal_running_no_reason(self):
        """PascalCase running instance with no StateReason → status ok."""
        adapter = _make_adapter()
        result = _call(adapter, dict(_PASCAL_RUNNING))
        hc = result["provider_data"]["health_checks"]
        assert hc["status"] == "ok"
        assert hc["source"] == "describe_instances"
        assert hc["details"] is None

    def test_health_checks_pascal_state_reason_dict_normalised(self):
        """PascalCase StateReason dict is normalised to its Message string."""
        adapter = _make_adapter()
        data = {
            **_PASCAL_RUNNING,
            "StateReason": {"Code": "Server.InternalError", "Message": "Host failure"},
        }
        result = _call(adapter, data)
        hc = result["provider_data"]["health_checks"]
        assert hc["status"] == "impaired"
        assert hc["details"]["state_reason"] == "Host failure"
