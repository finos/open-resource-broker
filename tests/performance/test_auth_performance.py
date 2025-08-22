"""Performance tests for authentication middleware."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api.server import create_fastapi_app
from config.schemas.server_schema import AuthConfig, ServerConfig
from infrastructure.auth.strategies import BearerTokenStrategy


class TestAuthenticationPerformance:
    """Test authentication performance and scalability."""

    @pytest.fixture
    def no_auth_client(self):
        """Client with no authentication."""
        server_config = ServerConfig(
            enabled=True, auth=AuthConfig(enabled=False, strategy="replace")
        )
        app = create_fastapi_app(server_config)
        return TestClient(app)

    @pytest.fixture
    def auth_client_and_token(self):
        """Client with authentication and valid token."""
        server_config = ServerConfig(
            enabled=True,
            auth=AuthConfig(
                enabled=True,
                strategy="bearer_token",
                bearer_token={
                    "secret_key": "performance-test-secret-key",
                    "algorithm": "HS256",
                },
            ),
        )
        app = create_fastapi_app(server_config)
        client = TestClient(app)

        # Create valid token
        strategy = BearerTokenStrategy(
            secret_key="performance-test-secret-key", algorithm="HS256", enabled=True
        )
        token = strategy._create_access_token(
            user_id="perf-test-user", roles=["user"], permissions=["read"]
        )

        return client, token

    def test_no_auth_baseline_performance(self, no_auth_client):
        """Baseline performance test without authentication."""
        num_requests = 100
        start_time = time.time()

        for _ in range(num_requests):
            response = no_auth_client.get("/health")
            assert response.status_code == 200

        end_time = time.time()
        total_time = end_time - start_time
        avg_time = total_time / num_requests

        print(f"No auth - {num_requests} requests in {total_time:.3f}s")
        print(f"Average response time: {avg_time * 1000:.2f}ms")

        # Performance assertion - should be very fast
        assert avg_time < 0.1, f"Average response time {avg_time:.3f}s too slow"

    def test_bearer_token_auth_performance(self, auth_client_and_token):
        """Performance test with Bearer token authentication."""
        client, token = auth_client_and_token
        headers = {"Authorization": f"Bearer {token}"}

        num_requests = 100
        start_time = time.time()

        for _ in range(num_requests):
            response = client.get("/info", headers=headers)
            assert response.status_code == 200

        end_time = time.time()
        total_time = end_time - start_time
        avg_time = total_time / num_requests

        print(f"Bearer token auth - {num_requests} requests in {total_time:.3f}s")
        print(f"Average response time: {avg_time * 1000:.2f}ms")

        # Performance assertion - should be reasonably fast
        assert avg_time < 0.2, f"Average response time {avg_time:.3f}s too slow"

    def test_concurrent_auth_requests(self, auth_client_and_token):
        """Test concurrent authenticated requests."""
        client, token = auth_client_and_token
        headers = {"Authorization": f"Bearer {token}"}

        def make_request():
            response = client.get("/info", headers=headers)
            return response.status_code == 200

        num_threads = 10
        requests_per_thread = 10

        start_time = time.time()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for _ in range(num_threads):
                for _ in range(requests_per_thread):
                    future = executor.submit(make_request)
                    futures.append(future)

            # Wait for all requests to complete
            results = [future.result() for future in futures]

        end_time = time.time()
        total_time = end_time - start_time
        total_requests = num_threads * requests_per_thread

        print(f"Concurrent auth - {total_requests} requests in {total_time:.3f}s")
        print(f"Requests per second: {total_requests / total_time:.1f}")

        # All requests should succeed
        assert all(results), "Some concurrent requests failed"

        # Should handle reasonable concurrency
        assert total_requests / total_time > 10, "Concurrent performance too low"

    def test_token_validation_performance(self):
        """Test JWT token validation performance."""
        strategy = BearerTokenStrategy(
            secret_key="performance-test-secret", algorithm="HS256", enabled=True
        )

        # Create test token
        token = strategy._create_access_token(
            user_id="perf-user",
            roles=["user", "admin"],
            permissions=["read", "write", "admin"],
        )

        num_validations = 1000
        start_time = time.time()

        async def validate_tokens():
            for _ in range(num_validations):
                result = await strategy.validate_token(token)
                assert result.is_authenticated

        asyncio.run(validate_tokens())

        end_time = time.time()
        total_time = end_time - start_time
        avg_time = total_time / num_validations

        print(f"Token validation - {num_validations} validations in {total_time:.3f}s")
        print(f"Average validation time: {avg_time * 1000:.2f}ms")

        # Token validation should be very fast
        assert avg_time < 0.01, f"Token validation too slow: {avg_time:.4f}s"

    def test_auth_middleware_overhead(self, no_auth_client, auth_client_and_token):
        """Test authentication middleware overhead."""
        auth_client, token = auth_client_and_token
        headers = {"Authorization": f"Bearer {token}"}

        num_requests = 50

        # Test no auth performance
        start_time = time.time()
        for _ in range(num_requests):
            response = no_auth_client.get("/health")
            assert response.status_code == 200
        no_auth_time = time.time() - start_time

        # Test with auth performance (excluded path)
        start_time = time.time()
        for _ in range(num_requests):
            response = auth_client.get("/health")  # Excluded path
            assert response.status_code == 200
        auth_excluded_time = time.time() - start_time

        # Test with auth performance (protected path)
        start_time = time.time()
        for _ in range(num_requests):
            response = auth_client.get("/info", headers=headers)
            assert response.status_code == 200
        auth_protected_time = time.time() - start_time

        print(f"No auth: {no_auth_time:.3f}s")
        print(f"Auth (excluded): {auth_excluded_time:.3f}s")
        print(f"Auth (protected): {auth_protected_time:.3f}s")

        # Calculate overhead
        excluded_overhead = (auth_excluded_time - no_auth_time) / no_auth_time * 100
        protected_overhead = (auth_protected_time - no_auth_time) / no_auth_time * 100

        print(f"Excluded path overhead: {excluded_overhead:.1f}%")
        print(f"Protected path overhead: {protected_overhead:.1f}%")

        # Overhead should be reasonable
        assert excluded_overhead < 50, f"Excluded path overhead too high: {excluded_overhead:.1f}%"
        assert protected_overhead < 200, (
            f"Protected path overhead too high: {protected_overhead:.1f}%"
        )

    def test_memory_usage_stability(self, auth_client_and_token):
        """Test memory usage stability under load."""
        import os

        import psutil

        client, token = auth_client_and_token
        headers = {"Authorization": f"Bearer {token}"}

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Make many requests
        num_requests = 500
        for i in range(num_requests):
            response = client.get("/info", headers=headers)
            assert response.status_code == 200

            # Check memory every 100 requests
            if i % 100 == 0:
                current_memory = process.memory_info().rss / 1024 / 1024
                print(f"Request {i}: Memory usage {current_memory:.1f}MB")

        final_memory = process.memory_info().rss / 1024 / 1024
        memory_increase = final_memory - initial_memory

        print(f"Initial memory: {initial_memory:.1f}MB")
        print(f"Final memory: {final_memory:.1f}MB")
        print(f"Memory increase: {memory_increase:.1f}MB")

        # Memory increase should be reasonable (allow for some growth)
        assert memory_increase < 50, f"Memory increase too high: {memory_increase:.1f}MB"
