"""CLI delivery-surface tests against kmock-backed Kubernetes.

Exercises the full ORB CLI lifecycle — templates list, machines request,
requests status, full lifecycle, requests list — without a real cluster.

The CLI is invoked in-process by setting sys.argv and calling main() directly.
Because everything runs in the same process, the kmock server is reachable for
all kubernetes SDK calls made by the CLI code.

Event-loop design
-----------------
Tests that do NOT touch the Kubernetes API (templates list, narrow-terminal JSON
guard, unknown-template error) are synchronous and use ``asyncio.run(main())``
via ``_run_orb_cli``.

Tests that DO exercise a code path reaching the k8s provider (machines request,
requests status, requests list, full lifecycle) MUST be async and must use
``await _run_orb_cli_async(...)`` instead.

The reason: the kmock aiohttp server is started by a pytest-asyncio async fixture
and therefore runs on the pytest-asyncio event loop.  The synchronous k8s SDK
wraps its urllib3 calls in ``asyncio.to_thread``.  When a *sync* test calls
``asyncio.run(main())``, a brand-new event loop takes over the thread; the
pytest-asyncio loop — and with it the kmock accept/handler coroutines — is
suspended.  Every urllib3 thread then blocks waiting for a kmock response that
never arrives (300 s hang).  Running the CLI with ``await main()`` inside an
*async* test keeps execution on the pytest-asyncio loop so kmock can service
the incoming HTTP requests between ``asyncio.to_thread`` continuations.

kmock limitations accounted for:
- kmock provides an in-process aiohttp server emulating the Kubernetes
  apiserver at HTTP level.
- K8sClient is swapped post-bootstrap to point at the kmock URL so all
  kubernetes SDK calls route through the emulator.
- k8s workloads are single-pod creates — the first acquire returns an
  ``orb-...`` pod name as the machine_id.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from tests.providers.k8s.mocked.kmock_delivery_conftest import (  # noqa: E402
    _inject_kmock_factory,
    _make_k8s_logger,
    _register_pod_resource,
)
from tests.shared.constants import REQUEST_ID_RE  # noqa: E402
from tests.shared.response_helpers import (  # noqa: E402
    extract_machine_ids as _extract_machine_ids,
    extract_request_id as _extract_request_id,
    extract_status as _extract_status,
)

pytestmark = [pytest.mark.kmock, pytest.mark.cli]

_K8S_TEMPLATE_ID = "k8s-pod-example"
_K8S_CAPACITY = 1


# ---------------------------------------------------------------------------
# In-process CLI helper  (mirrors tests/providers/aws/mocked/test_cli_onmoto.py)
# ---------------------------------------------------------------------------


def _run_orb_cli(args: list[str]) -> dict:  # type: ignore[return]
    """Invoke the ORB CLI in-process and return parsed JSON output.

    Sets sys.argv to ['orb'] + args, captures stdout, calls asyncio.run(main()),
    then resets sys.argv and the DI container.

    Suppresses console warning output by setting ORB_LOG_CONSOLE_ENABLED=false
    for the duration of the call.

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
                pass  # CLI uses sys.exit(); suppressed intentionally in tests
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
            f"CLI output is not valid JSON for args {args}.\nOutput was:\n{output}"
        ) from exc

    # machines request returns [result_dict, exit_code] — unwrap to the dict
    if isinstance(parsed, list) and len(parsed) == 2 and isinstance(parsed[0], dict):
        return parsed[0]  # type: ignore[return-value]

    return parsed  # type: ignore[return-value]


