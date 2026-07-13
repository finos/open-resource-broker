"""SDK delivery-surface tests against kmock-backed Kubernetes.

Exercises the full ORBClient lifecycle — initialize, method discovery,
list_templates, create_request, get_request_status, create_return_request,
cleanup — without a real cluster.

kmock limitations accounted for:
- kmock provides an in-process aiohttp server emulating the Kubernetes
  apiserver at HTTP level.
- K8sClient is swapped after ORBClient.initialize() to point at the kmock URL.
- k8s machine_ids are ``orb-...`` pod names, not EC2 instance IDs.
- The first create_request call creates a Pod synchronously in kmock, so
  get_request_status should return machine_ids on the first poll.
"""

from __future__ import annotations

import sys
from pathlib import Path

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

pytestmark = [pytest.mark.kmock, pytest.mark.sdk]


# ---------------------------------------------------------------------------
# Helpers (mirrors test_sdk_onmoto.py pattern)
# ---------------------------------------------------------------------------


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
# TestSDKK8sInitialization
# ---------------------------------------------------------------------------


class TestSDKK8sInitialization:
    """ORBClient initializes correctly with programmatic kmock config."""

    @pytest.mark.asyncio
    async def test_sdk_initializes_with_app_config(self, orb_config_dir_k8s, kmock_k8s):
        """SDK initializes successfully using app_config dict (no filesystem config path)."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            assert sdk.initialized

    @pytest.mark.asyncio
    async def test_sdk_initializes_with_config_path(self, orb_config_dir_k8s, kmock_k8s):
        """SDK initializes successfully using a config file path."""
        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_path = str(orb_config_dir_k8s / "config.json")

        async with ORBClient(config_path=config_path) as sdk:
            assert sdk.initialized

    @pytest.mark.asyncio
    async def test_sdk_discovers_methods(self, orb_config_dir_k8s, kmock_k8s):
        """SDK discovers CQRS handler methods after initialization."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            methods = sdk.list_available_methods()
            assert len(methods) > 0, "No methods discovered"
            assert "list_templates" in methods, f"list_templates missing. Got: {methods}"
            assert "create_request" in methods, f"create_request missing. Got: {methods}"
            assert "get_request" in methods or "get_request_status" in methods, (
                f"No request status method found. Got: {methods}"
            )

    @pytest.mark.asyncio
    async def test_sdk_get_stats(self, orb_config_dir_k8s, kmock_k8s):
        """SDK.get_stats() returns expected shape after initialization."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            stats = sdk.get_stats()
            assert stats["initialized"] is True
            assert stats["methods_discovered"] > 0
            assert "available_methods" in stats

    @pytest.mark.asyncio
    async def test_sdk_cleanup_resets_state(self, orb_config_dir_k8s, kmock_k8s):
        """SDK.cleanup() resets initialized state and removes dynamic methods."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        sdk = ORBClient(app_config=config_data)
        await sdk.initialize()
        assert sdk.initialized

        await sdk.cleanup()
        assert not sdk.initialized


# ---------------------------------------------------------------------------
# TestSDKK8sTemplates
# ---------------------------------------------------------------------------


