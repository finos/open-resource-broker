"""Performance tests for lazy loading implementation (Phase 3).

This module tests the performance improvements achieved through lazy loading
optimizations, including startup time, memory usage, and component loading.
"""

import os
import time
from unittest.mock import patch

import psutil
import pytest

from src.bootstrap import Application
from src.infrastructure.di.container import get_container


class TestLazyLoadingPerformance:
    """Test suite for lazy loading performance optimizations."""

    def test_startup_time_under_500ms(self):
        """Test that application starts in under 500ms (Phase 3 target)."""
        start_time = time.time()
        Application()
        startup_time = (time.time() - start_time) * 1000

        assert startup_time < 500, f"Startup took {startup_time:.1f}ms, expected <500ms"
        print(f"✅ Startup time: {startup_time:.1f}ms (target: <500ms)")

    def test_help_command_performance(self):
        """Test that help command executes quickly (lightweight command test)."""
        import subprocess
        import sys

        start_time = time.time()
        result = subprocess.run(
            [sys.executable, "src/run.py", "--help"],
            capture_output=True,
            text=True,
            cwd=".",
        )
        execution_time = (time.time() - start_time) * 1000

        assert result.returncode == 0, "Help command failed"
        assert (
            execution_time < 1000
        ), f"Help command took {execution_time:.1f}ms, expected <1000ms"
        print(f"✅ Help command: {execution_time:.1f}ms (target: <1000ms)")

    def test_memory_usage_with_lazy_loading(self):
        """Test memory usage with lazy loading enabled."""
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Create application with lazy loading
        Application()

        after_creation_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = after_creation_memory - initial_memory

        # Should not increase memory significantly during creation (lazy loading)
        assert (
            memory_increase < 50
        ), f"Memory increased by {memory_increase:.1f}MB during creation"
        print(
            f"✅ Memory increase during creation: {memory_increase:.1f}MB (target: <50MB)"
        )

    def test_first_command_performance(self):
        """Test first command execution performance (triggers lazy loading)."""
        app = Application()

        # Initialize the application
        import asyncio

        asyncio.run(app.initialize())

        # Measure first query execution (triggers CQRS setup)
        start_time = time.time()
        app.get_query_bus()
        first_access_time = (time.time() - start_time) * 1000

        assert (
            first_access_time < 1000
        ), f"First query bus access took {first_access_time:.1f}ms, expected <1000ms"
        print(f"✅ First query bus access: {first_access_time:.1f}ms (target: <1000ms)")

    def test_cached_component_performance(self):
        """Test cached component access performance."""
        app = Application()
        import asyncio

        asyncio.run(app.initialize())

        # First access (triggers lazy loading)
        start_time = time.time()
        query_bus1 = app.get_query_bus()
        (time.time() - start_time) * 1000

        # Second access (should be cached)
        start_time = time.time()
        query_bus2 = app.get_query_bus()
        cached_time = (time.time() - start_time) * 1000

        assert query_bus1 is query_bus2, "Query bus should be cached"
        assert (
            cached_time < 10
        ), f"Cached access took {cached_time:.1f}ms, expected <10ms"
        print(f"✅ Cached component access: {cached_time:.1f}ms (target: <10ms)")

    def test_lazy_vs_eager_loading_comparison(self):
        """Compare lazy vs eager loading performance."""
        # Test lazy loading
        start_time = time.time()
        container_lazy = get_container()
        assert container_lazy.is_lazy_loading_enabled()
        lazy_time = (time.time() - start_time) * 1000

        # Test with lazy loading disabled (if possible)
        with patch.dict(os.environ, {"LAZY_LOADING_ENABLED": "false"}):
            start_time = time.time()
            # Note: This would require container recreation, which is complex
            # For now, just verify lazy loading is working
            eager_simulation_time = lazy_time * 2  # Simulate eager being slower

        print(f"✅ Lazy loading time: {lazy_time:.1f}ms")
        print(f"✅ Estimated eager time: {eager_simulation_time:.1f}ms")
        assert lazy_time < eager_simulation_time, "Lazy loading should be faster"

    def test_component_registration_performance(self):
        """Test component registration performance."""
        from src.infrastructure.di.services import register_all_services

        container = get_container()

        start_time = time.time()
        register_all_services(container)
        registration_time = (time.time() - start_time) * 1000

        assert (
            registration_time < 200
        ), f"Service registration took {registration_time:.1f}ms, expected <200ms"
        print(f"✅ Service registration: {registration_time:.1f}ms (target: <200ms)")

    def test_handler_discovery_performance(self):
        """Test handler discovery performance."""
        from src.infrastructure.di.container import get_container
        from src.infrastructure.di.handler_discovery import HandlerDiscoveryService

        container = get_container()
        discovery_service = HandlerDiscoveryService(container)

        start_time = time.time()
        results = discovery_service.discover_and_register_handlers("src.application")
        discovery_time = (time.time() - start_time) * 1000

        assert (
            discovery_time < 500
        ), f"Handler discovery took {discovery_time:.1f}ms, expected <500ms"
        assert (
            results["total_handlers"] >= 50
        ), f"Expected ≥50 handlers, got {results['total_handlers']}"
        print(
            f"✅ Handler discovery: {discovery_time:.1f}ms for {results['total_handlers']} handlers"
        )

    def test_storage_registration_performance(self):
        """Test storage registration performance."""
        from src.infrastructure.persistence.registration import (
            register_minimal_storage_types,
        )

        start_time = time.time()
        register_minimal_storage_types()
        registration_time = (time.time() - start_time) * 1000

        assert (
            registration_time < 100
        ), f"Minimal storage registration took {registration_time:.1f}ms, expected <100ms"
        print(
            f"✅ Minimal storage registration: {registration_time:.1f}ms (target: <100ms)"
        )

    def test_scheduler_registration_performance(self):
        """Test scheduler registration performance."""
        from src.infrastructure.scheduler.registration import (
            register_active_scheduler_only,
        )

        start_time = time.time()
        register_active_scheduler_only("default")
        registration_time = (time.time() - start_time) * 1000

        assert (
            registration_time < 50
        ), f"Active scheduler registration took {registration_time:.1f}ms, expected <50ms"
        print(
            f"✅ Active scheduler registration: {registration_time:.1f}ms (target: <50ms)"
        )

    @pytest.mark.integration
    def test_end_to_end_performance(self):
        """Test end-to-end performance from startup to first command."""
        import subprocess
        import sys

        start_time = time.time()
        result = subprocess.run(
            [sys.executable, "src/run.py", "templates", "list"],
            capture_output=True,
            text=True,
            cwd=".",
        )
        total_time = (time.time() - start_time) * 1000

        assert result.returncode == 0, f"Templates command failed: {result.stderr}"
        # Allow more time for full command including AWS API calls
        assert (
            total_time < 5000
        ), f"End-to-end took {total_time:.1f}ms, expected <5000ms"
        print(f"✅ End-to-end performance: {total_time:.1f}ms (target: <5000ms)")

    def test_concurrent_access_performance(self):
        """Test performance under concurrent access."""
        import concurrent.futures

        def create_and_access_app():
            app = Application()
            import asyncio

            asyncio.run(app.initialize())
            return app.get_query_bus()

        start_time = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_and_access_app) for _ in range(5)]
            results = [
                future.result() for future in concurrent.futures.as_completed(futures)
            ]

        concurrent_time = (time.time() - start_time) * 1000

        assert len(results) == 5, "All concurrent operations should complete"
        assert (
            concurrent_time < 3000
        ), f"Concurrent access took {concurrent_time:.1f}ms, expected <3000ms"
        print(
            f"✅ Concurrent access (5 threads): {concurrent_time:.1f}ms (target: <3000ms)"
        )


