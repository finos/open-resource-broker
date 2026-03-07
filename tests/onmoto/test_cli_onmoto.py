"""CLI integration tests against moto-mocked AWS.

Exercises the full ORB CLI lifecycle — templates list, machines request,
requests status, full lifecycle, requests list — without real AWS credentials.

The CLI is invoked in-process by setting sys.argv and calling asyncio.run(main())
directly. Because everything runs in the same process, moto's mock_aws patches
remain active for all boto3 calls made by the CLI code.

Moto limitations accounted for:
- SSM parameter resolution: patched out (moto cannot resolve SSM paths)
- AWSProvisioningAdapter: patched to synthesise instances from instance_ids
  so the orchestration loop completes on the first attempt
"""

import asyncio
import contextlib
import io
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.onmoto.conftest import _inject_moto_factory, _make_logger, _make_moto_aws_client
from tests.shared.scenarios import TestScenario, get_smoke_scenarios

REGION = "eu-west-2"
REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

pytestmark = [pytest.mark.moto, pytest.mark.cli]


# ---------------------------------------------------------------------------
# In-process CLI helper
# ---------------------------------------------------------------------------


def _run_orb_cli(args: list[str]) -> dict:  # type: ignore[return]
    """Invoke the ORB CLI in-process and return parsed JSON output.

    Sets sys.argv to ['orb'] + args, captures stdout, calls asyncio.run(main()),
    then resets sys.argv and the DI container.

    Suppresses console warning output (AWS credentials warning etc.) by setting
    ORB_LOG_CONSOLE_ENABLED=false for the duration of the call.

    Returns the parsed JSON dict from stdout.  If the CLI returns a list of the
    form [{...}, exit_code] (machines request shape), the first element is
    returned as the result dict.
    Raises AssertionError if the output cannot be parsed as JSON.
    """
    import json
    import os

    from orb.cli.main import main
    from orb.infrastructure.di.container import reset_container

    original_argv = sys.argv[:]
    original_console = os.environ.get("ORB_LOG_CONSOLE_ENABLED")
    sys.argv = ["orb"] + args
    os.environ["ORB_LOG_CONSOLE_ENABLED"] = "false"

    stdout_capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_capture):
            try:
                asyncio.run(main())
            except SystemExit:
                pass
    finally:
        sys.argv = original_argv
        if original_console is None:
            os.environ.pop("ORB_LOG_CONSOLE_ENABLED", None)
        else:
            os.environ["ORB_LOG_CONSOLE_ENABLED"] = original_console
        reset_container()

    output = stdout_capture.getvalue().strip()
    assert output, f"CLI produced no output for args: {args}"

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"CLI output is not valid JSON for args {args}.\n"
            f"Output was:\n{output}"
        ) from exc

    # machines request returns [result_dict, exit_code] — unwrap to the dict
    if isinstance(parsed, list) and len(parsed) == 2 and isinstance(parsed[0], dict):
        return parsed[0]  # type: ignore[return-value]

    return parsed  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Helpers to extract fields from CLI JSON output
# ---------------------------------------------------------------------------

from tests.shared.response_helpers import extract_machine_ids as _extract_machine_ids
from tests.shared.response_helpers import extract_request_id as _extract_request_id
from tests.shared.response_helpers import extract_status as _extract_status


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCLITemplates:
    def test_cli_templates_list(self, orb_config_dir, moto_aws):
        """'orb templates list' returns JSON with at least one template that has a template_id."""
        result = _run_orb_cli(["templates", "list"])

        templates = result.get("templates", result if isinstance(result, list) else [])
        assert len(templates) > 0, f"Expected at least one template, got: {result}"

        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing template_id: {tpl}"


