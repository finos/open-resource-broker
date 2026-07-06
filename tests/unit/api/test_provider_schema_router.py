"""Unit tests for provider UI column schema endpoints and UIColumnDescriptor DTO."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import get_current_user  # noqa: F401
from orb.api.routers.providers import router as providers_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*, role: str = "viewer") -> FastAPI:
    from fastapi.responses import JSONResponse

    from orb.api.dependencies import CurrentUser
    from orb.infrastructure.error.exception_handler import get_exception_handler

    app = FastAPI()
    app.include_router(providers_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        username="test-user", role=role
    )
    exception_handler = get_exception_handler()

    @app.exception_handler(Exception)
    async def global_exception_handler(__request, exc):
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            raise exc
        error_response = exception_handler.handle_error_for_http(exc)
        return JSONResponse(
            status_code=error_response.http_status or 500,
            content={"detail": error_response.message},
        )

    return app


# ---------------------------------------------------------------------------
# DTO validation tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUIColumnDescriptorDTO:
    """Verify UIColumnDescriptor pydantic validation."""

    def test_happy_path_minimal(self):
        from orb.application.dto.system import UIColumnDescriptor

        col = UIColumnDescriptor(
            key="aws_instance_type",
            path="provider_data.instance_type",
            label="Instance Type",
            kind="badge",
            resource_type="machines",
        )
        assert col.key == "aws_instance_type"
        assert col.kind == "badge"
        assert col.resource_type == "machines"
        assert col.provider is None
        assert col.sortable is False
        assert col.default_visible is False
        assert col.lockable is False
        assert col.badge_color_map is None

    def test_happy_path_full(self):
        from orb.application.dto.system import UIColumnDescriptor

        col = UIColumnDescriptor(
            key="aws_lifecycle",
            path="provider_data.lifecycle",
            label="Lifecycle",
            kind="badge",
            resource_type="machines",
            provider="aws",
            sortable=True,
            default_visible=True,
            lockable=True,
            badge_color_map={"spot": "orange", "ondemand": "blue"},
        )
        assert col.provider == "aws"
        assert col.sortable is True
        assert col.badge_color_map == {"spot": "orange", "ondemand": "blue"}

    def test_invalid_kind_raises(self):
        from pydantic import ValidationError

        from orb.application.dto.system import UIColumnDescriptor

        with pytest.raises(ValidationError):
            UIColumnDescriptor(
                key="bad",
                path="x",
                label="Bad",
                kind="unknown_kind",  # not in Literal
                resource_type="machines",
            )

    def test_invalid_resource_type_raises(self):
        from pydantic import ValidationError

        from orb.application.dto.system import UIColumnDescriptor

        with pytest.raises(ValidationError):
            UIColumnDescriptor(
                key="bad",
                path="x",
                label="Bad",
                kind="text",
                resource_type="pods",  # not in Literal
            )

    def test_to_dict_excludes_none_fields(self):
        from orb.application.dto.system import UIColumnDescriptor

        col = UIColumnDescriptor(
            key="k",
            path="p",
            label="L",
            kind="text",
            resource_type="templates",
        )
        d = col.to_dict()
        assert "provider" not in d
        assert "badge_color_map" not in d
        assert d["key"] == "k"

    def test_all_valid_kind_values(self):
        from orb.application.dto.system import UIColumnDescriptor

        for kind in ("text", "code", "badge", "timestamp", "count", "link"):
            col = UIColumnDescriptor(
                key="k", path="p", label="L", kind=kind, resource_type="machines"
            )
            assert col.kind == kind

    def test_all_valid_resource_type_values(self):
        from orb.application.dto.system import UIColumnDescriptor

        for rt in ("machines", "requests", "templates"):
            col = UIColumnDescriptor(key="k", path="p", label="L", kind="text", resource_type=rt)
            assert col.resource_type == rt


# ---------------------------------------------------------------------------
# AWS strategy schema tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSStrategyUIColumnSchema:
    """Verify AWSProviderStrategy.get_ui_column_schema returns expected descriptors."""

    def _make_strategy(self):
        """Build a minimal AWSProviderStrategy without live AWS deps."""
        from unittest.mock import MagicMock

        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        config = MagicMock()
        config.region = "us-east-1"
        config.profile = None
        logger = MagicMock()
        strategy = object.__new__(AWSProviderStrategy)
        # Bypass __init__ — we only need get_ui_column_schema
        strategy._aws_config = config
        strategy._logger = logger
        strategy._initialized = False
        return strategy

    def test_returns_non_empty_list(self):
        strategy = self._make_strategy()
        schema = strategy.get_ui_column_schema()
        assert len(schema) > 0

    def test_machines_columns_present(self):
        strategy = self._make_strategy()
        schema = strategy.get_ui_column_schema()
        machine_keys = {c.key for c in schema if c.resource_type == "machines"}
        assert "aws_instance_type" in machine_keys
        assert "aws_availability_zone" in machine_keys
        assert "aws_lifecycle" in machine_keys
        assert "aws_image_id" in machine_keys
        assert "aws_subnet_id" in machine_keys

    def test_requests_columns_present(self):
        strategy = self._make_strategy()
        schema = strategy.get_ui_column_schema()
        request_keys = {c.key for c in schema if c.resource_type == "requests"}
        assert "aws_request_type" in request_keys
        assert "aws_launch_template_id" in request_keys
        assert "aws_launch_template_version" in request_keys
        assert "aws_fulfillment_method" in request_keys

    def test_templates_columns_present(self):
        strategy = self._make_strategy()
        schema = strategy.get_ui_column_schema()
        template_keys = {c.key for c in schema if c.resource_type == "templates"}
        assert "aws_provider_api" in template_keys
        assert "aws_instance_type" in template_keys
        assert "aws_allocation_strategy" in template_keys
        assert "aws_price_type" in template_keys
        assert "aws_key_name" in template_keys
        assert "aws_image_id" in template_keys

    def test_all_descriptors_have_provider_aws(self):
        strategy = self._make_strategy()
        schema = strategy.get_ui_column_schema()
        for col in schema:
            assert col.provider == "aws", f"Column {col.key!r} missing provider='aws'"

    def test_all_descriptors_are_valid_dto_instances(self):
        from orb.application.dto.system import UIColumnDescriptor

        strategy = self._make_strategy()
        schema = strategy.get_ui_column_schema()
        for col in schema:
            assert isinstance(col, UIColumnDescriptor)

    def test_base_strategy_default_returns_empty(self):
        from orb.providers.base.strategy.provider_strategy import ProviderStrategy

        # ProviderStrategy.get_ui_column_schema() must return [] by default
        assert ProviderStrategy.get_ui_column_schema is not None

        # Call it via a concrete minimal stub
        class _StubStrategy(ProviderStrategy):
            @property
            def provider_type(self) -> str:
                return "stub"

            def initialize(self) -> bool:
                return True

            async def execute_operation(self, operation):
                pass

            def get_capabilities(self):
                pass

            def check_health(self):
                pass

            def generate_provider_name(self, config):
                return ""

            def parse_provider_name(self, provider_name):
                return {}

            def get_provider_name_pattern(self) -> str:
                return ""

            def cleanup(self) -> None:
                pass

        stub = object.__new__(_StubStrategy)
        assert stub.get_ui_column_schema() == []


# ---------------------------------------------------------------------------
# Endpoint tests — GET /providers/{name}/schema
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestProviderSchemaEndpoint:
    """Tests for GET /providers/{name}/schema."""

    def _make_mock_registry(self, *, registered: bool, schema: list | None = None):
        from orb.providers.registry.types import ProviderRegistration

        registry = MagicMock()
        registry.is_provider_registered.return_value = registered

        if registered and schema is not None:
            mock_strategy_class = MagicMock()
            mock_instance = MagicMock()
            mock_instance.get_ui_column_schema.return_value = schema
            mock_strategy_class.return_value = mock_instance

            reg = MagicMock(spec=ProviderRegistration)
            reg.strategy_class = mock_strategy_class

            # _get_type_registration is called inside _get_schema_for_provider_type
            registry._get_type_registration.return_value = reg
        return registry

    def test_returns_404_for_unknown_provider(self):
        registry = self._make_mock_registry(registered=False)
        app = _make_app()

        with patch(
            "orb.providers.registry.provider_registry.get_provider_registry",
            return_value=registry,
        ):
            with patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                return_value=[],
            ):
                # Patch the import inside the route function
                import orb.providers.registry.provider_registry as _pr_mod

                original = _pr_mod.get_provider_registry
                _pr_mod.get_provider_registry = lambda: registry
                try:
                    client = TestClient(app, raise_server_exceptions=False)
                    resp = client.get("/providers/nonexistent/schema")
                finally:
                    _pr_mod.get_provider_registry = original

        assert resp.status_code == 404

    def test_returns_200_for_known_provider(self):
        from orb.application.dto.system import UIColumnDescriptor

        col = UIColumnDescriptor(
            key="aws_instance_type",
            path="provider_data.instance_type",
            label="Instance Type",
            kind="badge",
            resource_type="machines",
            provider="aws",
        )
        registry = self._make_mock_registry(registered=True, schema=[col])
        app = _make_app()

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            with patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                return_value=[col.to_dict()],
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.get("/providers/aws/schema")
        finally:
            _pr_mod.get_provider_registry = original

        assert resp.status_code == 200

    def test_response_is_list(self):
        app = _make_app()
        registry = MagicMock()
        registry.is_provider_registered.return_value = True

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            with patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                return_value=[{"key": "aws_instance_type", "label": "Instance Type"}],
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.get("/providers/aws/schema")
        finally:
            _pr_mod.get_provider_registry = original

        assert isinstance(resp.json(), list)

    def test_viewer_role_allowed(self):
        """Viewer role must be sufficient to read schema."""
        app = _make_app(role="viewer")
        registry = MagicMock()
        registry.is_provider_registered.return_value = True

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            with patch("orb.api.routers.providers._get_schema_for_provider_type", return_value=[]):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.get("/providers/aws/schema")
        finally:
            _pr_mod.get_provider_registry = original

        assert resp.status_code == 200

    def test_unknown_role_returns_403(self):
        app = _make_app(role="unknown_role")
        registry = MagicMock()
        registry.is_provider_registered.return_value = True

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/aws/schema")
        finally:
            _pr_mod.get_provider_registry = original

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Endpoint tests — GET /providers/schemas
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.api
class TestAllProviderSchemasEndpoint:
    """Tests for GET /providers/schemas."""

    def _patch_registry(self, registry):
        """Context manager helper to monkey-patch get_provider_registry."""
        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        return original, _pr_mod

    def test_returns_200(self):
        app = _make_app()
        registry = MagicMock()
        registry.get_registered_providers.return_value = []

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/schemas")
        finally:
            _pr_mod.get_provider_registry = original

        assert resp.status_code == 200

    def test_returns_dict_keyed_by_provider(self):
        app = _make_app()
        registry = MagicMock()
        registry.get_registered_providers.return_value = ["aws"]

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            with patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                return_value=[{"key": "aws_instance_type"}],
            ):
                client = TestClient(app, raise_server_exceptions=False)
                body = client.get("/providers/schemas").json()
        finally:
            _pr_mod.get_provider_registry = original

        assert "aws" in body
        assert isinstance(body["aws"], list)

    def test_empty_registry_returns_empty_dict(self):
        app = _make_app()
        registry = MagicMock()
        registry.get_registered_providers.return_value = []

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            client = TestClient(app, raise_server_exceptions=False)
            body = client.get("/providers/schemas").json()
        finally:
            _pr_mod.get_provider_registry = original

        assert body == {}

    def test_viewer_role_allowed(self):
        app = _make_app(role="viewer")
        registry = MagicMock()
        registry.get_registered_providers.return_value = []

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/providers/schemas")
        finally:
            _pr_mod.get_provider_registry = original

        assert resp.status_code == 200

    def test_schema_error_for_one_provider_does_not_crash(self):
        """A schema retrieval failure for one provider must not 500 the whole request."""
        app = _make_app()
        registry = MagicMock()
        registry.get_registered_providers.return_value = ["aws", "gcp"]

        def _schema_side_effect(provider_type):
            if provider_type == "gcp":
                raise RuntimeError("gcp not ready")
            return [{"key": "aws_instance_type"}]

        import orb.providers.registry.provider_registry as _pr_mod

        original = _pr_mod.get_provider_registry
        _pr_mod.get_provider_registry = lambda: registry
        try:
            with patch(
                "orb.api.routers.providers._get_schema_for_provider_type",
                side_effect=_schema_side_effect,
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.get("/providers/schemas")
        finally:
            _pr_mod.get_provider_registry = original

        assert resp.status_code == 200
        body = resp.json()
        assert "aws" in body
        assert body.get("gcp") == []
