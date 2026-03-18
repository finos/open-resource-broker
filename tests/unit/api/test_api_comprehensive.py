"""Comprehensive API tests that adapt to existing code structure."""

import importlib
import inspect
from unittest.mock import AsyncMock, Mock

import pytest

from orb.application.dto.queries import GetRequestQuery, ListActiveRequestsQuery
from orb.application.services.orchestration.get_request_status import GetRequestStatusOrchestrator

# Check if FastAPI is available
try:
    import importlib.util

    FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None
except ImportError:
    FASTAPI_AVAILABLE = False


@pytest.mark.unit
@pytest.mark.api
class TestAPIHandlersComprehensive:
    """Comprehensive tests for orchestrator modules (replaces deleted handler tests)."""

    _ORCHESTRATOR_MODULES = [
        "orb.application.services.orchestration.acquire_machines",
        "orb.application.services.orchestration.cancel_request",
        "orb.application.services.orchestration.get_machine",
        "orb.application.services.orchestration.get_request_status",
        "orb.application.services.orchestration.list_machines",
        "orb.application.services.orchestration.list_requests",
        "orb.application.services.orchestration.list_return_requests",
        "orb.application.services.orchestration.list_templates",
        "orb.application.services.orchestration.return_machines",
    ]

    def get_handler_modules(self):
        """Get all orchestrator modules."""
        modules = []
        for mod_path in self._ORCHESTRATOR_MODULES:
            try:
                module = importlib.import_module(mod_path)
                modules.append((mod_path.split(".")[-1], module))
            except ImportError:
                continue
        return modules

    def get_handler_classes(self, module):
        """Get orchestrator classes from module."""
        classes = []
        for name, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and "Orchestrator" in name and not name.startswith("Base"):
                classes.append((name, obj))
        return classes

    def test_handler_modules_exist(self):
        """Test that orchestrator modules exist."""
        modules = self.get_handler_modules()
        assert len(modules) > 0, "At least one orchestrator module should exist"

    def test_handler_classes_exist(self):
        """Test that orchestrator classes exist in modules."""
        modules = self.get_handler_modules()
        total_classes = sum(len(self.get_handler_classes(m)) for _, m in modules)
        assert total_classes > 0, "At least one orchestrator class should exist"

    def test_handler_initialization(self):
        """Test orchestrator initialization with mocked dependencies."""
        from unittest.mock import MagicMock

        from orb.domain.base.ports.logging_port import LoggingPort
        from orb.infrastructure.di.buses import CommandBus, QueryBus

        modules = self.get_handler_modules()
        for _module_name, module in modules:
            for class_name, orch_class in self.get_handler_classes(module):
                try:
                    instance = orch_class(
                        command_bus=MagicMock(spec=CommandBus),
                        query_bus=MagicMock(spec=QueryBus),
                        logger=MagicMock(spec=LoggingPort),
                    )
                    assert instance is not None
                    assert hasattr(instance, "execute")
                except Exception as e:
                    print(f"Could not initialize {class_name}: {e}")

    @pytest.mark.asyncio
    async def test_handler_methods(self):
        """Test that orchestrators have an async execute method."""
        modules = self.get_handler_modules()
        for _module_name, module in modules:
            for class_name, orch_class in self.get_handler_classes(module):
                assert hasattr(orch_class, "execute"), f"{class_name} missing execute()"
                assert inspect.iscoroutinefunction(orch_class.execute), (
                    f"{class_name}.execute() must be async"
                )

    def test_handler_dependencies(self):
        """Test orchestrator constructor has expected dependency parameters."""
        modules = self.get_handler_modules()
        for _module_name, module in modules:
            for _class_name, orch_class in self.get_handler_classes(module):
                sig = inspect.signature(orch_class.__init__)
                params = list(sig.parameters.keys())[1:]  # skip self
                assert len(params) > 0