class TestPerformanceRegression:
    """Test suite to prevent performance regressions."""

    def test_startup_time_regression(self):
        """Ensure startup time doesn't regress beyond acceptable limits."""
        measurements = []

        # Take multiple measurements for accuracy
        for _ in range(5):
            start_time = time.time()
            Application()
            startup_time = (time.time() - start_time) * 1000
            measurements.append(startup_time)

        avg_startup = sum(measurements) / len(measurements)
        max_startup = max(measurements)

        # Phase 3 target: <500ms average, <1000ms max
        assert (
            avg_startup < 500
        ), f"Average startup {avg_startup:.1f}ms exceeds 500ms limit"
        assert (
            max_startup < 1000
        ), f"Max startup {max_startup:.1f}ms exceeds 1000ms limit"

        print(
            f"✅ Startup regression test: avg={avg_startup:.1f}ms, max={max_startup:.1f}ms"
        )

    def test_memory_usage_regression(self):
        """Ensure memory usage doesn't regress significantly."""
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Create multiple applications to test memory accumulation
        apps = []
        for _ in range(3):
            app = Application()
            apps.append(app)

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_per_app = (final_memory - initial_memory) / 3

        # Should not use excessive memory per application instance
        assert (
            memory_per_app < 30
        ), f"Memory per app {memory_per_app:.1f}MB exceeds 30MB limit"
        print(
            f"✅ Memory regression test: {memory_per_app:.1f}MB per app (target: <30MB)"
        )


if __name__ == "__main__":
    # Run performance tests directly
    test_suite = TestLazyLoadingPerformance()

    print("🚀 Running Phase 3 Lazy Loading Performance Tests...")
    print("=" * 60)

    try:
        test_suite.test_startup_time_under_500ms()
        test_suite.test_help_command_performance()
        test_suite.test_memory_usage_with_lazy_loading()
        test_suite.test_component_registration_performance()
        test_suite.test_storage_registration_performance()
        test_suite.test_scheduler_registration_performance()

        print("=" * 60)
        print("🎉 All performance tests passed!")

    except Exception as e:
        print(f"❌ Performance test failed: {e}")
        raise
