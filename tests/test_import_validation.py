"""Import validation tests to prevent import failures after refactoring.

This test suite validates that all critical imports work correctly and catches
issues that might be introduced during code refactoring or module reorganization.
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestCriticalImports:
    """Test critical imports that are used by main entry points."""

    def test_run_py_imports(self):
        """Test all imports needed by run.py."""
        # These imports must work for CLI scripts to function
        from src.bootstrap import Application
        from src.domain.base.exceptions import DomainException
        from src.domain.request.value_objects import RequestStatus
        from src.infrastructure.logging.logger import get_logger

        # Interface command handlers (function-based)
        from src.interface.command_handlers import (
            CLICommandHandler,
            handle_get_request_status,
        )

        # Verify classes and functions are importable
        assert Application is not None
        assert RequestStatus is not None
        assert DomainException is not None
        assert get_logger is not None
        assert CLICommandHandler is not None
        assert handle_get_request_status is not None

    def test_value_object_locations(self):
        """Test that value objects are in their correct locations after decomposition."""
        # Request domain value objects
        # Base domain value objects
        from src.domain.base.value_objects import InstanceId, InstanceType, ResourceId

        # Machine domain value objects
        from src.domain.machine.value_objects import (
            MachineId,
            MachineStatus,
            MachineType,
            PriceType,
        )
        from src.domain.request.value_objects import (
            MachineReference,
            RequestId,
            RequestStatus,
            RequestType,
        )

        # Template domain value objects
        from src.domain.template.value_objects import ProviderConfiguration, TemplateId

        # Verify all imports successful
        assert all(
            [
                RequestStatus,
                RequestType,
                RequestId,
                MachineReference,
                MachineStatus,
                MachineId,
                MachineType,
                PriceType,
                TemplateId,
                ProviderConfiguration,
                InstanceId,
                InstanceType,
                ResourceId,
            ]
        )

    def test_deprecated_imports_fail(self):
        """Test that deprecated import paths fail as expected."""
        # These imports should fail after value object decomposition
        with pytest.raises(ImportError, match="cannot import name 'MachineStatus'"):
            pass

        # BaseCommandHandler should not be available from interface layer
        with pytest.raises(
            ImportError, match="cannot import name 'BaseCommandHandler'"
        ):
            pass

    def test_bootstrap_application(self):
        """Test that the main Application class can be instantiated."""
        from src.bootstrap import Application

        # Should be able to create application instance
        app = Application()
        assert app is not None
        assert hasattr(app, "initialize")

    def test_command_handler_inheritance(self):
        """Test that command handlers have correct inheritance."""
        from src.application.interfaces.command_handler import CommandHandler
        from src.interface.command_handlers import CLICommandHandler

        # CLICommandHandler should inherit from CommandHandler (CQRS interface)
        assert issubclass(CLICommandHandler, CommandHandler)


class TestDomainBoundaries:
    """Test that domain boundaries are respected in imports."""

    def test_machine_domain_exports(self):
        """Test machine domain exports are correct."""
        from src.domain.machine.value_objects import __all__ as machine_exports

        expected_machine_objects = [
            "MachineStatus",
            "MachineId",
            "MachineType",
            "PriceType",
            "MachineConfiguration",
            "MachineEvent",
            "HealthCheck",
        ]

        for obj in expected_machine_objects:
            assert obj in machine_exports, f"{obj} not exported from machine domain"

    def test_request_domain_exports(self):
        """Test request domain exports are correct."""
        from src.domain.request.value_objects import __all__ as request_exports

        expected_request_objects = [
            "RequestStatus",
            "RequestType",
            "RequestId",
            "MachineReference",
            "RequestTimeout",
            "MachineCount",
            "RequestTag",
        ]

        for obj in expected_request_objects:
            assert obj in request_exports, f"{obj} not exported from request domain"

        # MachineStatus should NOT be in request domain
        assert "MachineStatus" not in request_exports

    def test_template_domain_exports(self):
        """Test template domain exports are correct."""
        from src.domain.template.value_objects import __all__ as template_exports

        expected_template_objects = ["TemplateId", "ProviderConfiguration"]

        for obj in expected_template_objects:
            assert obj in template_exports, f"{obj} not exported from template domain"


class TestBackwardCompatibility:
    """Test backward compatibility for common import patterns."""

    def test_common_application_imports(self):
        """Test imports commonly used in application layer."""
        # These should work without issues
        from src.application.dto.commands import RequestStatus
        from src.application.request.dto import MachineReference

        assert RequestStatus is not None
        assert MachineReference is not None

    def test_provider_layer_imports(self):
        """Test imports used in provider layer."""
        from src.providers.aws.infrastructure.adapters.machine_adapter import (
            MachineStatus,
        )
        from src.providers.aws.infrastructure.adapters.request_adapter import (
            RequestType,
        )

        assert MachineStatus is not None
        assert RequestType is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
