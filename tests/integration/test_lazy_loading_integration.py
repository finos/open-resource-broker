"""Integration tests for lazy loading functionality.

This module tests that lazy loading implementation maintains all existing
functionality while providing performance improvements.
"""

import asyncio
from unittest.mock import patch

import pytest

from src.application.queries.handlers import ListTemplatesQuery
from src.bootstrap import Application
from src.infrastructure.di.container import get_container


class TestLazyLoadingIntegration:
    """Test suite for lazy loading integration with existing functionality."""

    @pytest.fixture
    def app(self):
        """Create application instance for testing."""
        return Application()

    @pytest.fixture
    async def initialized_app(self, app):
        """Create and initialize application instance."""
        await app.initialize()
        return app

    def test_application_creation_is_fast(self, app):
        """Test that application creation is fast (lazy loading)."""
        # Application should be created quickly without heavy initialization
        assert app._container is None, "Container should not be created during __init__"
        assert (
            app._config_manager is None
        ), "Config manager should not be created during __init__"
        assert app.logger is not None, "Logger should be available immediately"

    async def test_application_initialization_works(self, app):
        """Test that application initialization works correctly."""
        result = await app.initialize()
        assert result is True, "Application initialization should succeed"
        assert app._initialized is True, "Application should be marked as initialized"

    async def test_lazy_container_creation(self, app):
        """Test that DI container is created lazily."""
        # Container should not exist initially
        assert app._container is None

        # Initialize app
        await app.initialize()

        # Container should be created during initialization
        assert app._container is not None
        assert app._container.is_lazy_loading_enabled()

    async def test_lazy_config_manager_creation(self, app):
        """Test that config manager is created lazily."""
        # Config manager should not exist initially
        assert app._config_manager is None

        # Initialize app
        await app.initialize()

        # Config manager should be created during initialization
        assert app._config_manager is not None

    async def test_query_bus_access(self, initialized_app):
        """Test that query bus can be accessed and cached."""
        # First access
        query_bus1 = initialized_app.get_query_bus()
        assert query_bus1 is not None

        # Second access should return same instance (cached)
        query_bus2 = initialized_app.get_query_bus()
        assert query_bus1 is query_bus2

    async def test_command_bus_access(self, initialized_app):
        """Test that command bus can be accessed and cached."""
        # First access
        command_bus1 = initialized_app.get_command_bus()
        assert command_bus1 is not None

        # Second access should return same instance (cached)
        command_bus2 = initialized_app.get_command_bus()
        assert command_bus1 is command_bus2

    async def test_provider_info_access(self, initialized_app):
        """Test that provider info can be accessed."""
        provider_info = initialized_app.get_provider_info()
        assert isinstance(provider_info, dict)
        assert "mode" in provider_info or "status" in provider_info

    async def test_health_check_works(self, initialized_app):
        """Test that health check functionality works."""
        health = initialized_app.health_check()
        assert isinstance(health, dict)
        assert "status" in health

    async def test_templates_query_execution(self, initialized_app):
        """Test that templates query can be executed (end-to-end test)."""
        query_bus = initialized_app.get_query_bus()

        # This should work without errors
        try:
            result = query_bus.execute(ListTemplatesQuery())
            # Result might be empty or contain templates, but should not error
            assert result is not None
        except Exception as e:
            # If it fails due to missing AWS credentials, that's expected in test environment
            if "credentials" not in str(e).lower() and "aws" not in str(e).lower():
                raise

    def test_lazy_loading_configuration(self):
        """Test that lazy loading can be configured."""
        container = get_container()
        assert (
            container.is_lazy_loading_enabled()
        ), "Lazy loading should be enabled by default"

    async def test_multiple_app_instances(self):
        """Test that multiple application instances work correctly."""
        app1 = Application()
        app2 = Application()

        await app1.initialize()
        await app2.initialize()

        # Both should be initialized successfully
        assert app1._initialized
        assert app2._initialized

        # They should have separate containers
        assert app1._container is not app2._container

    async def test_async_context_manager(self):
        """Test that async context manager works correctly."""
        async with Application() as app:
            assert app._initialized
            provider_info = app.get_provider_info()
            assert isinstance(provider_info, dict)

    def test_shutdown_functionality(self, app):
        """Test that shutdown functionality works."""
        app.shutdown()
        assert not app._initialized


