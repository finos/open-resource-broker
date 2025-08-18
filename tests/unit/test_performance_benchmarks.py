"""Comprehensive performance and load testing."""

import gc
import os
import statistics
import threading
import time
from unittest.mock import Mock

import psutil
import pytest

# Import components for performance testing
try:
    from domain.request.aggregate import Request
    from domain.template.aggregate import Template
    from infrastructure.persistence.repositories.request_repository import (
        RequestRepository,
    )

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"Performance test imports not available: {e}")


@pytest.mark.performance
@pytest.mark.slow
class TestPerformanceBenchmarks:
    """Performance benchmark tests."""

    def test_request_creation_performance(self):
        """Test performance of request creation."""
        iterations = 1000
        times = []

        for i in range(iterations):
            start_time = time.perf_counter()

            request = Request.create_new_request(
                template_id=f"template-{i}", machine_count=2, requester_id=f"user-{i}"
            )

            end_time = time.perf_counter()
            times.append(end_time - start_time)

        # Performance assertions
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)

        # Should create requests quickly
        assert avg_time < 0.001, f"Average creation time {avg_time:.6f}s too slow"
        assert max_time < 0.01, f"Maximum creation time {max_time:.6f}s too slow"

        print(
            f"Request creation - Avg: {avg_time:.6f}s, Max: {max_time:.6f}s, Min: {min_time:.6f}s"
        )

    def test_template_loading_performance(self):
        """Test performance of template loading."""
        # Create mock templates
        templates = []
        for i in range(100):
            template = Template(
                template_id=f"template-{i}",
                name=f"Template {i}",
                provider_api="RunInstances",
                image_id=f"ami-{i:016x}",
                instance_type="t2.micro",
            )
            templates.append(template)

        # Test loading performance
        iterations = 100
        times = []

        for _i in range(iterations):
            start_time = time.perf_counter()

            # Simulate template loading operation
            loaded_templates = [t for t in templates if t.template_id.startswith("template-")]

            end_time = time.perf_counter()
            times.append(end_time - start_time)

        avg_time = statistics.mean(times)
        assert avg_time < 0.01, f"Template loading too slow: {avg_time:.6f}s"

        print(f"Template loading - Avg: {avg_time:.6f}s for {len(templates)} templates")

    def test_domain_event_generation_performance(self):
        """Test performance of domain event generation."""
        iterations = 500
        times = []

        for i in range(iterations):
            request = Request.create_new_request(
                template_id=f"template-{i}", machine_count=2, requester_id=f"user-{i}"
            )

            start_time = time.perf_counter()

            # Perform operations that generate events
            request.start_processing()
            request.complete_successfully(
                machine_ids=[f"i-{i:016x}1", f"i-{i:016x}2"],
                completion_message="Success",
            )

            # Get events
            events = request.get_domain_events()

            end_time = time.perf_counter()
            times.append(end_time - start_time)

        avg_time = statistics.mean(times)
        assert avg_time < 0.005, f"Event generation too slow: {avg_time:.6f}s"

        print(f"Event generation - Avg: {avg_time:.6f}s")

    def test_repository_save_performance(self):
        """Test repository save performance."""
        mock_storage = Mock()
        repository = RequestRepository(storage=mock_storage)

        iterations = 200
        times = []

        for i in range(iterations):
            request = Request.create_new_request(
                template_id=f"template-{i}", machine_count=2, requester_id=f"user-{i}"
            )

            start_time = time.perf_counter()
            repository.save(request)
            end_time = time.perf_counter()

            times.append(end_time - start_time)

        avg_time = statistics.mean(times)
        assert avg_time < 0.01, f"Repository save too slow: {avg_time:.6f}s"

        print(f"Repository save - Avg: {avg_time:.6f}s")


