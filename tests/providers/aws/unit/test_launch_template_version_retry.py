"""Launch template version creation is idempotent under retry and reaches the IAM fallback."""

from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager

pytestmark = pytest.mark.unit

SLEEP = "orb.infrastructure.resilience.retry_decorator.time.sleep"
REQUEST_ID = "req-00000000-0000-0000-0000-000000000456"


def _error(code: str, operation: str = "CreateLaunchTemplateVersion") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "m"}}, operation)


def _manager() -> AWSLaunchTemplateManager:
    aws_client = Mock()
    aws_client.get_selected_aws_provider_config.return_value = None
    config_port = Mock()
    config_port.get_resource_prefix.return_value = ""
    return AWSLaunchTemplateManager(aws_client=aws_client, logger=Mock(), config_port=config_port)


def _template(**overrides) -> AWSTemplate:
    values = dict(
        template_id="tpl",
        launch_template_id="lt-user",
        machine_image="ami-11111111",
    )
    values.update(overrides)
    return AWSTemplate(**values)


def _request() -> Request:
    return Request(
        request_id=RequestId(value=REQUEST_ID),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tpl",
        requested_count=1,
    )


def _version_response() -> dict:
    return {"LaunchTemplateVersion": {"VersionNumber": 7, "LaunchTemplateName": "user-lt"}}


def test_version_call_carries_a_token_and_every_attempt_reuses_it():
    manager = _manager()
    ec2 = manager.aws_client.ec2_client
    ec2.create_launch_template_version.side_effect = [
        _error("RequestLimitExceeded"),
        _error("ServiceUnavailable"),
        _version_response(),
    ]

    with patch(SLEEP):
        result = manager._create_new_lt_version(_template(), _request())

    assert result.version == "7"
    calls = ec2.create_launch_template_version.call_args_list
    assert len(calls) == 3
    tokens = {call.kwargs["ClientToken"] for call in calls}
    assert len(tokens) == 1
    assert len(next(iter(tokens))) == 32


def test_version_token_is_distinct_from_the_create_template_token():
    manager = _manager()
    template, request = _template(), _request()
    version_token = manager._generate_version_client_token(
        request, template, {"ImageId": "ami-1"}, "n"
    )
    assert version_token != manager._generate_client_token(request, template)


def test_changed_version_data_for_the_same_request_gets_a_different_token():
    manager = _manager()
    template, request = _template(), _request()
    first = manager._generate_version_client_token(request, template, {"ImageId": "ami-1"}, "n")
    same = manager._generate_version_client_token(request, template, {"ImageId": "ami-1"}, "n")
    changed = manager._generate_version_client_token(request, template, {"ImageId": "ami-2"}, "n")
    assert first == same
    assert first != changed


def test_token_depends_on_request_and_launch_template():
    manager = _manager()
    data = {"ImageId": "ami-1"}
    base = manager._generate_version_client_token(_request(), _template(), data, "n")
    other_lt = manager._generate_version_client_token(
        _request(), _template(launch_template_id="lt-other"), data, "n"
    )
    assert base != other_lt


def test_a_later_call_for_the_same_request_never_replays_an_earlier_version():
    """A version deleted after the request ended must not be replayed by a repeat call."""
    manager = _manager()
    ec2 = manager.aws_client.ec2_client
    ec2.create_launch_template_version.return_value = _version_response()

    manager._create_new_lt_version(_template(), _request())
    manager._create_new_lt_version(_template(), _request())

    tokens = [c.kwargs["ClientToken"] for c in ec2.create_launch_template_version.call_args_list]
    assert len(tokens) == 2 and tokens[0] != tokens[1]


@pytest.mark.parametrize("mode", ["warn_on_iam_denial", "warn"])
def test_iam_denial_still_falls_back_after_one_attempt(mode):
    manager = _manager()
    manager._get_lt_update_failure_mode = lambda: mode  # type: ignore[method-assign]
    ec2 = manager.aws_client.ec2_client
    ec2.create_launch_template_version.side_effect = _error("UnauthorizedOperation")
    ec2.describe_launch_templates.return_value = {
        "LaunchTemplates": [{"LaunchTemplateName": "user-lt"}]
    }

    with patch(SLEEP) as sleep:
        result = manager._handle_existing_lt_with_overrides(_template(), _request())

    assert ec2.create_launch_template_version.call_count == 1
    assert sleep.call_count == 0
    assert result.template_id == "lt-user"
    assert result.is_new_version is False


def test_transient_failure_exhaustion_surfaces_the_original_client_error():
    manager = _manager()
    manager._get_lt_update_failure_mode = lambda: "warn_on_iam_denial"  # type: ignore[method-assign]
    ec2 = manager.aws_client.ec2_client
    original = _error("RequestLimitExceeded")
    ec2.create_launch_template_version.side_effect = original

    with patch(SLEEP):
        with pytest.raises(ClientError) as caught:
            manager._handle_existing_lt_with_overrides(_template(), _request())

    assert caught.value is original
    assert ec2.create_launch_template_version.call_count == 4


def test_launch_template_describes_retry_a_throttle():
    manager = _manager()
    ec2 = manager.aws_client.ec2_client
    ec2.describe_launch_templates.side_effect = [
        _error("Throttling", "DescribeLaunchTemplates"),
        {"LaunchTemplates": [{"LaunchTemplateName": "user-lt"}]},
    ]
    with patch(SLEEP):
        result = manager._use_existing_template_strategy(_template())
    assert result.template_name == "user-lt"
    assert ec2.describe_launch_templates.call_count == 2