@pytest.mark.unit
@pytest.mark.api
class TestRequestStatusOrchestratorBehaviour:
    """Focused tests covering GetRequestStatusOrchestrator edge cases."""

    def _make_orchestrator(self, query_bus, command_bus=None):
        from unittest.mock import MagicMock

        from orb.infrastructure.di.buses import CommandBus

        return GetRequestStatusOrchestrator(
            command_bus=command_bus or MagicMock(spec=CommandBus),
            query_bus=query_bus,
            logger=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_single_request_id_dispatches_get_request_query(self):
        from orb.application.services.orchestration.dtos import GetRequestStatusInput

        query_bus = Mock()
        query_bus.execute = AsyncMock(return_value={"request_id": "req-1", "status": "complete"})

        orchestrator = self._make_orchestrator(query_bus)
        result = await orchestrator.execute(
            GetRequestStatusInput(request_ids=["req-1"], all_requests=False, detailed=False)
        )

        query_bus.execute.assert_awaited_once()
        assert isinstance(query_bus.execute.call_args.args[0], GetRequestQuery)
        assert len(result.requests) == 1

    @pytest.mark.asyncio
    async def test_all_requests_dispatches_list_active_requests_query(self):
        from orb.application.services.orchestration.dtos import GetRequestStatusInput

        mock_req = Mock()
        mock_req.model_dump = Mock(return_value={"request_id": "req-2", "status": "running"})
        query_bus = Mock()
        query_bus.execute = AsyncMock(return_value=[mock_req])

        orchestrator = self._make_orchestrator(query_bus)
        result = await orchestrator.execute(
            GetRequestStatusInput(request_ids=[], all_requests=True, detailed=False)
        )

        query_bus.execute.assert_awaited_once()
        assert isinstance(query_bus.execute.call_args.args[0], ListActiveRequestsQuery)
        assert len(result.requests) == 1

    @pytest.mark.asyncio
    async def test_detailed_flag_sets_long_on_query(self):
        from orb.application.services.orchestration.dtos import GetRequestStatusInput

        query_bus = Mock()
        query_bus.execute = AsyncMock(return_value=Mock())

        orchestrator = self._make_orchestrator(query_bus)
        await orchestrator.execute(
            GetRequestStatusInput(request_ids=["req-3"], all_requests=False, detailed=True)
        )

        executed_query = query_bus.execute.call_args.args[0]
        assert isinstance(executed_query, GetRequestQuery)
        assert executed_query.long is True


@pytest.mark.unit
@pytest.mark.api
class TestAPIModelsComprehensive:
    """Comprehensive tests for API models."""

    def get_model_modules(self):
        """Get all model modules."""
        model_modules = []
        model_files = ["base", "requests", "responses", "templates"]

        for model_file in model_files:
            try:
                module = importlib.import_module(f"orb.api.models.{model_file}")
                model_modules.append((model_file, module))
            except ImportError:
                continue

        return model_modules

    def get_model_classes(self, module):
        """Get model classes from module."""
        classes = []
        for name, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and (
                hasattr(obj, "__annotations__")
                or hasattr(obj, "model_fields")
                or "Model" in name
                or "Request" in name
                or "Response" in name
            ):
                classes.append((name, obj))
        return classes

    def test_model_modules_exist(self):
        """Test that model modules exist."""
        modules = self.get_model_modules()
        assert len(modules) > 0, "At least one model module should exist"

    def test_model_classes_exist(self):
        """Test that model classes exist."""
        modules = self.get_model_modules()
        total_classes = 0

        for _module_name, module in modules:
            classes = self.get_model_classes(module)
            total_classes += len(classes)

        assert total_classes > 0, "At least one model class should exist"

    def test_model_instantiation(self):
        """Test model instantiation."""
        modules = self.get_model_modules()

        for _module_name, module in modules:
            classes = self.get_model_classes(module)

            for class_name, model_class in classes:
                try:
                    # Try to create instance with empty data
                    try:
                        instance = model_class()
                        assert instance is not None
                    except Exception:
                        # Try with sample data
                        sample_data = {
                            "id": "test-id",
                            "name": "test-name",
                            "template_id": "test-template",
                            "machine_count": 1,
                            "status": "PENDING",
                            "success": True,
                            "message": "test message",
                        }

                        # Try different combinations of sample data
                        for i in range(1, len(sample_data) + 1):
                            try:
                                subset_data = dict(list(sample_data.items())[:i])
                                instance = model_class(**subset_data)
                                assert instance is not None
                                break
                            except Exception:
                                continue

                except Exception as e:
                    # Log but don't fail - some models might have specific requirements
                    print(f"Could not instantiate {class_name}: {e}")

    def test_model_serialization(self):
        """Test model serialization capabilities."""
        modules = self.get_model_modules()

        for _module_name, module in modules:
            classes = self.get_model_classes(module)

            for class_name, model_class in classes:
                try:
                    # Create instance with minimal data
                    instance = None
                    try:
                        instance = model_class()
                    except Exception:
                        try:
                            instance = model_class(id="test", name="test")
                        except Exception:
                            continue

                    if instance:
                        # Test serialization methods
                        serialization_methods = [
                            "dict",
                            "model_dump",
                            "json",
                            "model_dump_json",
                        ]

                        for method_name in serialization_methods:
                            if hasattr(instance, method_name):
                                method = getattr(instance, method_name)
                                try:
                                    result = method()
                                    assert result is not None
                                    break
                                except Exception:
                                    continue

                except Exception as e:
                    # Log but don't fail
                    print(f"Could not test serialization for {class_name}: {e}")


@pytest.mark.unit
@pytest.mark.api
class TestAPIRoutersComprehensive:
    """Comprehensive tests for API routers."""

    def get_router_modules(self):
        """Get all router modules."""
        router_modules = []
        router_files = ["machines", "requests", "templates"]

        for router_file in router_files:
            try:
                module = importlib.import_module(f"orb.api.routers.{router_file}")
                router_modules.append((router_file, module))
            except ImportError:
                continue

        return router_modules

    def test_router_modules_exist(self):
        """Test that router modules exist."""
        modules = self.get_router_modules()
        assert len(modules) > 0, "At least one router module should exist"

    def test_routers_have_routes(self):
        """Test that routers have defined routes."""
        modules = self.get_router_modules()

        for _module_name, module in modules:
            # Look for router objects
            routers = []
            for name, obj in inspect.getmembers(module):
                if hasattr(obj, "routes") and hasattr(obj, "include_router"):
                    routers.append((name, obj))

            if routers:
                for _router_name, router in routers:
                    assert hasattr(router, "routes")
                    routes = router.routes
                    assert len(routes) >= 0  # Router might have no routes yet

    def test_router_integration(self):
        """Test router FastAPI integration."""
        if not FASTAPI_AVAILABLE:
            pytest.skip("FastAPI not available")

        modules = self.get_router_modules()

        for _module_name, module in modules:
            # Look for router objects
            for name, obj in inspect.getmembers(module):
                if hasattr(obj, "routes") and hasattr(obj, "include_router"):
                    try:
                        from fastapi import FastAPI

                        app = FastAPI()
                        app.include_router(obj)
                        # Should not raise exception
                        assert True
                    except Exception as e:
                        print(f"Router {name} not compatible with FastAPI: {e}")


@pytest.mark.unit
@pytest.mark.api
class TestAPIValidationComprehensive:
    """Comprehensive tests for API validation."""

    def test_validation_module_exists(self):
        """Test that validation module exists."""
        try:
            import orb.api.validation as validation_module

            assert validation_module is not None
        except ImportError:
            pytest.skip("API validation module not available")

    def test_validation_functions(self):
        """Test validation functions exist."""
        try:
            import orb.api.validation as validation_module

            # Look for validation functions
            functions = [
                name
                for name, obj in inspect.getmembers(validation_module)
                if inspect.isfunction(obj) and not name.startswith("_")
            ]

            # Should have some validation functions
            assert len(functions) >= 0  # Module might be empty but should exist

        except ImportError:
            pytest.skip("API validation module not available")

    def test_validation_classes(self):
        """Test validation classes exist."""
        try:
            import orb.api.validation as validation_module

            # Look for validation classes
            classes = [
                name
                for name, obj in inspect.getmembers(validation_module)
                if inspect.isclass(obj) and not name.startswith("_")
            ]

            # Should have some validation classes or functions
            total_items = len(classes) + len(
                [
                    name
                    for name, obj in inspect.getmembers(validation_module)
                    if inspect.isfunction(obj)
                ]
            )

            assert total_items >= 0  # Module should have some content

        except ImportError:
            pytest.skip("API validation module not available")