@pytest.mark.performance
@pytest.mark.slow
class TestMemoryPerformance:
    """Memory usage and performance tests."""

    def test_memory_usage_during_request_creation(self):
        """Test memory usage during request creation."""
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        requests = []
        iterations = 1000

        for i in range(iterations):
            request = Request.create_new_request(
                template_id=f"template-{i}", machine_count=2, requester_id=f"user-{i}"
            )
            requests.append(request)

            # Check memory every 100 iterations
            if i % 100 == 0:
                current_memory = process.memory_info().rss
                memory_increase = current_memory - initial_memory

                # Memory increase should be reasonable
                memory_per_request = memory_increase / (i + 1) if i > 0 else 0
                assert (
                    memory_per_request < 10000
                ), f"Memory per request too high: {memory_per_request} bytes"

        final_memory = process.memory_info().rss
        total_increase = final_memory - initial_memory
        memory_per_request = total_increase / iterations

        print(
            f"Memory usage - Total: {total_increase / 1024 / 1024:.2f} MB, Per request: {memory_per_request:.0f} bytes"
        )

        # Clean up
        requests.clear()
        gc.collect()

    def test_memory_leak_detection(self):
        """Test for memory leaks in repeated operations."""
        process = psutil.Process(os.getpid())

        memory_samples = []
        iterations = 5
        operations_per_iteration = 200

        for iteration in range(iterations):
            # Perform operations
            for i in range(operations_per_iteration):
                request = Request.create_new_request(
                    template_id=f"template-{i}",
                    machine_count=2,
                    requester_id=f"user-{i}",
                )

                request.start_processing()
                request.complete_successfully(
                    machine_ids=[f"i-{i:016x}"], completion_message="Success"
                )

                # Clear events to simulate normal operation
                request.clear_domain_events()

            # Force garbage collection
            gc.collect()

            # Sample memory
            current_memory = process.memory_info().rss
            memory_samples.append(current_memory)

            print(f"Iteration {iteration + 1}: {current_memory / 1024 / 1024:.2f} MB")

        # Check for memory leaks
        if len(memory_samples) > 2:
            # Memory should not continuously increase
            memory_trend = memory_samples[-1] - memory_samples[0]
            memory_per_iteration = memory_trend / len(memory_samples)

            # Allow some memory increase but not excessive
            assert (
                memory_per_iteration < 1024 * 1024
            ), f"Potential memory leak: {memory_per_iteration / 1024:.0f} KB per iteration"

    def test_garbage_collection_impact(self):
        """Test impact of garbage collection on performance."""
        # Create objects that will need garbage collection
        objects = []

        # Test performance without GC
        gc.disable()
        start_time = time.perf_counter()

        for i in range(1000):
            request = Request.create_new_request(
                template_id=f"template-{i}", machine_count=2, requester_id=f"user-{i}"
            )
            objects.append(request)

        time_without_gc = time.perf_counter() - start_time

        # Clear objects and re-enable GC
        objects.clear()
        gc.enable()
        gc.collect()

        # Test performance with GC
        start_time = time.perf_counter()

        for i in range(1000):
            request = Request.create_new_request(
                template_id=f"template-{i}", machine_count=2, requester_id=f"user-{i}"
            )
            objects.append(request)

        time_with_gc = time.perf_counter() - start_time

        # GC impact should be minimal
        gc_overhead = (time_with_gc - time_without_gc) / time_without_gc
        assert gc_overhead < 0.5, f"GC overhead too high: {gc_overhead:.2%}"

        print(
            f"GC impact - Without GC: {time_without_gc:.4f}s, With GC: {time_with_gc:.4f}s, Overhead: {gc_overhead:.2%}"
        )

        # Clean up
        objects.clear()
        gc.collect()