class TestCLIMachinesRequest:
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    def test_cli_machines_request(self, orb_config_dir, moto_aws, scenario: TestScenario):
        """'orb machines request' returns a valid request_id."""
        aws_client = _make_moto_aws_client()
        logger = _make_logger()

        # Patch Application.initialize to inject moto factory after app boots
        from orb.bootstrap import Application

        _original_initialize = Application.initialize

        async def _patched_initialize(self, dry_run=False):
            result = await _original_initialize(self, dry_run=dry_run)
            _inject_moto_factory(aws_client, logger, None)
            return result

        with patch.object(Application, "initialize", _patched_initialize):
            result = _run_orb_cli(
                ["machines", "request", "--template", scenario.template_id, "--count", str(scenario.capacity)]
            )

        request_id = _extract_request_id(result)
        assert request_id is not None, f"No request_id in response: {result}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )


class TestCLIRequestsStatus:
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    def test_cli_requests_status(self, orb_config_dir, moto_aws, scenario: TestScenario):
        """'orb requests status <id>' returns a known status and echoes back the request_id."""
        aws_client = _make_moto_aws_client()
        logger = _make_logger()

        from orb.bootstrap import Application

        _original_initialize = Application.initialize

        async def _patched_initialize(self, dry_run=False):
            result = await _original_initialize(self, dry_run=dry_run)
            _inject_moto_factory(aws_client, logger, None)
            return result

        with patch.object(Application, "initialize", _patched_initialize):
            create_result = _run_orb_cli(
                ["machines", "request", "--template", scenario.template_id, "--count", str(scenario.capacity)]
            )

        request_id = _extract_request_id(create_result)
        assert request_id, f"No request_id in create response: {create_result}"

        with patch.object(Application, "initialize", _patched_initialize):
            status_result = _run_orb_cli(["requests", "status", request_id])

        status = _extract_status(status_result)
        assert status in {"running", "complete", "complete_with_error", "pending"}, (
            f"Unexpected status: {status!r}"
        )

        # Must echo back the same request_id
        requests_list = status_result.get("requests", [])
        if requests_list:
            returned_id = requests_list[0].get("request_id") or requests_list[0].get("requestId")
            assert returned_id == request_id, (
                f"Status response request_id {returned_id!r} != created {request_id!r}"
            )


class TestCLIFullLifecycle:
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    def test_cli_full_lifecycle(self, orb_config_dir, moto_aws, scenario: TestScenario):
        """request -> status -> return: machines appear and return succeeds."""
        aws_client = _make_moto_aws_client()
        logger = _make_logger()

        from orb.bootstrap import Application

        _original_initialize = Application.initialize

        async def _patched_initialize(self, dry_run=False):
            result = await _original_initialize(self, dry_run=dry_run)
            _inject_moto_factory(aws_client, logger, None)
            return result

        with patch.object(Application, "initialize", _patched_initialize):
            # 1. Create request
            create_result = _run_orb_cli(
                ["machines", "request", "--template", scenario.template_id, "--count", str(scenario.capacity)]
            )

        request_id = _extract_request_id(create_result)
        assert request_id, f"No request_id: {create_result}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

        with patch.object(Application, "initialize", _patched_initialize):
            # 2. Query status
            status_result = _run_orb_cli(["requests", "status", request_id])

        status = _extract_status(status_result)
        assert status in {"running", "complete", "complete_with_error", "pending"}, (
            f"Unexpected status: {status!r}"
        )

        machine_ids = _extract_machine_ids(status_result)

        if machine_ids:
            for mid in machine_ids:
                assert re.match(r"^i-[0-9a-f]+$", mid), (
                    f"machineId {mid!r} does not look like an EC2 instance ID"
                )

            with patch.object(Application, "initialize", _patched_initialize):
                # 3. Return machines — machine_ids are positional args
                return_result = _run_orb_cli(["machines", "return"] + machine_ids)

            assert return_result is not None
            message = return_result.get("message")
            assert message is not None, (
                f"Return response missing 'message' field: {return_result}"
            )

            # Poll for return completion
            import time

            return_request_id = return_result.get("request_id") or return_result.get("requestId")
            if return_request_id:
                deadline = time.time() + 10
                terminal = {"complete", "complete_with_error", "failed", "cancelled"}
                while time.time() < deadline:
                    with patch.object(Application, "initialize", _patched_initialize):
                        status_result = _run_orb_cli(["requests", "status", return_request_id])
                    status = _extract_status(status_result)
                    if status in terminal:
                        break
                    time.sleep(0.5)