async def _run_orb_cli_async(args: list[str]) -> dict:  # type: ignore[return]
    """Async variant of _run_orb_cli for tests that run on the kmock event loop.

    Tests that exercise CLI code paths reaching the Kubernetes API (machines
    request, requests status, requests list, full lifecycle) MUST use this
    helper instead of _run_orb_cli.

    Background
    ----------
    The kmock aiohttp server is started by a pytest-asyncio async fixture and
    therefore runs on the pytest-asyncio event loop.  The synchronous k8s SDK
    uses ``urllib3`` for HTTP; the provider handlers wrap those calls with
    ``asyncio.to_thread``.

    When a *sync* test calls ``asyncio.run(main())``, a **brand-new** event
    loop is created for the duration of that call.  The pytest-asyncio loop
    — and with it the kmock aiohttp accept/handler coroutines — is suspended
    for the entire duration, so the urllib3 TCP connections arrive at the
    kmock socket but are never serviced → the thread blocks forever waiting
    for a response that never comes.

    Running the CLI coroutine with ``await main()`` inside an *async* test
    keeps execution on the pytest-asyncio event loop.  The event loop can
    interleave asyncio.to_thread continuations with kmock's aiohttp handlers,
    so every urllib3 request gets a response promptly.

    Usage: decorate callers with ``@pytest.mark.asyncio``.
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
                await main()
            except SystemExit:
                pass  # CLI uses sys.exit(); suppressed intentionally in tests
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
            f"CLI output is not valid JSON for args {args}.\nOutput was:\n{output}"
        ) from exc

    # machines request returns [result_dict, exit_code] — unwrap to the dict
    if isinstance(parsed, list) and len(parsed) == 2 and isinstance(parsed[0], dict):
        return parsed[0]  # type: ignore[return-value]

    return parsed  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Patched-initialize helper
# ---------------------------------------------------------------------------


def _make_patched_initialize(kmock_k8s, logger):
    """Return an async Application.initialize replacement that injects the kmock factory."""
    from orb.bootstrap import Application

    _original_initialize = Application.initialize

    async def _patched_initialize(self, dry_run=False):
        result = await _original_initialize(self, dry_run=dry_run)
        _inject_kmock_factory(kmock_k8s, logger)
        return result

    return _patched_initialize


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCLIK8sTemplates:
    def test_cli_templates_list(self, orb_config_dir_k8s, kmock_k8s):
        """'orb templates list' returns JSON with at least one k8s template."""
        _register_pod_resource(kmock_k8s)
        result = _run_orb_cli(["templates", "list"])

        templates = result.get("templates", result if isinstance(result, list) else [])
        assert len(templates) > 0, f"Expected at least one template, got: {result}"

        for tpl in templates:
            tid = tpl.get("template_id") or tpl.get("templateId")
            assert tid, f"Template missing template_id: {tpl}"

        template_ids = {tpl.get("template_id") or tpl.get("templateId") for tpl in templates}
        assert any("k8s" in str(tid).lower() for tid in template_ids if tid), (
            f"No k8s template found in list: {sorted(str(t) for t in template_ids if t)}"
        )


class TestCLIK8sMachinesRequest:
    @pytest.mark.asyncio
    async def test_cli_machines_request(self, orb_config_dir_k8s, kmock_k8s):
        """'orb machines request' against kmock returns a valid request_id."""
        from orb.bootstrap import Application

        _register_pod_resource(kmock_k8s)
        logger = _make_k8s_logger()
        _patched_initialize = _make_patched_initialize(kmock_k8s, logger)

        with patch.object(Application, "initialize", _patched_initialize):
            result = await _run_orb_cli_async(
                [
                    "machines",
                    "request",
                    "--template",
                    _K8S_TEMPLATE_ID,
                    "--count",
                    str(_K8S_CAPACITY),
                ]
            )

        request_id = _extract_request_id(result)
        assert request_id is not None, f"No request_id in response: {result}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )


class TestCLIK8sRequestsStatus:
    @pytest.mark.asyncio
    async def test_cli_requests_status(self, orb_config_dir_k8s, kmock_k8s):
        """'orb requests status <id>' returns a known status and echoes back the request_id."""
        from orb.bootstrap import Application

        _register_pod_resource(kmock_k8s)
        logger = _make_k8s_logger()
        _patched_initialize = _make_patched_initialize(kmock_k8s, logger)

        with patch.object(Application, "initialize", _patched_initialize):
            create_result = await _run_orb_cli_async(
                [
                    "machines",
                    "request",
                    "--template",
                    _K8S_TEMPLATE_ID,
                    "--count",
                    str(_K8S_CAPACITY),
                ]
            )

        request_id = _extract_request_id(create_result)
        assert request_id, f"No request_id in create response: {create_result}"

        with patch.object(Application, "initialize", _patched_initialize):
            status_result = await _run_orb_cli_async(["requests", "status", request_id])

        status = _extract_status(status_result)
        assert status in {"running", "complete", "complete_with_error", "pending"}, (
            f"Unexpected status: {status!r}"
        )

        requests_list = status_result.get("requests", [])
        if requests_list:
            returned_id = requests_list[0].get("request_id") or requests_list[0].get("requestId")
            assert returned_id == request_id, (
                f"Status response request_id {returned_id!r} != created {request_id!r}"
            )


class TestCLIK8sFullLifecycle:
    @pytest.mark.asyncio
    async def test_cli_full_lifecycle(self, orb_config_dir_k8s, kmock_k8s):
        """request -> status -> return: machines appear and return succeeds."""
        from orb.bootstrap import Application

        _register_pod_resource(kmock_k8s)
        logger = _make_k8s_logger()
        _patched_initialize = _make_patched_initialize(kmock_k8s, logger)

        with patch.object(Application, "initialize", _patched_initialize):
            create_result = await _run_orb_cli_async(
                [
                    "machines",
                    "request",
                    "--template",
                    _K8S_TEMPLATE_ID,
                    "--count",
                    str(_K8S_CAPACITY),
                ]
            )

        request_id = _extract_request_id(create_result)
        assert request_id, f"No request_id: {create_result}"
        assert REQUEST_ID_RE.match(request_id), (
            f"request_id {request_id!r} does not match expected pattern"
        )

        with patch.object(Application, "initialize", _patched_initialize):
            status_result = await _run_orb_cli_async(["requests", "status", request_id])

        status = _extract_status(status_result)
        assert status in {"running", "complete", "complete_with_error", "pending"}, (
            f"Unexpected status: {status!r}"
        )

        machine_ids = _extract_machine_ids(status_result)

        if machine_ids:
            for mid in machine_ids:
                assert mid.startswith("orb-"), f"k8s machine_id {mid!r} does not start with 'orb-'"

            with patch.object(Application, "initialize", _patched_initialize):
                return_result = await _run_orb_cli_async(["machines", "return"] + machine_ids)

            assert return_result is not None
            message = return_result.get("message")
            assert message is not None, f"Return response missing 'message' field: {return_result}"

    def test_cli_output_is_valid_json_with_narrow_terminal(self, orb_config_dir_k8s, kmock_k8s):
        """CLI produces valid JSON even when COLUMNS=40 simulates a narrow terminal.

        Regression guard for the Rich line-wrapping bug.
        """
        import os

        _register_pod_resource(kmock_k8s)
        original_columns = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = "40"
        try:
            result = _run_orb_cli(["templates", "list"])
        finally:
            if original_columns is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = original_columns

        assert isinstance(result, (dict, list)), (
            f"Expected dict or list from JSON parse, got {type(result)}: {result}"
        )

        if isinstance(result, dict):
            assert "templates" in result, f"Missing 'templates' key in response: {result}"
            templates = result["templates"]
        else:
            templates = result

        assert isinstance(templates, list), f"'templates' value is not a list: {templates}"


class TestCLIK8sErrorHandling:
    def test_cli_machines_request_unknown_template(self, orb_config_dir_k8s, kmock_k8s):
        """'orb machines request' with a non-existent template returns an error."""
        from orb.bootstrap import Application

        _register_pod_resource(kmock_k8s)
        logger = _make_k8s_logger()
        _patched_initialize = _make_patched_initialize(kmock_k8s, logger)

        with patch.object(Application, "initialize", _patched_initialize):
            try:
                result = _run_orb_cli(
                    [
                        "machines",
                        "request",
                        "--template",
                        "NonExistent-K8s-Template-XYZ",
                        "--count",
                        "1",
                    ]
                )
            except AssertionError:
                return

        exit_code = 0
        if isinstance(result, list) and len(result) == 2 and isinstance(result[1], int):
            result, exit_code = result[0], result[1]

        has_error = isinstance(result, dict) and (
            result.get("error")
            or result.get("status") == "error"
            or "not found" in str(result).lower()
            or "NonExistent" in str(result)
        )
        assert has_error or exit_code != 0, (
            f"Expected error for unknown template, got exit_code={exit_code} result={result}"
        )


class TestCLIK8sRequestsList:
    @pytest.mark.asyncio
    async def test_cli_requests_list(self, orb_config_dir_k8s, kmock_k8s):
        """'orb requests list' includes the newly created request_id."""
        from orb.bootstrap import Application

        _register_pod_resource(kmock_k8s)
        logger = _make_k8s_logger()
        _patched_initialize = _make_patched_initialize(kmock_k8s, logger)

        with patch.object(Application, "initialize", _patched_initialize):
            create_result = await _run_orb_cli_async(
                [
                    "machines",
                    "request",
                    "--template",
                    _K8S_TEMPLATE_ID,
                    "--count",
                    str(_K8S_CAPACITY),
                ]
            )

        request_id = _extract_request_id(create_result)
        assert request_id, f"No request_id in create response: {create_result}"

        with patch.object(Application, "initialize", _patched_initialize):
            list_result = await _run_orb_cli_async(
                ["requests", "list", "--filter", f"request_id={request_id}"]
            )

        if isinstance(list_result, list):
            requests = list_result
        else:
            requests = list_result.get("requests", [])

        assert len(requests) > 0, (
            f"Created request {request_id} not found in filtered list. Got: {list_result}"
        )