@pytest.mark.performance
@pytest.mark.slow
class TestConcurrentPerformance:
    """Concurrent operation performance tests."""

    def test_concurrent_request_creation_performance(self):
        """Test performance of concurrent request creation."""
        num_threads = 10
        requests_per_thread = 100
        results = []

        def create_requests(thread_id):
            thread_results = []
            for i in range(requests_per_thread):
                start_time = time.perf_counter()

                request = Request.create_new_request(
                    template_id=f"template-{thread_id}-{i}",
                    machine_count=2,
                    requester_id=f"user-{thread_id}-{i}",
                )

                end_time = time.perf_counter()
                thread_results.append(end_time - start_time)

            results.extend(thread_results)

        # Create and start threads
        threads = []
        overall_start = time.perf_counter()

        for thread_id in range(num_threads):
            thread = threading.Thread(target=create_requests, args=(thread_id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        overall_end = time.perf_counter()

        # Analyze results
        total_requests = num_threads * requests_per_thread
        total_time = overall_end - overall_start
        avg_time_per_request = statistics.mean(results)
        throughput = total_requests / total_time

        # Performance assertions
        assert (
            avg_time_per_request < 0.01
        ), f"Concurrent creation too slow: {avg_time_per_request:.6f}s"
        assert throughput > 100, f"Throughput too low: {throughput:.0f} requests/second"

        print(f"Concurrent creation - {total_requests} requests in {total_time:.2f}s")
        print(f"Throughput: {throughput:.0f} requests/second")
        print(f"Average time per request: {avg_time_per_request:.6f}s")

    def test_repository_concurrent_access_performance(self):
        """Test repository performance under concurrent access."""
        mock_storage = Mock()
        repository = RequestRepository(storage=mock_storage)

        num_threads = 5
        operations_per_thread = 50
        results = []

        def repository_operations(thread_id):
            thread_results = []
            for i in range(operations_per_thread):
                request = Request.create_new_request(
                    template_id=f"template-{thread_id}-{i}",
                    machine_count=2,
                    requester_id=f"user-{thread_id}-{i}",
                )

                start_time = time.perf_counter()
                repository.save(request)
                end_time = time.perf_counter()

                thread_results.append(end_time - start_time)

            results.extend(thread_results)

        # Run concurrent operations
        threads = []
        overall_start = time.perf_counter()

        for thread_id in range(num_threads):
            thread = threading.Thread(target=repository_operations, args=(thread_id,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        overall_end = time.perf_counter()

        # Analyze performance
        total_operations = num_threads * operations_per_thread
        total_time = overall_end - overall_start
        avg_time = statistics.mean(results)
        throughput = total_operations / total_time

        assert avg_time < 0.05, f"Concurrent repository access too slow: {avg_time:.6f}s"

        print(f"Repository concurrent access - {total_operations} operations in {total_time:.2f}s")
        print(f"Throughput: {throughput:.0f} operations/second")

    def test_thread_safety_performance_impact(self):
        """Test performance impact of thread safety mechanisms."""
        # Test without thread safety (single thread)
        single_thread_times = []

        for i in range(100):
            start_time = time.perf_counter()

            request = Request.create_new_request(
                template_id=f"template-{i}", machine_count=2, requester_id=f"user-{i}"
            )
            request.start_processing()

            end_time = time.perf_counter()
            single_thread_times.append(end_time - start_time)

        single_thread_avg = statistics.mean(single_thread_times)

        # Test with potential thread safety overhead (multiple threads)
        multi_thread_times = []
        results_lock = threading.Lock()

        def thread_operations():
            for i in range(20):
                start_time = time.perf_counter()

                request = Request.create_new_request(
                    template_id=f"template-{threading.current_thread().ident}-{i}",
                    machine_count=2,
                    requester_id=f"user-{threading.current_thread().ident}-{i}",
                )
                request.start_processing()

                end_time = time.perf_counter()

                with results_lock:
                    multi_thread_times.append(end_time - start_time)

        # Run multiple threads
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=thread_operations)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        multi_thread_avg = statistics.mean(multi_thread_times)

        # Thread safety overhead should be minimal
        overhead = (multi_thread_avg - single_thread_avg) / single_thread_avg
        assert overhead < 1.0, f"Thread safety overhead too high: {overhead:.2%}"

        print(
            f"Thread safety impact - Single: {single_thread_avg:.6f}s, Multi: {multi_thread_avg:.6f}s, Overhead: {overhead:.2%}"
        )


@pytest.mark.performance
@pytest.mark.slow
class TestScalabilityLimits:
    """Test scalability limits and breaking points."""

    def test_maximum_concurrent_requests(self):
        """Test maximum number of concurrent requests."""
        max_threads = 50
        successful_threads = 0
        failed_threads = 0

        def create_request_thread():
            nonlocal successful_threads, failed_threads
            try:
                request = Request.create_new_request(
                    template_id=f"template-{threading.current_thread().ident}",
                    machine_count=1,
                    requester_id=f"user-{threading.current_thread().ident}",
                )
                successful_threads += 1
            except Exception:
                failed_threads += 1

        # Create maximum threads
        threads = []
        for _i in range(max_threads):
            thread = threading.Thread(target=create_request_thread)
            threads.append(thread)

        # Start all threads
        start_time = time.perf_counter()
        for thread in threads:
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        end_time = time.perf_counter()

        # Analyze results
        success_rate = successful_threads / max_threads
        total_time = end_time - start_time

        assert success_rate > 0.9, f"Success rate too low: {success_rate:.2%}"
        assert total_time < 10.0, f"Total time too high: {total_time:.2f}s"

        print(
            f"Concurrent requests - {successful_threads}/{max_threads} successful in {total_time:.2f}s"
        )

    def test_large_dataset_handling(self):
        """Test handling of large datasets."""
        large_dataset_sizes = [100, 500, 1000, 2000]
        performance_results = {}

        for size in large_dataset_sizes:
            start_time = time.perf_counter()

            # Create large dataset
            requests = []
            for i in range(size):
                request = Request.create_new_request(
                    template_id=f"template-{i}",
                    machine_count=1,
                    requester_id=f"user-{i}",
                    metadata={"index": i, "data": f"data-{i}" * 10},  # Some metadata
                )
                requests.append(request)

            # Perform operations on dataset
            for request in requests:
                request.start_processing()

            end_time = time.perf_counter()
            total_time = end_time - start_time
            time_per_item = total_time / size

            performance_results[size] = {
                "total_time": total_time,
                "time_per_item": time_per_item,
            }

            # Performance should scale reasonably
            assert (
                time_per_item < 0.01
            ), f"Time per item too high for {size} items: {time_per_item:.6f}s"

            print(f"Dataset size {size}: {total_time:.2f}s total, {time_per_item:.6f}s per item")

            # Clean up
            requests.clear()
            gc.collect()

        # Check scalability
        small_time_per_item = performance_results[100]["time_per_item"]
        large_time_per_item = performance_results[2000]["time_per_item"]
        scalability_factor = large_time_per_item / small_time_per_item

        # Should scale reasonably (not exponentially)
        assert (
            scalability_factor < 5.0
        ), f"Poor scalability: {scalability_factor:.2f}x slower for 20x data"

    def test_memory_scalability_limits(self):
        """Test memory usage scalability limits."""
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        dataset_sizes = [100, 500, 1000]
        memory_usage = {}

        for size in dataset_sizes:
            # Create dataset
            requests = []
            for i in range(size):
                request = Request.create_new_request(
                    template_id=f"template-{i}",
                    machine_count=2,
                    requester_id=f"user-{i}",
                )
                requests.append(request)

            # Measure memory
            current_memory = process.memory_info().rss
            memory_increase = current_memory - initial_memory
            memory_per_item = memory_increase / size

            memory_usage[size] = {
                "total_memory": memory_increase,
                "memory_per_item": memory_per_item,
            }

            print(
                f"Memory for {size} items: {memory_increase / 1024 / 1024:.2f} MB, {memory_per_item:.0f} bytes per item"
            )

            # Clean up
            requests.clear()
            gc.collect()

        # Memory usage should be reasonable and scale linearly
        memory_per_item_100 = memory_usage[100]["memory_per_item"]
        memory_per_item_1000 = memory_usage[1000]["memory_per_item"]

        # Memory per item should not increase dramatically with scale
        memory_scaling = memory_per_item_1000 / memory_per_item_100
        assert memory_scaling < 2.0, f"Memory usage scales poorly: {memory_scaling:.2f}x"


@pytest.mark.performance
@pytest.mark.benchmark
class TestPerformanceRegression:
    """Performance regression tests."""

    def test_performance_baseline(self):
        """Establish performance baseline for regression testing."""
        # This test establishes baseline performance metrics
        # In a real scenario, these would be compared against historical data

        operations = [
            (
                "request_creation",
                lambda: Request.create_new_request("template-1", 1, "user-1"),
            ),
            ("status_transition", lambda: self._test_status_transition()),
            ("event_generation", lambda: self._test_event_generation()),
        ]

        baselines = {}

        for operation_name, operation_func in operations:
            times = []

            for _ in range(100):
                start_time = time.perf_counter()
                operation_func()
                end_time = time.perf_counter()
                times.append(end_time - start_time)

            avg_time = statistics.mean(times)
            p95_time = statistics.quantiles(times, n=20)[18]  # 95th percentile

            baselines[operation_name] = {"avg_time": avg_time, "p95_time": p95_time}

            print(f"{operation_name} - Avg: {avg_time:.6f}s, P95: {p95_time:.6f}s")

        # Store baselines for future comparison
        # In practice, these would be stored in a file or database
        assert all(baseline["avg_time"] < 0.01 for baseline in baselines.values())

    def _test_status_transition(self):
        """Helper method for status transition test."""
        request = Request.create_new_request("template-1", 1, "user-1")
        request.start_processing()
        request.complete_successfully(["i-123"], "Success")
        return request

    def _test_event_generation(self):
        """Helper method for event generation test."""
        request = Request.create_new_request("template-1", 1, "user-1")
        request.start_processing()
        events = request.get_domain_events()
        return events