class TestLazyLoadingErrorHandling:
    """Test error handling in lazy loading scenarios."""

    def test_uninitialized_app_access(self):
        """Test that accessing uninitialized app raises appropriate errors."""
        app = Application()

        with pytest.raises(RuntimeError, match="Application not initialized"):
            app.get_query_bus()

        with pytest.raises(RuntimeError, match="Application not initialized"):
            app.get_command_bus()

    def test_initialization_failure_handling(self):
        """Test handling of initialization failures."""
        app = Application()

        # Mock a failure in initialization
        with patch.object(
            app, "_ensure_config_manager", side_effect=Exception("Config error")
        ):
            result = asyncio.run(app.initialize())
            assert result is False, "Initialization should fail gracefully"

    async def test_lazy_component_failure_handling(self):
        """Test handling of lazy component creation failures."""
        app = Application()
        await app.initialize()

        # Mock a failure in lazy component creation
        with patch.object(
            app._container, "get", side_effect=Exception("Component error")
        ):
            with pytest.raises(Exception, match="Component error"):
                app.get_query_bus()


class TestLazyLoadingCompatibility:
    """Test compatibility with existing functionality."""

    async def test_all_cli_commands_compatibility(self):
        """Test that all CLI commands are compatible with lazy loading."""
        # This would ideally test all CLI commands, but we'll test a representative sample
        commands_to_test = [
            ["--help"],
            ["templates", "list"],
            ["requests", "list"],
        ]

        import subprocess
        import sys

        for cmd in commands_to_test:
            try:
                result = subprocess.run(
                    [sys.executable, "src/run.py"] + cmd,
                    capture_output=True,
                    text=True,
                    cwd=".",
                    timeout=30,
                )

                # Command should either succeed or fail gracefully (not crash)
                assert result.returncode in [
                    0,
                    1,
                ], f"Command {cmd} crashed with return code {result.returncode}"

            except subprocess.TimeoutExpired:
                pytest.fail(f"Command {cmd} timed out")

    def test_configuration_compatibility(self):
        """Test that configuration system is compatible with lazy loading."""
        # Test with different config paths
        app1 = Application()
        app2 = Application(config_path="config/default_config.json")

        # Both should create successfully
        assert app1.config_path is None
        assert app2.config_path == "config/default_config.json"

    async def test_provider_strategy_compatibility(self):
        """Test that provider strategy system works with lazy loading."""
        app = Application()
        await app.initialize()

        provider_info = app.get_provider_info()

        # Should have provider information
        assert isinstance(provider_info, dict)
        # Should not crash when accessing provider info


class TestLazyLoadingPerformanceIntegration:
    """Integration tests for performance aspects of lazy loading."""

    def test_startup_performance_integration(self):
        """Test startup performance in integration context."""
        import time

        start_time = time.time()
        Application()
        creation_time = (time.time() - start_time) * 1000

        # Creation should be very fast (lazy loading)
        assert (
            creation_time < 100
        ), f"App creation took {creation_time:.1f}ms, expected <100ms"

    async def test_first_access_performance_integration(self):
        """Test first access performance in integration context."""
        import time

        app = Application()
        await app.initialize()

        start_time = time.time()
        app.get_query_bus()
        first_access_time = (time.time() - start_time) * 1000

        # First access should be reasonable (triggers lazy loading)
        assert (
            first_access_time < 1000
        ), f"First access took {first_access_time:.1f}ms, expected <1000ms"

    async def test_cached_access_performance_integration(self):
        """Test cached access performance in integration context."""
        import time

        app = Application()
        await app.initialize()

        # First access (triggers lazy loading)
        app.get_query_bus()

        # Second access (should be cached)
        start_time = time.time()
        app.get_query_bus()
        cached_access_time = (time.time() - start_time) * 1000

        # Cached access should be very fast
        assert (
            cached_access_time < 10
        ), f"Cached access took {cached_access_time:.1f}ms, expected <10ms"


if __name__ == "__main__":
    # Run integration tests directly
    import sys

    print("🧪 Running Lazy Loading Integration Tests...")
    print("=" * 50)

    # Run a subset of tests that don't require pytest fixtures
    test_suite = TestLazyLoadingIntegration()

    try:
        app = Application()
        test_suite.test_application_creation_is_fast(app)
        print("✅ Application creation test passed")

        test_suite.test_lazy_loading_configuration()
        print("✅ Lazy loading configuration test passed")

        asyncio.run(test_suite.test_application_initialization_works(app))
        print("✅ Application initialization test passed")

        print("=" * 50)
        print("🎉 Integration tests completed successfully!")

    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        sys.exit(1)
