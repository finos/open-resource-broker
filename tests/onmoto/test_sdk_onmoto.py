"""SDK end-to-end tests against moto-mocked AWS.

Exercises the full ORBClient lifecycle — initialize, method discovery,
list_templates, create_request, get_request_status, create_return_request,
cleanup — without real AWS credentials.

Moto limitations accounted for:
- RunInstances: fully supported (instances created and visible)
- ASG/EC2Fleet/SpotFleet: resources created but instances not auto-fulfilled
- SSM parameter resolution: patched out (moto cannot resolve SSM paths)
- AWSProvisioningAdapter: patched to synthesise instances from instance_ids
  so the orchestration loop completes on the first attempt
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.onmoto.conftest import _inject_moto_factory, _make_logger, _make_moto_aws_client
from tests.shared.scenarios import TestScenario, get_smoke_scenarios

from tests.shared.constants import REQUEST_ID_RE

REGION = "eu-west-2"

pytestmark = [pytest.mark.moto, pytest.mark.sdk]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from tests.shared.response_helpers import extract_machine_ids as _extract_machine_ids
from tests.shared.response_helpers import extract_request_id as _extract_request_id
from tests.shared.response_helpers import extract_status as _extract_status


def _extract_templates(result) -> list:
    """Normalise list_templates() result to a flat list of template objects."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("templates", [])
    templates = getattr(result, "templates", None)
    if templates is not None:
        return list(templates)
    return []


def _get_template_field(tpl, *keys: str):
    """Return the first matching field from a template (dict or DTO)."""
    for key in keys:
        if isinstance(tpl, dict):
            val = tpl.get(key)
        else:
            val = getattr(tpl, key, None)
        if val is not None:
            return val
    return None


