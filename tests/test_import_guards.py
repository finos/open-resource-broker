"""Test import guards for optional dependencies.

This test suite validates that:
1. Import guards work correctly for all optional dependencies
2. Graceful degradation occurs when optional dependencies are missing
3. Error messages are helpful and guide users to correct installation
4. Core functionality works regardless of optional dependencies
"""

import sys
import subprocess
from unittest.mock import patch
import pytest


class TestImportGuards:
    """Test that import guards work correctly for optional dependencies."""

    def test_cli_console_without_rich(self):
        """Test CLI console works without Rich installed."""
        with patch.dict(sys.modules, {"rich": None, "rich.console": None, "rich_argparse": None}):
            # Force reimport to test fallback
            if "cli.console" in sys.modules:
                del sys.modules["cli.console"]

            from cli.console import get_console, print_success, print_error

            console = get_console()
            assert console is not None

            # These should not raise exceptions
            print_success("test success")
            print_error("test error")

    def test_cli_formatters_without_rich(self):
        """Test CLI formatters work without Rich installed."""
        with patch.dict(sys.modules, {"rich": None, "rich.table": None, "rich.console": None}):
            # Force reimport to test fallback
            if "cli.formatters" in sys.modules:
                del sys.modules["cli.formatters"]

            from cli.formatters import format_generic_table, format_generic_list

            test_data = [
                {"id": "1", "name": "test1", "status": "active"},
                {"id": "2", "name": "test2", "status": "inactive"},
            ]

            # Should fall back to ASCII table
            table_result = format_generic_table(test_data, "Test Items")
            assert "Test Items:" in table_result
            assert "test1" in table_result
            assert "test2" in table_result

            # Should work for list format too
            list_result = format_generic_list(test_data, "Test Items")
            assert "Test Items:" in list_result
            assert "test1" in list_result

    def test_cli_main_without_rich_argparse(self):
        """Test CLI main works without rich-argparse."""
        with patch.dict(sys.modules, {"rich_argparse": None}):
            # Force reimport to test fallback
            if "cli.main" in sys.modules:
                del sys.modules["cli.main"]

            from cli.main import parse_args

            # Should use standard argparse formatter
            # This is a basic test - full CLI testing would need more setup
            assert parse_args is not None

    def test_api_server_without_fastapi(self):
        """Test API server gracefully fails without FastAPI."""
        with patch.dict(
            sys.modules,
            {
                "fastapi": None,
                "fastapi.middleware": None,
                "fastapi.middleware.cors": None,
                "fastapi.middleware.trustedhost": None,
                "fastapi.responses": None,
            },
        ):
            # Force reimport to test guard
            if "api.server" in sys.modules:
                del sys.modules["api.server"]

            from api.server import create_fastapi_app

            with pytest.raises(ImportError) as exc_info:
                create_fastapi_app(None)

            error_msg = str(exc_info.value)
            assert "FastAPI not installed" in error_msg
            assert "pip install orb-py[api]" in error_msg

    def test_monitoring_without_optional_deps(self):
        """Test monitoring works without optional dependencies."""
        with patch.dict(
            sys.modules,
            {
                "psutil": None,
                "prometheus_client": None,
                "opentelemetry": None,
                "opentelemetry.trace": None,
            },
        ):
            # Force reimport to test guards
            if "monitoring.health" in sys.modules:
                del sys.modules["monitoring.health"]

            from monitoring.health import HealthStatus

            # Should create but with limited functionality
            health_status = HealthStatus(name="test", status="healthy", details={"test": "value"})
            assert health_status is not None
            assert health_status.name == "test"

    def test_core_imports_always_work(self):
        """Test core imports work regardless of optional dependencies."""
        # Mock all optional dependencies as unavailable
        optional_modules = [
            "rich",
            "rich.console",
            "rich.table",
            "rich_argparse",
            "fastapi",
            "fastapi.middleware",
            "uvicorn",
            "psutil",
            "prometheus_client",
            "opentelemetry",
        ]

        with patch.dict(sys.modules, {mod: None for mod in optional_modules}):
            # These should never fail
            from bootstrap import Application
            from domain.base.exceptions import DomainException
            from infrastructure.logging.logger import get_logger

            assert Application is not None
            assert DomainException is not None
            assert get_logger is not None

            # Test basic instantiation
            logger = get_logger(__name__)
            assert logger is not None


class TestPackageVariants:
    """Test different package installation variants."""

    @pytest.mark.integration
    def test_minimal_package_functionality(self):
        """Test minimal package provides core functionality."""
        # Test core imports work
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import run
from bootstrap import Application
from domain.base.exceptions import DomainException
from infrastructure.logging.logger import get_logger
print('CORE_IMPORTS_OK')
            """,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert "CORE_IMPORTS_OK" in result.stdout

    @pytest.mark.integration
    def test_cli_fallback_functionality(self):
        """Test CLI package provides fallback functionality."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
