"""One retry layer: botocore retries are off on executor-routed clients.

The HTTP layer is replaced by a ``before-send`` hook that answers every request
with a 503, so the number of hook calls is the number of requests that would
have gone to AWS.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.awsrequest import AWSResponse

from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.exceptions.aws_exceptions import NetworkError
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler

_EC2_503 = (
    b'<?xml version="1.0"?><Response><Errors><Error><Code>ServiceUnavailable</Code>'
    b"<Message>unavailable</Message></Error></Errors><RequestID>r</RequestID></Response>"
)


class _Raw:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def stream(self, *args, **kwargs):
        yield self._body

    def read(self, *args, **kwargs):
        return self._body


def _client(monkeypatch, **config_kwargs) -> AWSClient:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    provider_config = AWSProviderConfig(region="eu-west-1", **config_kwargs)  # type: ignore[call-arg]
    with patch.object(AWSClient, "get_selected_aws_provider_config", return_value=provider_config):
        client = AWSClient(config=MagicMock(), logger=MagicMock())
    client.get_selected_aws_provider_config = lambda: provider_config  # type: ignore[method-assign]
    return client


def _count_requests(ec2) -> list[int]:
    seen: list[int] = []

    def answer(request, **kwargs):
        seen.append(1)
        return AWSResponse(request.url, 503, {}, _Raw(_EC2_503))

    ec2.meta.events.register("before-send.ec2.*", answer)
    return seen


@pytest.mark.unit
class TestRetryBudget:
    def test_persistent_503_makes_exactly_retries_plus_one_requests(self, monkeypatch):
        client = _client(monkeypatch, request_retry_attempts=5)
        seen = _count_requests(client.ec2_client)
        executor = client.get_call_executor("budget")

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(Exception):
                executor.execute(
                    client.ec2_client.describe_instances,
                    operation_type="standard",
                    preserve_client_error=True,
                )
        assert len(seen) == 6

    def test_default_budgets_reproduce_four_and_three_calls(self, monkeypatch):
        client = _client(monkeypatch)
        seen = _count_requests(client.ec2_client)
        executor = client.get_call_executor("budget")

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(Exception):
                executor.execute(
                    client.ec2_client.describe_instances,
                    operation_type="standard",
                    preserve_client_error=True,
                )
            assert len(seen) == 4
            seen.clear()
            with pytest.raises(Exception):
                executor.execute(
                    client.ec2_client.describe_instances,
                    operation_type="read_only",
                    preserve_client_error=True,
                )
            assert len(seen) == 3

    def test_describe_budget_is_configurable(self, monkeypatch):
        client = _client(monkeypatch, describe_request_retry_attempts=4)
        seen = _count_requests(client.ec2_client)
        executor = client.get_call_executor("budget")

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(Exception):
                executor.execute(
                    client.ec2_client.describe_instances,
                    operation_type="read_only",
                    preserve_client_error=True,
                )
        assert len(seen) == 5

    def test_handler_exhaustion_surfaces_the_typed_error(self, monkeypatch):
        client = _client(monkeypatch, request_retry_attempts=2)
        seen = _count_requests(client.ec2_client)

        class _Handler(AWSHandler):
            def _acquire_hosts_internal(self, request, aws_template):  # type: ignore[override]
                pass  # type: ignore[return]

            def check_hosts_status(self, request):  # type: ignore[override]
                return []

            def release_hosts(self, machine_ids, resource_mapping=None, request_id=""):  # type: ignore[override]
                pass

            def cancel_resource(self, resource_id, request_id):  # type: ignore[override]
                return {}

            @classmethod
            def get_example_templates(cls):
                return []

        handler = _Handler(
            aws_client=client,
            logger=MagicMock(),
            aws_ops=MagicMock(),
            launch_template_manager=MagicMock(),
        )
        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(NetworkError) as caught:
                handler._retry_with_backoff(client.ec2_client.describe_instances)
        assert len(seen) == 3
        assert caught.value.aws_error_code == "ServiceUnavailable"  # type: ignore[attr-defined]


@pytest.mark.unit
class TestClientConfiguration:
    def test_ec2_and_autoscaling_keep_the_adaptive_limiter_and_botocore_retries(self, monkeypatch):
        client = _client(monkeypatch, request_retry_attempts=7)
        for service_client in (client.ec2_client, client.autoscaling_client):
            retries = service_client.meta.config.retries
            assert retries["mode"] == "adaptive"
            # botocore's own single retry, unaffected by the executor's budget
            assert retries["total_max_attempts"] == 2

    def test_dynamodb_is_retried_by_botocore_in_standard_mode(self, monkeypatch):
        client = _client(monkeypatch, request_retry_attempts=0)
        for dynamodb in (client.dynamodb_client, client.dynamodb_resource.meta.client):
            retries = dynamodb.meta.config.retries
            assert retries["mode"] == "standard"
            assert retries["total_max_attempts"] == 4

    def test_other_clients_keep_one_botocore_retry_whatever_the_executor_budget(self, monkeypatch):
        client = _client(monkeypatch, request_retry_attempts=9)
        for service_client in (client.ssm_client, client.iam_client, client.elbv2_client):
            retries = service_client.meta.config.retries
            assert retries["mode"] == "adaptive"
            assert retries["total_max_attempts"] == 2


_AUTOSCALING_503 = (
    b'<?xml version="1.0"?><ErrorResponse><Error><Type>Receiver</Type><Code>ServiceUnavailable'
    b"</Code><Message>unavailable</Message></Error><RequestId>r</RequestId></ErrorResponse>"
)


def _count_autoscaling(autoscaling) -> list[int]:
    seen: list[int] = []

    def answer(request, **kwargs):
        seen.append(1)
        return AWSResponse(request.url, 503, {}, _Raw(_AUTOSCALING_503))

    autoscaling.meta.events.register("before-send.auto-scaling.*", answer)
    return seen


@pytest.mark.unit
class TestEveryCallHasOneRetryLayer:
    """Calls outside the executor keep botocore's retry; executor calls never double it."""

    def test_calls_outside_the_executor_still_get_botocore_retry(self, monkeypatch):
        client = _client(monkeypatch)
        seen = _count_requests(client.ec2_client)
        sites = {
            "describe_instances": lambda: client.ec2_client.describe_instances(
                Filters=[{"Name": "tag:orb:request-id", "Values": ["r"]}]
            ),
            "create_tags": lambda: client.ec2_client.create_tags(Resources=["i-1"], Tags=[]),
            "describe_launch_templates": lambda: client.ec2_client.describe_launch_templates(),
            "describe_images": lambda: client.ec2_client.describe_images(ImageIds=["ami-1"]),
        }
        with patch("time.sleep"):
            for name, call in sites.items():
                seen.clear()
                with pytest.raises(Exception):
                    call()
                assert len(seen) == 2, name

    def test_autoscaling_calls_outside_the_executor_still_get_botocore_retry(self, monkeypatch):
        client = _client(monkeypatch)
        seen = _count_autoscaling(client.autoscaling_client)
        with patch("time.sleep"), pytest.raises(Exception):
            client.autoscaling_client.describe_auto_scaling_groups()
        assert len(seen) == 2

    def test_executor_calls_make_one_request_per_attempt_on_both_services(self, monkeypatch):
        client = _client(monkeypatch, request_retry_attempts=2)
        ec2_seen = _count_requests(client.ec2_client)
        asg_seen = _count_autoscaling(client.autoscaling_client)
        executor = client.get_call_executor("both")
        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            for call, seen in (
                (client.ec2_client.describe_instances, ec2_seen),
                (client.autoscaling_client.describe_auto_scaling_groups, asg_seen),
            ):
                with pytest.raises(Exception):
                    executor.execute(call, preserve_client_error=True)
                assert len(seen) == 3

    def test_an_executor_call_does_not_disable_retries_of_later_raw_calls(self, monkeypatch):
        client = _client(monkeypatch, request_retry_attempts=0)
        seen = _count_requests(client.ec2_client)
        executor = client.get_call_executor("mixed")
        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"), patch("time.sleep"):
            with pytest.raises(Exception):
                executor.execute(client.ec2_client.describe_instances, preserve_client_error=True)
            assert len(seen) == 1
            seen.clear()
            with pytest.raises(Exception):
                client.ec2_client.describe_instances()
            assert len(seen) == 2

    def test_a_raw_call_inside_the_cleanup_service_is_retried_by_botocore(self, monkeypatch):
        from orb.providers.aws.infrastructure.handlers.launch_template_cleanup import (
            LaunchTemplateCleanupService,
        )

        client = _client(monkeypatch)
        seen = _count_requests(client.ec2_client)
        service = LaunchTemplateCleanupService(client, MagicMock(), MagicMock())
        with patch("time.sleep"):
            # a failed lookup keeps the template (fail closed) after botocore's retry
            assert service._has_live_instances("tag:x", "y") is True
        assert len(seen) == 2

    def test_ssm_lookup_has_no_second_retry_layer(self):
        import inspect

        from orb.providers.aws.utilities import ssm_utils

        assert "@retry" not in inspect.getsource(ssm_utils)
        assert not hasattr(ssm_utils._get_ssm_parameter, "__wrapped__")