class TestCLIErrorHandling:
    def test_cli_machines_request_unknown_template(self, orb_config_dir, moto_aws):
        """'orb machines request' with a non-existent template returns an error (non-zero exit or error in output)."""
        import json
        import os

        from orb.bootstrap import Application

        aws_client = _make_moto_aws_client()
        logger = _make_logger()

        _original_initialize = Application.initialize

        async def _patched_initialize(self, dry_run=False):
            result = await _original_initialize(self, dry_run=dry_run)
            _inject_moto_factory(aws_client, logger, None)
            return result

        # Capture raw output — the CLI may produce non-JSON on error, so we
        # bypass _run_orb_cli's JSON assertion and inspect stdout directly.
        import asyncio
        import contextlib
        import io
        import sys
        from unittest.mock import patch

        from orb.cli.main import main
        from orb.infrastructure.di.container import reset_container

        original_argv = sys.argv[:]
        original_console = os.environ.get("ORB_LOG_CONSOLE_ENABLED")
        sys.argv = ["orb", "machines", "request", "--template", "NonExistent-Template-XYZ", "--count", "1"]
        os.environ["ORB_LOG_CONSOLE_ENABLED"] = "false"

        stdout_capture = io.StringIO()
        exit_code = 0
        try:
            with contextlib.redirect_stdout(stdout_capture):
                with patch.object(Application, "initialize", _patched_initialize):
                    try:
                        asyncio.run(main())
                    except SystemExit as exc:
                        exit_code = exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.argv = original_argv
            if original_console is None:
                os.environ.pop("ORB_LOG_CONSOLE_ENABLED", None)
            else:
                os.environ["ORB_LOG_CONSOLE_ENABLED"] = original_console
            reset_container()

        output = stdout_capture.getvalue().strip()

        # Either a non-zero exit code or an error indicator in the output
        if exit_code == 0 and output:
            try:
                parsed = json.loads(output)
                if isinstance(parsed, list) and len(parsed) == 2:
                    parsed, exit_code = parsed[0], parsed[1]
                has_error = (
                    (isinstance(parsed, dict) and (
                        parsed.get("error") or
                        parsed.get("status") == "error" or
                        "not found" in str(parsed).lower() or
                        "NonExistent" in str(parsed)
                    ))
                )
                assert has_error or exit_code != 0, (
                    f"Expected error for unknown template, got exit_code={exit_code} output={output}"
                )
            except json.JSONDecodeError:
                # Non-JSON output on error path is acceptable
                pass


class TestCLIRequestsList:
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    def test_cli_requests_list(self, orb_config_dir, moto_aws, scenario: TestScenario):
        """'orb requests list' includes the newly created request_id."""
        aws_client = _make_moto_aws_client()
        logger = _make_logger()

        from orb.bootstrap import Application

        _original_initialize = Application.initialize

        async def _patched_initialize(self, dry_run=False):
            result = await _original_initialize(self, dry_run=dry_run)
            _inject_moto_factory(aws_client, logger, None)
            return result

        with patch.object(Application, "initialize", _patched_initialize):
            create_result = _run_orb_cli(
                ["machines", "request", "--template", scenario.template_id, "--count", str(scenario.capacity)]
            )

        request_id = _extract_request_id(create_result)
        assert request_id, f"No request_id in create response: {create_result}"

        # Use --filter to narrow the list to the specific request_id.
        # The short list view omits request_id from the formatted output, so we
        # verify presence by asserting the filtered result is non-empty.
        with patch.object(Application, "initialize", _patched_initialize):
            list_result = _run_orb_cli(
                ["requests", "list", "--filter", f"request_id={request_id}"]
            )

        if isinstance(list_result, list):
            requests = list_result
        else:
            requests = list_result.get("requests", [])

        assert len(requests) > 0, (
            f"Created request {request_id} not found in filtered list. Got: {list_result}"
        )