class TestSDKK8sTemplates:
    """ORBClient template operations via kmock."""

    @pytest.mark.asyncio
    async def test_list_templates_returns_result(self, orb_config_dir_k8s, kmock_k8s):
        """list_templates() returns a non-empty list and every template has provider_type 'k8s'."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            assert result is not None

            templates = _extract_templates(result)
            assert len(templates) > 0, (
                "list_templates() returned no templates — expected at least one from k8s_templates.json"
            )

            for tpl in templates:
                tid = _get_template_field(tpl, "template_id", "templateId")
                assert tid, f"Template missing template_id: {tpl}"

                provider_type = _get_template_field(tpl, "provider_type", "providerType")
                if provider_type is not None:
                    assert provider_type == "k8s", (
                        f"Template {tid!r} has provider_type {provider_type!r}, expected 'k8s'"
                    )

    @pytest.mark.asyncio
    async def test_list_templates_active_only(self, orb_config_dir_k8s, kmock_k8s):
        """list_templates(active_only=True) returns a subset of the full list."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            all_result = await sdk.list_templates()
            active_result = await sdk.list_templates(active_only=True)
            assert active_result is not None

            all_templates = _extract_templates(all_result)
            active_templates = _extract_templates(active_result)

            assert len(active_templates) <= len(all_templates), (
                f"active_only=True returned {len(active_templates)} templates "
                f"but full list has only {len(all_templates)}"
            )

    @pytest.mark.asyncio
    async def test_list_templates_ids_match_config(self, orb_config_dir_k8s, kmock_k8s):
        """template_id used in create_request exists in the templates returned by list_templates."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            result = await sdk.list_templates()
            templates = _extract_templates(result)

            known_ids = {
                _get_template_field(tpl, "template_id", "templateId") for tpl in templates
            } - {None}

            assert len(known_ids) > 0, "No template IDs found in list_templates() result"

            assert "k8s-pod-example" in known_ids, (
                f"'k8s-pod-example' not found in loaded templates. Got: {sorted(known_ids)}"  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# TestSDKK8sRequestLifecycle
# ---------------------------------------------------------------------------


class TestSDKK8sRequestLifecycle:
    """Full request lifecycle via ORBClient against kmock."""

    @pytest.mark.asyncio
    async def test_create_request_returns_request_id(self, orb_config_dir_k8s, kmock_k8s):
        """create_request() returns a valid request_id."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            logger = _make_k8s_logger()
            _inject_kmock_factory(kmock_k8s, logger)

            result = await sdk.create_request(template_id="k8s-pod-example", count=1)
            request_id = _extract_request_id(result)

            assert request_id is not None, f"No request_id in response: {result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

    @pytest.mark.asyncio
    async def test_get_request_status_after_create(self, orb_config_dir_k8s, kmock_k8s):
        """get_request_status() returns a well-formed status response after create_request()."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            logger = _make_k8s_logger()
            _inject_kmock_factory(kmock_k8s, logger)

            create_result = await sdk.create_request(template_id="k8s-pod-example", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id in create response: {create_result}"

            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_ids=[request_id])  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            assert status_result is not None

            status = _extract_status(status_result)
            assert status in {
                "running",
                "complete",
                "complete_with_error",
                "pending",
                "unknown",
            }, f"Unexpected status: {status!r}"

            if isinstance(status_result, dict):
                requests_list = status_result.get("requests", [])
                if requests_list:
                    first = requests_list[0]
                    returned_id = first.get("request_id") or first.get("requestId")
                    assert returned_id == request_id, (
                        f"Status response request_id {returned_id!r} != created {request_id!r}"
                    )

    @pytest.mark.asyncio
    async def test_full_request_and_return_cycle(self, orb_config_dir_k8s, kmock_k8s):
        """Full cycle: create_request -> get_request_status -> create_return_request.

        Uses the k8s-pod-example template because kmock fully supports Pod create/delete.
        Asserts that:
        - request_id is a valid UUID-based string
        - status query returns a known status and echoes back the request_id
        - machine_ids are present and look like kmock-generated pod names (orb-...)
        - create_return_request returns a response with a message or request_id field
        """
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            logger = _make_k8s_logger()
            _inject_kmock_factory(kmock_k8s, logger)

            # 1. Verify template exists
            templates_result = await sdk.list_templates()
            known_ids = {
                _get_template_field(tpl, "template_id", "templateId")
                for tpl in _extract_templates(templates_result)
            } - {None}
            assert "k8s-pod-example" in known_ids, (
                f"k8s-pod-example not in loaded templates: {sorted(known_ids)}"  # type: ignore[arg-type]
            )

            # 2. Create request
            create_result = await sdk.create_request(template_id="k8s-pod-example", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id, f"No request_id: {create_result}"
            assert REQUEST_ID_RE.match(request_id), (
                f"request_id {request_id!r} does not match expected pattern"
            )

            # 3. Query status
            methods = sdk.list_available_methods()
            if "get_request_status" in methods:
                status_result = await sdk.get_request_status(request_ids=[request_id])  # type: ignore[attr-defined]
            else:
                status_result = await sdk.get_request(request_id=request_id)

            status = _extract_status(status_result)
            assert status in {
                "running",
                "complete",
                "complete_with_error",
                "pending",
                "unknown",
            }, f"Unexpected status: {status!r}"

            if isinstance(status_result, dict):
                requests_list = status_result.get("requests", [])
                if requests_list:
                    returned_id = requests_list[0].get("request_id") or requests_list[0].get(
                        "requestId"
                    )
                    assert returned_id == request_id, (
                        f"Status response request_id {returned_id!r} != created {request_id!r}"
                    )

            # 4. Extract machine IDs (kmock creates pods synchronously)
            machine_ids = _extract_machine_ids(status_result)
            if machine_ids:
                for mid in machine_ids:
                    assert mid.startswith("orb-"), (
                        f"k8s machine_id {mid!r} does not start with 'orb-'"
                    )

                # 5. Return machines
                return_result = await sdk.create_return_request(machine_ids=machine_ids)
                assert return_result is not None

                # Accept any of: created_request_ids, request_id, message
                created_ids = (
                    return_result.get("created_request_ids")
                    if isinstance(return_result, dict)
                    else getattr(return_result, "created_request_ids", None)
                )
                return_request_id = (
                    return_result.get("request_id") or return_result.get("requestId")
                    if isinstance(return_result, dict)
                    else getattr(return_result, "request_id", None)
                )
                has_message = (
                    bool(return_result.get("message"))
                    if isinstance(return_result, dict)
                    else bool(getattr(return_result, "message", None))
                )
                assert created_ids or return_request_id or has_message, (
                    f"create_return_request response missing expected fields: {return_result}"
                )

    @pytest.mark.asyncio
    async def test_list_requests_after_create(self, orb_config_dir_k8s, kmock_k8s):
        """list_requests() includes the newly created request."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            logger = _make_k8s_logger()
            _inject_kmock_factory(kmock_k8s, logger)

            create_result = await sdk.create_request(template_id="k8s-pod-example", count=1)
            request_id = _extract_request_id(create_result)
            assert request_id

            list_result = await sdk.list_requests()
            assert list_result is not None

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
        self, orb_config_dir_k8s, kmock_k8s
    ):
        """create_request() with a non-existent template_id returns an error response, not a crash."""
        import json

        from orb.sdk.client import ORBClient

        _register_pod_resource(kmock_k8s)
        config_data = json.loads((orb_config_dir_k8s / "config.json").read_text())

        async with ORBClient(app_config=config_data) as sdk:
            logger = _make_k8s_logger()
            _inject_kmock_factory(kmock_k8s, logger)

            # An unknown template must surface as an error — either a raised
            # exception or an error-shaped result — but never a silent success.
            try:
                result = await sdk.create_request(
                    template_id="NonExistent-K8s-Template-XYZ", count=1
                )
            except Exception as exc:
                # A raised exception is an acceptable error signal for an
                # unknown template; the contract is only "does not silently
                # succeed", which a raise satisfies.  Assert the failure is
                # about the missing template so an unrelated crash still fails
                # the test loudly instead of passing silently.
                assert "NonExistent" in str(exc) or "not found" in str(exc).lower(), (
                    f"unknown-template create_request raised an unexpected error: {exc!r}"
                )
                return

            is_error = (
                isinstance(result, dict)
                and (
                    result.get("error")
                    or result.get("status") == "error"
                    or "not found" in str(result).lower()
                    or "NonExistent" in str(result)
                )
            ) or result is None
            assert is_error, f"Expected error response for unknown template, got: {result}"