@pytest.mark.unit
class TestMachineAdapterRoute:
    def test_execute_with_circuit_breaker_retries_and_keeps_the_client_error(self, monkeypatch):
        from botocore.exceptions import ClientError

        client = _client(monkeypatch, describe_request_retry_attempts=1)
        seen = _count_requests(client.ec2_client)

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(ClientError):
                client.execute_with_circuit_breaker(
                    "ec2",
                    "describe_instances",
                    lambda: client.ec2_client.describe_instances(),
                )
        assert len(seen) == 2

    def test_execute_with_circuit_breaker_returns_the_result(self, monkeypatch):
        client = _client(monkeypatch)
        assert client.execute_with_circuit_breaker("ec2", "noop", lambda: {"ok": True}) == {
            "ok": True
        }


@pytest.mark.unit
class TestRetryGateOnOlderBotocore:
    """The gate needs botocore 1.43.3+; older releases get a retry-off client, never stacking."""

    def test_the_supported_floor_is_a_release_that_honours_the_gate(self):
        import re
        from pathlib import Path

        from orb.providers.aws.resilience.botocore_retry_gate import MIN_BOTOCORE_VERSION

        pyproject = (Path(__file__).parents[5] / "pyproject.toml").read_text(encoding="utf-8")
        floors = {
            tuple(int(p) for p in m.split("."))
            for m in re.findall(r'"boto(?:3|core)>=([0-9.]+)"', pyproject)
        }
        assert floors and all(floor >= MIN_BOTOCORE_VERSION for floor in floors)

    @pytest.mark.parametrize(
        "version, expected",
        [
            ("1.42.21", False),
            ("1.43.2", False),
            ("1.43.3", True),
            ("1.44.0", True),
            ("2.0.0", True),
        ],
    )
    def test_the_version_check(self, monkeypatch, version, expected):
        from orb.providers.aws.resilience import botocore_retry_gate as gate

        monkeypatch.setattr(gate.botocore, "__version__", version)
        assert gate._botocore_understands_false() is expected

    @pytest.mark.parametrize("version", ["", "dev", None])
    def test_an_unreadable_version_counts_as_unsupported(self, monkeypatch, version):
        from orb.providers.aws.resilience import botocore_retry_gate as gate

        monkeypatch.setattr(gate.botocore, "__version__", version)
        assert gate._botocore_understands_false() is False

    def test_install_reports_false_and_registers_nothing_on_an_old_botocore(self, monkeypatch):
        from orb.providers.aws.resilience import botocore_retry_gate as gate

        monkeypatch.setattr(gate.botocore, "__version__", "1.42.21")
        client = MagicMock()
        assert gate.install_retry_gate(client) is False
        client.meta.events.register_first.assert_not_called()

    def test_install_reports_false_when_registration_fails(self):
        from orb.providers.aws.resilience.botocore_retry_gate import install_retry_gate

        client = MagicMock()
        client.meta.events.register_first.side_effect = RuntimeError("no events")
        assert install_retry_gate(client) is False

    def test_without_the_gate_both_services_use_a_retry_off_client_and_warn(self, monkeypatch):
        monkeypatch.setattr(
            "orb.providers.aws.infrastructure.aws_client.install_retry_gate", lambda client: False
        )
        client = _client(monkeypatch, request_retry_attempts=2)

        for service_client in (client.ec2_client, client.autoscaling_client):
            assert service_client.meta.config.retries["total_max_attempts"] == 1
        warned = " ".join(str(c) for c in client._logger.warning.call_args_list)
        assert "1.43.3" in warned and "ec2" in warned and "autoscaling" in warned

    def test_without_the_gate_an_executor_call_never_stacks_retries(self, monkeypatch):
        monkeypatch.setattr(
            "orb.providers.aws.infrastructure.aws_client.install_retry_gate", lambda client: False
        )
        client = _client(monkeypatch, request_retry_attempts=2)
        seen = _count_requests(client.ec2_client)
        executor = client.get_call_executor("old")

        with patch("orb.infrastructure.resilience.retry_decorator.time.sleep"):
            with pytest.raises(Exception):
                executor.execute(client.ec2_client.describe_instances, preserve_client_error=True)
        assert len(seen) == 3  # exactly the executor's retries plus one, nothing from botocore

    def test_with_the_gate_the_client_keeps_botocore_retries_for_other_calls(self, monkeypatch):
        client = _client(monkeypatch)
        assert client.ec2_client.meta.config.retries["total_max_attempts"] == 2