from cli.formatters import format_generic_table
from cli.console import get_console

# Test with sample data
test_data = [{'id': '1', 'name': 'test'}]
result = format_generic_table(test_data, 'Test Items')
console = get_console()
console.print('Console test')
print('CLI_FALLBACK_OK')
            """,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert "CLI_FALLBACK_OK" in result.stdout


class TestErrorMessages:
    """Test that error messages are helpful and guide users."""

    def test_api_error_message_helpful(self):
        """Test API error message tells user how to install."""
        with patch.dict(sys.modules, {"fastapi": None}):
            if "api.server" in sys.modules:
                del sys.modules["api.server"]

            from api.server import create_fastapi_app

            with pytest.raises(ImportError) as exc_info:
                create_fastapi_app(None)

            error_msg = str(exc_info.value)
            assert "FastAPI not installed" in error_msg
            assert "pip install orb-py[api]" in error_msg

    def test_serve_command_error_message(self):
        """Test serve command error message is helpful."""
        with patch.dict(sys.modules, {"fastapi": None, "uvicorn": None}):
            # This would test the serve command handler
            # Implementation depends on the actual serve command structure
            pass

    def test_monitoring_graceful_degradation(self):
        """Test monitoring provides helpful messages for missing features."""
        with patch.dict(sys.modules, {"prometheus_client": None}):
            # Test that monitoring still works but indicates limited functionality
            # This would be implemented based on actual monitoring code structure
            pass


class TestImportGuardPatterns:
    """Test different import guard patterns work correctly."""

    def test_availability_flag_pattern(self):
        """Test the FEATURE_AVAILABLE flag pattern."""
        # Test pattern: try/except with boolean flag
        with patch.dict(sys.modules, {"rich": None}):
            code = """
try:
    from rich.console import Console
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None

def use_rich():
    if not RICH_AVAILABLE:
        raise ImportError("Rich not available")
    return Console()

print(f"RICH_AVAILABLE={RICH_AVAILABLE}")
try:
    use_rich()
    print("RICH_USED")
except ImportError as e:
    print(f"RICH_ERROR={e}")
            """

            result = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=False
            )

            assert result.returncode == 0
            assert "RICH_AVAILABLE=False" in result.stdout
            assert "RICH_ERROR=Rich not available" in result.stdout

    def test_graceful_degradation_pattern(self):
        """Test the graceful degradation pattern."""
        # Test pattern: fallback class/function
        with patch.dict(sys.modules, {"rich": None}):
            code = """
try:
    from rich.console import Console
    console = Console()
except ImportError:
    class PlainConsole:
        def print(self, text, **kwargs):
            print(f"PLAIN: {text}")
    console = PlainConsole()

console.print("test message")
            """

            result = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=False
            )

            assert result.returncode == 0
            assert "PLAIN: test message" in result.stdout

    def test_lazy_import_pattern(self):
        """Test the lazy import with error pattern."""
        # Test pattern: import inside function with error
        code = """
def api_function():
    try:
        from fastapi import FastAPI
        return "FASTAPI_AVAILABLE"
    except ImportError:
        raise ImportError("API functionality requires: pip install orb-py[api]")

try:
    result = api_function()
    print(result)
except ImportError as e:
    print(f"LAZY_ERROR={e}")
        """

        with patch.dict(sys.modules, {"fastapi": None}):
            result = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=False
            )

            assert result.returncode == 0
            assert "pip install orb-py[api]" in result.stdout


class TestFeatureDetection:
    """Test feature detection and capability reporting."""

    def test_feature_availability_detection(self):
        """Test that we can detect which features are available."""
        code = """
def detect_features():
    features = {}

    try:
        import rich
        features['cli_rich'] = True
    except ImportError:
        features['cli_rich'] = False

    try:
        import fastapi
        features['api'] = True
    except ImportError:
        features['api'] = False

    try:
        import prometheus_client
        features['monitoring'] = True
    except ImportError:
        features['monitoring'] = False

    return features

features = detect_features()
for feature, available in features.items():
    print(f"{feature}={available}")
        """

        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )

        assert result.returncode == 0
        # Should show which features are available in current environment
        assert "cli_rich=" in result.stdout
        assert "api=" in result.stdout
        assert "monitoring=" in result.stdout


class TestRealWorldScenarios:
    """Test real-world usage scenarios."""

    def test_minimal_install_cli_usage(self):
        """Test typical CLI usage with minimal install."""
        # This would test actual CLI commands work with minimal install
        # Implementation depends on CLI structure
        pass

    def test_api_install_server_startup(self):
        """Test API server can start with API install."""
        # This would test actual server startup with API dependencies
        # Implementation depends on server structure
        pass

    def test_mixed_usage_patterns(self):
        """Test mixed usage of different features."""
        # Test scenarios where some features are available and others aren't
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
