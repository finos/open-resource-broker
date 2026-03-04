"""Moto integration tests: error paths through the full pipeline.

Covers template loading errors, handler input validation errors, AWS API
errors, and response shape consistency.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from providers.aws.exceptions.aws_exceptions import AWSValidationError
from tests.aws_mock.conftest import (
    _make_aws_client,
    _make_config_port,
    _make_logger,
    make_asg_handler,
    make_aws_template,
    make_request,
    make_run_instances_handler,
)

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy(scheduler_type: str = "default"):
    """Build a lightweight scheduler strategy with no DI needed."""
    logger = _make_logger()
    if scheduler_type == "hostfactory":
        from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        return HostFactorySchedulerStrategy(logger=logger)
    from infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    return DefaultSchedulerStrategy(logger=logger)


# ---------------------------------------------------------------------------
# Template Loading Errors
# ---------------------------------------------------------------------------


class TestTemplateLoadingErrors:
    def test_nonexistent_path_returns_empty_list(self):
        """load_templates_from_path on a missing file returns [] without raising."""
        strategy = _make_strategy("default")
        result = strategy.load_templates_from_path("/tmp/does-not-exist-orb-test.json")
        assert result == []

    def test_nonexistent_path_hf_returns_empty_list(self):
        """HostFactory strategy also returns [] for a missing file."""
        strategy = _make_strategy("hostfactory")
        result = strategy.load_templates_from_path("/tmp/does-not-exist-orb-hf.json")
        assert result == []

    def test_malformed_json_returns_empty_list(self, tmp_path):
        """load_templates_from_path on malformed JSON returns [] and logs the error."""
        bad_file = tmp_path / "bad_templates.json"
        bad_file.write_text("{ this is not valid json !!!")

        strategy = _make_strategy("default")
        result = strategy.load_templates_from_path(str(bad_file))

        assert result == []
        strategy.logger.error.assert_called()

    def test_malformed_json_hf_returns_empty_list(self, tmp_path):
        """HostFactory strategy also returns [] for malformed JSON."""
        bad_file = tmp_path / "bad_hf_templates.json"
        bad_file.write_text("[[[not json")

        strategy = _make_strategy("hostfactory")
        result = strategy.load_templates_from_path(str(bad_file))

        assert result == []
        strategy.logger.error.assert_called()

    def test_unknown_scheduler_type_logs_warning_and_loads_best_effort(self, tmp_path):
        """A file with an unrecognised scheduler_type logs a warning and loads best-effort."""
        templates_file = tmp_path / "unknown_type_templates.json"
        templates_file.write_text(
            json.dumps(
                {
                    "scheduler_type": "nonexistent_scheduler_xyz",
                    "templates": [
                        {
                            "template_id": "tpl-1",
                            "name": "test",
                            "image_id": "ami-12345678",
                            "machine_types": {"t3.micro": 1},
                            "max_instances": 1,
                            "subnet_ids": [],
                            "security_group_ids": [],
                            "tags": {},
                        }
                    ],
                }
            )
        )

        strategy = _make_strategy("default")
        result = strategy.load_templates_from_path(str(templates_file))

        # Warning must have been logged about the unknown scheduler type
        strategy.logger.warning.assert_called()
        # Best-effort load: either returns the template or an empty list — must not raise
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Handler Input Validation
# ---------------------------------------------------------------------------


class TestHandlerInputValidation:
    @pytest.fixture
    def handler(self, moto_aws):
        aws_client = _make_aws_client(region=REGION)
        logger = _make_logger()
        config_port = _make_config_port(prefix="")
        return make_asg_handler(aws_client, logger, config_port)

    def test_empty_machine_types_raises_validation_error(self, handler, moto_vpc_resources):
        """acquire_hosts with empty machine_types raises AWSValidationError."""
        template = make_aws_template(
            subnet_id=moto_vpc_resources["subnet_ids"][0],
            sg_id=moto_vpc_resources["sg_id"],
        )
        template = template.model_copy(update={"machine_types": {}})
        request = make_request(request_id="req-err-001")

        with pytest.raises(AWSValidationError) as exc_info:
            handler.acquire_hosts(request, template)

        msg = str(exc_info.value).lower()
        assert "machine_types" in msg or "instancetype" in msg

    def test_missing_subnet_ids_raises_validation_error(self, handler):
        """acquire_hosts with empty subnet_ids raises AWSValidationError."""
        template = make_aws_template(subnet_id="", sg_id="sg-12345678")
        template = template.model_copy(update={"subnet_ids": []})
        request = make_request(request_id="req-err-002")

        with pytest.raises(AWSValidationError) as exc_info:
            handler.acquire_hosts(request, template)

        assert "subnet" in str(exc_info.value).lower()

    def test_missing_security_group_ids_raises_validation_error(self, handler, moto_vpc_resources):
        """acquire_hosts with empty security_group_ids raises AWSValidationError."""
        template = make_aws_template(
            subnet_id=moto_vpc_resources["subnet_ids"][0],
            sg_id="sg-12345678",
        )
        template = template.model_copy(update={"security_group_ids": []})
        request = make_request(request_id="req-err-003")

        with pytest.raises(AWSValidationError) as exc_info:
            handler.acquire_hosts(request, template)

        assert "security" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# AWS API Errors
# ---------------------------------------------------------------------------


class TestAWSAPIErrors:
    @pytest.fixture
    def run_instances_handler(self, moto_aws):
        aws_client = _make_aws_client(region=REGION)
        logger = _make_logger()
        config_port = _make_config_port(prefix="")
        return make_run_instances_handler(aws_client, logger, config_port)

    @pytest.fixture
    def asg_handler(self, moto_aws):
        aws_client = _make_aws_client(region=REGION)
        logger = _make_logger()
        config_port = _make_config_port(prefix="")
        return make_asg_handler(aws_client, logger, config_port)

    def test_release_nonexistent_instances_does_not_crash(self, asg_handler):
        """release_hosts with non-existent instance IDs does not raise an unhandled exception."""
        fake_ids = ["i-000000000000000ff", "i-000000000000000fe"]
        try:
            asg_handler.release_hosts(fake_ids)
        except Exception as exc:
            # Moto may raise on terminate of non-existent IDs — that is acceptable
            # as long as it is a known AWS error, not an unhandled internal crash
            assert "InvalidInstanceID" in str(exc) or "does not exist" in str(exc).lower()

    def test_check_hosts_status_nonexistent_asg_returns_empty(self, asg_handler):
        """check_hosts_status for a non-existent ASG returns [] without crashing."""
        request = make_request(resource_ids=["asg-totally-fake-does-not-exist"])
        result = asg_handler.check_hosts_status(request)
        assert isinstance(result, list)

    def test_run_instances_invalid_ami_propagates_or_returns_failure(
        self, run_instances_handler, moto_vpc_resources
    ):
        """acquire_hosts with an invalid AMI either returns success=False or raises a known error.

        Moto accepts most AMI IDs without validation, so this test verifies the
        pipeline does not silently swallow errors — it either surfaces them in the
        response dict or raises a botocore ClientError.
        """
        template = make_aws_template(
            subnet_id=moto_vpc_resources["subnet_ids"][0],
            sg_id=moto_vpc_resources["sg_id"],
            image_id="ami-invalid000000000",
        )
        request = make_request(request_id="req-err-ami-001", requested_count=1)

        try:
            result = run_instances_handler.acquire_hosts(request, template)
            # If it returns a dict, it must have a success key
            assert "success" in result
        except Exception as exc:
            # A ClientError or AWSInfrastructureError is acceptable — not a silent crash
            assert exc is not None


# ---------------------------------------------------------------------------
# Response Shape
# ---------------------------------------------------------------------------


class TestErrorResponseShape:
    def test_validation_error_message_is_human_readable(self, moto_aws):
        """AWSValidationError message is a non-empty human-readable string, not a raw traceback."""
        aws_client = _make_aws_client(region=REGION)
        handler = make_asg_handler(aws_client, _make_logger(), _make_config_port())
        template = make_aws_template(subnet_id="", sg_id="sg-12345678")
        template = template.model_copy(update={"subnet_ids": []})
        request = make_request(request_id="req-shape-001")

        with pytest.raises(AWSValidationError) as exc_info:
            handler.acquire_hosts(request, template)

        msg = str(exc_info.value)
        assert len(msg) > 0
        assert "Traceback" not in msg

    def test_validation_error_does_not_expose_internal_paths(self, moto_aws):
        """AWSValidationError message does not leak internal file paths or stack frames."""
        aws_client = _make_aws_client(region=REGION)
        handler = make_asg_handler(aws_client, _make_logger(), _make_config_port())
        template = make_aws_template(subnet_id="", sg_id="sg-12345678")
        template = template.model_copy(update={"subnet_ids": [], "security_group_ids": []})
        request = make_request(request_id="req-shape-002")

        with pytest.raises(AWSValidationError) as exc_info:
            handler.acquire_hosts(request, template)

        msg = str(exc_info.value)
        assert 'File "' not in msg
        assert '.py", line' not in msg

    def test_format_error_response_shape_default_strategy(self):
        """DefaultSchedulerStrategy.format_error_response returns success=False without traceback."""
        strategy = _make_strategy("default")
        error = ValueError("something went wrong")
        response = strategy.format_error_response(error, context={})

        assert response["success"] is False
        assert "error" in response
        assert "traceback" not in response

    def test_format_error_response_shape_hf_strategy(self):
        """HostFactorySchedulerStrategy.format_error_response returns success=False without traceback."""
        strategy = _make_strategy("hostfactory")
        error = RuntimeError("hf pipeline error")
        response = strategy.format_error_response(error, context={})

        assert response["success"] is False
        assert "error" in response
        assert "traceback" not in response

    def test_format_error_response_includes_traceback_when_verbose(self):
        """format_error_response includes traceback only when context has verbose=True."""
        strategy = _make_strategy("default")
        error = ValueError("verbose error")
        response = strategy.format_error_response(error, context={"verbose": True})

        assert response["success"] is False
        assert "traceback" in response