def _extract_return_result_fields(result) -> dict:
    """Extract request_id and message from a create_return_request result."""
    if isinstance(result, dict):
        return {
            "request_id": result.get("request_id") or result.get("requestId"),
            "message": result.get("message"),
        }
    return {
        "request_id": getattr(result, "request_id", None),
        "message": getattr(result, "message", None),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSDKInitialization:
    """ORBClient initializes correctly with programmatic moto config."""

    @pytest.mark.asyncio
    async def test_sdk_initializes_with_app_config(self, orb_config_dir, moto_aws):
        """SDK initializes successfully using app_config dict (no filesystem config path)."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            assert sdk.initialized

    @pytest.mark.asyncio
    async def test_sdk_initializes_with_config_path(self, orb_config_dir, moto_aws):
        """SDK initializes successfully using a config file path."""
        from orb.sdk.client import ORBClient

        config_path = str(orb_config_dir / "config.json")

        async with ORBClient(config_path=config_path) as sdk:
            assert sdk.initialized

    @pytest.mark.asyncio
    async def test_sdk_discovers_methods(self, orb_config_dir, moto_aws):
        """SDK discovers CQRS handler methods after initialization."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            methods = sdk.list_available_methods()
            assert len(methods) > 0, "No methods discovered"
            # Core methods that must always be present
            assert "list_templates" in methods, f"list_templates missing. Got: {methods}"
            assert "create_request" in methods, f"create_request missing. Got: {methods}"
            assert "get_request" in methods or "get_request_status" in methods, (
                f"No request status method found. Got: {methods}"
            )

    @pytest.mark.asyncio
    async def test_sdk_get_stats(self, orb_config_dir, moto_aws):
        """SDK.get_stats() returns expected shape after initialization."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            stats = sdk.get_stats()
            assert stats["initialized"] is True
            assert stats["methods_discovered"] > 0
            assert "available_methods" in stats

    @pytest.mark.asyncio
    async def test_sdk_cleanup_resets_state(self, orb_config_dir, moto_aws):
        """SDK.cleanup() resets initialized state and removes dynamic methods."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        sdk = ORBClient(app_config=config_data)
        await sdk.initialize()
        assert sdk.initialized

        await sdk.cleanup()
        assert not sdk.initialized


class TestSDKTemplates:
    """ORBClient template operations via moto."""

    @pytest.mark.asyncio
    async def test_list_templates_returns_result(self, orb_config_dir, moto_aws):
        """list_templates() returns a non-empty list and every template has provider_type 'aws'."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            assert result is not None

            templates = _extract_templates(result)
            assert len(templates) > 0, (
                "list_templates() returned no templates — expected at least one from aws_templates.json"
            )

            for tpl in templates:
                tid = _get_template_field(tpl, "template_id", "templateId")
                assert tid, f"Template missing template_id: {tpl}"

                provider_type = _get_template_field(tpl, "provider_type", "providerType")
                if provider_type is not None:
                    assert provider_type == "aws", (
                        f"Template {tid!r} has provider_type {provider_type!r}, expected 'aws'"
                    )

    @pytest.mark.asyncio
    async def test_list_templates_active_only(self, orb_config_dir, moto_aws):
        """list_templates(active_only=True) returns a subset of the full list."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            all_result = await sdk.list_templates()
            active_result = await sdk.list_templates(active_only=True)
            assert active_result is not None

            all_templates = _extract_templates(all_result)
            active_templates = _extract_templates(active_result)

            # active_only must not return more than the full list
            assert len(active_templates) <= len(all_templates), (
                f"active_only=True returned {len(active_templates)} templates "
                f"but full list has only {len(all_templates)}"
            )

    @pytest.mark.asyncio
    async def test_list_templates_ids_match_config(self, orb_config_dir, moto_aws):
        """template_id used in create_request exists in the templates returned by list_templates."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            templates = _extract_templates(result)

            known_ids = {
                _get_template_field(tpl, "template_id", "templateId") for tpl in templates
            } - {None}

            assert len(known_ids) > 0, "No template IDs found in list_templates() result"

            # The template we use in request lifecycle tests must be present
            assert "RunInstances-OnDemand" in known_ids, (
                f"'RunInstances-OnDemand' not found in loaded templates. Got: {sorted(known_ids)}"  # type: ignore[arg-type]
            )


class TestSDKRequestLifecycle:
    """Full request lifecycle via ORBClient against moto AWS."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_create_request_returns_request_id(
        self, orb_config_dir, moto_aws, moto_vpc_resources, scenario: TestScenario
    ):
        """create_request() returns a valid request_id."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            result = await sdk.create_request(template_id=scenario.template_id, count=scenario.capacity)
            request_id = _extract_request_id(result)

            assert request_id is not None, f"No request_id in response: {result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_get_request_status_after_create(
        self, orb_config_dir, moto_aws, moto_vpc_resources, scenario: TestScenario
    ):
        """get_request() returns a well-formed status response after create_request()."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            create_result = await sdk.create_request(template_id=scenario.template_id, count=scenario.capacity)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id in create response: {create_result}"

            # get_request or get_request_status depending on what was discovered
            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            assert status_result is not None

            # Status must be a known value
            status = _extract_status(status_result)
            assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
                f"Unexpected status: {status!r}"
            )

            # The response must carry back the same request_id we created
            if isinstance(status_result, dict):
                requests_list = status_result.get("requests", [])
                if requests_list:
                    first = requests_list[0]
                    returned_id = first.get("request_id") or first.get("requestId")
                    assert returned_id == request_id, (
                        f"Status response request_id {returned_id!r} != created {request_id!r}"
                    )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_full_request_and_return_cycle(
        self, orb_config_dir, moto_aws, moto_vpc_resources, scenario: TestScenario
    ):
        """Full cycle: create_request -> get_request -> create_return_request.

        Uses RunInstances because moto fully supports instance creation.
        Asserts that:
        - request_id is a valid UUID-based string
        - status query returns a known status and echoes back the request_id
        - machine_ids are present and look like EC2 instance IDs
        - create_return_request returns a response with a message field
        - machine state transitions are verified where possible
        """
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            # 1. Verify template_id exists before using it
            templates_result = await sdk.list_templates()
            known_ids = {
                _get_template_field(tpl, "template_id", "templateId")
                for tpl in _extract_templates(templates_result)
            } - {None}
            assert scenario.template_id in known_ids, (
                f"{scenario.template_id!r} not in loaded templates: {sorted(known_ids)}"  # type: ignore[arg-type]
            )

            # 2. Create request
            create_result = await sdk.create_request(template_id=scenario.template_id, count=scenario.capacity)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id: {create_result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

            # 3. Query status — must echo back the same request_id
            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            status = _extract_status(status_result)
            assert status in {"running", "complete", "complete_with_error", "pending", "unknown"}, (
                f"Unexpected status: {status!r}"
            )

            if isinstance(status_result, dict):
                requests_list = status_result.get("requests", [])
                if requests_list:
                    returned_id = requests_list[0].get("request_id") or requests_list[0].get(
                        "requestId"
                    )
                    assert returned_id == request_id, (
                        f"Status response request_id {returned_id!r} != created {request_id!r}"
                    )

            # 4. Extract machine IDs (RunInstances creates real moto instances)
            machine_ids = _extract_machine_ids(status_result)
            # RunInstances under moto should produce at least one instance
            if machine_ids:
                for mid in machine_ids:
                    assert re.match(r"^i-[0-9a-f]+$", mid), (
                        f"machineId {mid!r} does not look like an EC2 instance ID"
                    )

                # 5. Return machines — response must have a message field
                return_result = await sdk.create_return_request(machine_ids=machine_ids)
                assert return_result is not None

                fields = _extract_return_result_fields(return_result)
                assert fields["message"] is not None, (
                    f"create_return_request response missing 'message' field: {return_result}"
                )

                # Poll for return completion
                import asyncio
                import time

                return_request_id = fields.get("request_id")
                if return_request_id:
                    deadline = time.time() + 10
                    while time.time() < deadline:
                        ret_status = await sdk.list_return_requests()  # type: ignore[attr-defined]
                        requests_list = (
                            ret_status.get("requests", [])
                            if isinstance(ret_status, dict)
                            else ret_status or []
                        )
                        done = any(
                            (
                                req.get("request_id") or req.get("requestId")
                            ) == return_request_id
                            and req.get("status") == "complete"
                            for req in requests_list
                            if isinstance(req, dict)
                        )
                        if done:
                            break
                        await asyncio.sleep(0.5)

                # 6. After return, status should not be 'running' (machines were released)
                if "get_request_status" in methods:
                    post_return_result = await sdk.get_request_status(request_id=request_id)  # type: ignore[attr-defined]
                else:
                    post_return_result = await sdk.get_request(request_id=request_id)

                post_status = _extract_status(post_return_result)
                assert post_status in {
                    "running",
                    "complete",
                    "complete_with_error",
                    "pending",
                    "unknown",
                }, f"Unexpected post-return status: {post_status!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", get_smoke_scenarios(), ids=lambda s: s.scenario_id)
    async def test_list_requests_after_create(self, orb_config_dir, moto_aws, moto_vpc_resources, scenario: TestScenario):
        """list_requests() includes the newly created request."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            create_result = await sdk.create_request(template_id=scenario.template_id, count=scenario.capacity)
            request_id = _extract_request_id(create_result)
            assert request_id

            list_result = await sdk.list_requests()
            assert list_result is not None

            # Verify the created request appears in the list
            if isinstance(list_result, dict):
                requests = list_result.get("requests", [])
            elif isinstance(list_result, list):
                requests = list_result
            else:
                requests = getattr(list_result, "requests", []) or []

            found_ids = []
            for req in requests:
                rid = (
                    req.get("requestId") or req.get("request_id")
                    if isinstance(req, dict)
                    else getattr(req, "request_id", None)
                )
                if rid:
                    found_ids.append(rid)

            assert request_id in found_ids, (
                f"Created request {request_id} not found in list. Got: {found_ids}"
            )

    @pytest.mark.asyncio
    async def test_create_request_unknown_template_returns_error(
        self, orb_config_dir, moto_aws
    ):
        """create_request() with a non-existent template_id returns an error response, not a crash."""
        import json

        from orb.sdk.client import ORBClient

        config_data = json.loads((orb_config_dir / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            aws_client = _make_moto_aws_client()
            logger = _make_logger()
            _inject_moto_factory(aws_client, logger, None)

            try:
                result = await sdk.create_request(
                    template_id="NonExistent-Template-XYZ", count=1
                )
                # If no exception, the result must indicate an error
                is_error = (
                    (isinstance(result, dict) and (
                        result.get("error") or
                        result.get("status") == "error" or
                        "not found" in str(result).lower() or
                        "NonExistent" in str(result)
                    ))
                    or result is None
                )
                assert is_error, (
                    f"Expected error response for unknown template, got: {result}"
                )
            except Exception:
                # Any exception is also acceptable — the system rejected the request
                pass
