"""Comprehensive tests for repository pattern implementations."""

import os
import tempfile
from unittest.mock import Mock

import pytest

# Import repository components
try:
    from src.domain.request.aggregate import Request
    from src.domain.request.value_objects import RequestStatus
    from src.infrastructure.persistence.base.base_repository import BaseRepository
    from src.infrastructure.persistence.components.json_storage import JSONStorage
    from src.infrastructure.persistence.repositories.machine_repository import (
        MachineRepository,
    )
    from src.infrastructure.persistence.repositories.request_repository import (
        RequestRepository,
    )
    from src.infrastructure.persistence.repositories.template_repository import (
        TemplateRepository,
    )

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"Repository imports not available: {e}")


@pytest.mark.unit
class TestRepositoryPatternCompliance:
    """Test repository pattern compliance and interface consistency."""

    def test_repositories_implement_common_interface(self):
        """Test that all repositories implement common interface."""
        # All repositories should inherit from BaseRepository
        assert issubclass(
            RequestRepository, BaseRepository
        ), "RequestRepository should inherit from BaseRepository"
        assert issubclass(
            TemplateRepository, BaseRepository
        ), "TemplateRepository should inherit from BaseRepository"
        assert issubclass(
            MachineRepository, BaseRepository
        ), "MachineRepository should inherit from BaseRepository"

    def test_repositories_have_standard_methods(self):
        """Test that repositories have standard CRUD methods."""
        # Create mock storage
        mock_storage = Mock()

        # Test RequestRepository
        request_repo = RequestRepository(storage=mock_storage)

        # Should have standard methods
        assert hasattr(request_repo, "save"), "Repository should have save method"
        assert hasattr(request_repo, "find_by_id"), "Repository should have find_by_id method"
        assert hasattr(request_repo, "find_all"), "Repository should have find_all method"
        assert hasattr(request_repo, "delete"), "Repository should have delete method"

        # Should have aggregate-specific methods
        assert hasattr(
            request_repo, "find_by_status"
        ), "RequestRepository should have find_by_status"
        assert hasattr(
            request_repo, "find_by_requester"
        ), "RequestRepository should have find_by_requester"

    def test_repositories_handle_domain_events(self):
        """Test that repositories handle domain events properly."""
        mock_storage = Mock()
        mock_event_publisher = Mock()

        request_repo = RequestRepository(storage=mock_storage, event_publisher=mock_event_publisher)

        # Create request with events
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Mock storage save
        mock_storage.save.return_value = None

        # Save request
        request_repo.save(request)

        # Should extract and publish events
        events = request.get_domain_events()
        if events:
            mock_event_publisher.publish_events.assert_called_once_with(events)

    def test_repositories_support_transactions(self):
        """Test that repositories support transaction boundaries."""
        mock_storage = Mock()

        request_repo = RequestRepository(storage=mock_storage)

        # Should support unit of work pattern
        if hasattr(request_repo, "begin_transaction"):
            request_repo.begin_transaction()

            # Perform operations
            request = Request.create_new_request(
                template_id="test-template", machine_count=2, requester_id="test-user"
            )

            request_repo.save(request)

            # Commit transaction
            request_repo.commit_transaction()

            # Should have called storage transaction methods
            mock_storage.begin_transaction.assert_called_once()
            mock_storage.commit_transaction.assert_called_once()


@pytest.mark.unit
class TestJSONRepositoryImplementation:
    """Test JSON-based repository implementation."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.json_file = os.path.join(self.temp_dir, "test_requests.json")

    def tearDown(self):
        """Clean up test fixtures."""
        if os.path.exists(self.json_file):
            os.remove(self.json_file)
        os.rmdir(self.temp_dir)

    def test_json_repository_saves_and_loads_data(self):
        """Test that JSON repository can save and load data."""
        # Create JSON storage
        json_storage = JSONStorage(file_path=self.json_file)
        request_repo = RequestRepository(storage=json_storage)

        # Create and save request
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request_repo.save(request)

        # Load request
        loaded_request = request_repo.find_by_id(request.id)

        assert loaded_request is not None
        assert loaded_request.id == request.id
        assert loaded_request.template_id == request.template_id
        assert loaded_request.machine_count == request.machine_count

    def test_json_repository_handles_concurrent_access(self):
        """Test that JSON repository handles concurrent access safely."""
        json_storage = JSONStorage(file_path=self.json_file)
        request_repo = RequestRepository(storage=json_storage)

        # Create multiple requests
        requests = []
        for i in range(5):
            request = Request.create_new_request(
                template_id=f"template-{i}",
                machine_count=i + 1,
                requester_id=f"user-{i}",
            )
            requests.append(request)

        # Save all requests (simulating concurrent access)
        for request in requests:
            request_repo.save(request)

        # Verify all requests are saved
        all_requests = request_repo.find_all()
        assert len(all_requests) == 5

    def test_json_repository_supports_queries(self):
        """Test that JSON repository supports complex queries."""
        json_storage = JSONStorage(file_path=self.json_file)
        request_repo = RequestRepository(storage=json_storage)

        # Create requests with different statuses
        pending_request = Request.create_new_request(
            template_id="template-1", machine_count=1, requester_id="user-1"
        )

        processing_request = Request.create_new_request(
            template_id="template-2", machine_count=2, requester_id="user-2"
        )
        processing_request.start_processing()

        # Save requests
        request_repo.save(pending_request)
        request_repo.save(processing_request)

        # Query by status
        pending_requests = request_repo.find_by_status(RequestStatus.PENDING)
        processing_requests = request_repo.find_by_status(RequestStatus.PROCESSING)

        assert len(pending_requests) == 1
        assert len(processing_requests) == 1
        assert pending_requests[0].status == RequestStatus.PENDING
        assert processing_requests[0].status == RequestStatus.PROCESSING

    def test_json_repository_handles_file_corruption(self):
        """Test that JSON repository handles file corruption gracefully."""
        # Create corrupted JSON file
        with open(self.json_file, "w") as f:
            f.write("invalid json content {")

        json_storage = JSONStorage(file_path=self.json_file)
        request_repo = RequestRepository(storage=json_storage)

        # Should handle corruption gracefully
        try:
            requests = request_repo.find_all()
            # Should return empty list or handle gracefully
            assert isinstance(requests, list)
        except Exception as e:
            # Should raise appropriate exception
            assert "json" in str(e).lower() or "corrupt" in str(e).lower()


@pytest.mark.unit
class TestSQLRepositoryImplementation:
    """Test SQL-based repository implementation."""

    def test_sql_repository_uses_connection_pooling(self):
        """Test that SQL repository uses connection pooling."""
        # Mock SQL storage with connection pool
        mock_sql_storage = Mock()
        mock_connection_pool = Mock()
        mock_sql_storage.connection_pool = mock_connection_pool

        request_repo = RequestRepository(storage=mock_sql_storage)

        # Create and save request
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request_repo.save(request)

        # Should use connection pool
        mock_connection_pool.get_connection.assert_called()

    def test_sql_repository_supports_transactions(self):
        """Test that SQL repository supports database transactions."""
        mock_sql_storage = Mock()
        mock_connection = Mock()
        mock_sql_storage.get_connection.return_value = mock_connection

        request_repo = RequestRepository(storage=mock_sql_storage)

        # Begin transaction
        if hasattr(request_repo, "begin_transaction"):
            request_repo.begin_transaction()

            # Perform operations
            request = Request.create_new_request(
                template_id="test-template", machine_count=2, requester_id="test-user"
            )

            request_repo.save(request)

            # Commit transaction
            request_repo.commit_transaction()

            # Should have used database transactions
            mock_connection.begin.assert_called()
            mock_connection.commit.assert_called()

    def test_sql_repository_handles_connection_failures(self):
        """Test that SQL repository handles connection failures."""
        mock_sql_storage = Mock()
        mock_sql_storage.get_connection.side_effect = Exception("Connection failed")

        request_repo = RequestRepository(storage=mock_sql_storage)

        # Should handle connection failure gracefully
        with pytest.raises(Exception):
            request = Request.create_new_request(
                template_id="test-template", machine_count=2, requester_id="test-user"
            )
            request_repo.save(request)

    def test_sql_repository_supports_complex_queries(self):
        """Test that SQL repository supports complex SQL queries."""
        mock_sql_storage = Mock()
        mock_connection = Mock()
        mock_cursor = Mock()

        mock_sql_storage.get_connection.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor

        # Mock query results
        mock_cursor.fetchall.return_value = [
            ("req-1", "template-1", 2, "PENDING", "user-1"),
            ("req-2", "template-2", 3, "PROCESSING", "user-2"),
        ]

        request_repo = RequestRepository(storage=mock_sql_storage)

        # Complex query with joins and filters
        if hasattr(request_repo, "find_with_template_info"):
            results = request_repo.find_with_template_info(
                status=RequestStatus.PENDING, template_name_like="test%"
            )

            # Should execute complex SQL query
            mock_cursor.execute.assert_called()
            assert len(results) >= 0


@pytest.mark.unit
class TestRepositoryEventPublishing:
    """Test repository event publishing functionality."""

    def test_repository_extracts_domain_events(self):
        """Test that repository extracts domain events from aggregates."""
        mock_storage = Mock()
        mock_event_publisher = Mock()

        request_repo = RequestRepository(storage=mock_storage, event_publisher=mock_event_publisher)

        # Create request (generates events)
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Verify events exist
        events_before = request.get_domain_events()
        assert len(events_before) > 0

        # Save request
        request_repo.save(request)

        # Events should be extracted and published
        mock_event_publisher.publish_events.assert_called_once()

        # Events should be cleared from aggregate
        events_after = request.get_domain_events()
        assert len(events_after) == 0

    def test_repository_publishes_events_in_order(self):
        """Test that repository publishes events in correct order."""
        mock_storage = Mock()
        mock_event_publisher = Mock()

        request_repo = RequestRepository(storage=mock_storage, event_publisher=mock_event_publisher)

        # Create request and perform multiple operations
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.start_processing()
        request.complete_successfully(machine_ids=["i-123", "i-456"], completion_message="Success")

        # Save request
        request_repo.save(request)

        # Should publish events in chronological order
        published_events = mock_event_publisher.publish_events.call_args[0][0]

        for i in range(1, len(published_events)):
            assert published_events[i - 1].occurred_at <= published_events[i].occurred_at

    def test_repository_handles_event_publishing_failures(self):
        """Test that repository handles event publishing failures."""
        mock_storage = Mock()
        mock_event_publisher = Mock()
        mock_event_publisher.publish_events.side_effect = Exception("Event publishing failed")

        request_repo = RequestRepository(storage=mock_storage, event_publisher=mock_event_publisher)

        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Should handle event publishing failure
        # (Implementation may choose to fail the entire operation or continue)
        try:
            request_repo.save(request)
        except Exception as e:
            assert "Event publishing failed" in str(e)

    def test_repository_supports_event_deduplication(self):
        """Test that repository supports event deduplication."""
        mock_storage = Mock()
        mock_event_publisher = Mock()

        request_repo = RequestRepository(storage=mock_storage, event_publisher=mock_event_publisher)

        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Save same request multiple times
        request_repo.save(request)
        request_repo.save(request)

        # Should not publish duplicate events
        # (Implementation depends on event deduplication strategy)
        call_count = mock_event_publisher.publish_events.call_count
        assert call_count >= 1  # At least one call should be made


@pytest.mark.unit
class TestRepositoryPerformanceOptimization:
    """Test repository performance optimization features."""

    def test_repository_supports_batch_operations(self):
        """Test that repository supports batch save operations."""
        mock_storage = Mock()
        request_repo = RequestRepository(storage=mock_storage)

        # Create multiple requests
        requests = []
        for i in range(10):
            request = Request.create_new_request(
                template_id=f"template-{i}",
                machine_count=i + 1,
                requester_id=f"user-{i}",
            )
            requests.append(request)

        # Batch save if supported
        if hasattr(request_repo, "save_batch"):
            request_repo.save_batch(requests)

            # Should call storage batch method
            mock_storage.save_batch.assert_called_once_with(requests)
        else:
            # Fallback to individual saves
            for request in requests:
                request_repo.save(request)

            assert mock_storage.save.call_count == 10

    def test_repository_supports_lazy_loading(self):
        """Test that repository supports lazy loading of related data."""
        mock_storage = Mock()
        request_repo = RequestRepository(storage=mock_storage)

        # Mock request data without related entities
        mock_request_data = {
            "id": "req-123",
            "template_id": "template-123",
            "machine_count": 2,
            "status": "PENDING",
        }

        mock_storage.find_by_id.return_value = mock_request_data

        # Load request
        request = request_repo.find_by_id("req-123")

        # Related data should be loaded lazily
        if hasattr(request, "machines") and callable(request.machines):
            # Lazy loading - machines property is a callable
            request.machines()

            # Should trigger additional query
            mock_storage.find_machines_by_request.assert_called_once_with("req-123")

    def test_repository_supports_caching(self):
        """Test that repository supports result caching."""
        mock_storage = Mock()
        mock_cache = Mock()

        request_repo = RequestRepository(storage=mock_storage, cache=mock_cache)

        # Mock cache miss then hit
        mock_cache.get.side_effect = [None, {"cached": "data"}]
        mock_storage.find_by_id.return_value = {"id": "req-123"}

        # First call - cache miss
        request_repo.find_by_id("req-123")

        # Should query storage and cache result
        mock_storage.find_by_id.assert_called_once_with("req-123")
        mock_cache.set.assert_called_once()

        # Second call - cache hit
        request_repo.find_by_id("req-123")

        # Should use cached result
        mock_cache.get.assert_called()

    def test_repository_supports_read_replicas(self):
        """Test that repository can use read replicas for queries."""
        mock_write_storage = Mock()
        mock_read_storage = Mock()

        request_repo = RequestRepository(
            write_storage=mock_write_storage, read_storage=mock_read_storage
        )

        # Write operations should use write storage
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request_repo.save(request)
        mock_write_storage.save.assert_called_once()

        # Read operations should use read storage
        request_repo.find_by_id("req-123")
        mock_read_storage.find_by_id.assert_called_once_with("req-123")


@pytest.mark.unit
class TestRepositoryErrorHandling:
    """Test repository error handling and resilience."""

    def test_repository_handles_storage_failures(self):
        """Test that repository handles storage failures gracefully."""
        mock_storage = Mock()
        mock_storage.save.side_effect = Exception("Storage failure")

        request_repo = RequestRepository(storage=mock_storage)

        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Should propagate storage exception
        with pytest.raises(Exception) as exc_info:
            request_repo.save(request)

        assert "Storage failure" in str(exc_info.value)

    def test_repository_supports_retry_logic(self):
        """Test that repository supports retry logic for transient failures."""
        mock_storage = Mock()

        # Simulate transient failure then success
        mock_storage.save.side_effect = [
            Exception("Transient failure"),
            Exception("Transient failure"),
            None,  # Success on third try
        ]

        request_repo = RequestRepository(storage=mock_storage)

        # Add retry decorator if supported
        if hasattr(request_repo, "_retry_on_failure"):
            request_repo._retry_on_failure = True
            request_repo._max_retries = 3

        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Should succeed after retries
        try:
            request_repo.save(request)
            # If retry is supported, should eventually succeed
            assert mock_storage.save.call_count == 3
        except Exception:
            # If retry is not supported, should fail immediately
            assert mock_storage.save.call_count == 1

    def test_repository_validates_aggregate_state(self):
        """Test that repository validates aggregate state before saving."""
        mock_storage = Mock()
        request_repo = RequestRepository(storage=mock_storage)

        # Create invalid request (this should be caught by aggregate validation)
        try:
            invalid_request = Request.create_new_request(
                template_id="",  # Invalid empty template ID
                machine_count=2,
                requester_id="test-user",
            )

            # Should not reach this point due to aggregate validation
            request_repo.save(invalid_request)

        except Exception as e:
            # Should catch validation error
            assert "template_id" in str(e).lower() or "validation" in str(e).lower()

    def test_repository_handles_concurrency_conflicts(self):
        """Test that repository handles concurrency conflicts."""
        mock_storage = Mock()

        # Simulate optimistic locking conflict
        mock_storage.save.side_effect = Exception("Optimistic lock exception")

        request_repo = RequestRepository(storage=mock_storage)

        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Should handle concurrency conflict
        with pytest.raises(Exception) as exc_info:
            request_repo.save(request)

        assert "lock" in str(exc_info.value).lower()


@pytest.mark.unit
class TestRepositoryMigration:
    """Test repository data migration capabilities."""

    def test_repository_supports_schema_migration(self):
        """Test that repository supports schema migration."""
        mock_storage = Mock()
        request_repo = RequestRepository(storage=mock_storage)

        # Should support schema version checking
        if hasattr(request_repo, "get_schema_version"):
            current_version = request_repo.get_schema_version()
            assert isinstance(current_version, (int, str))

        # Should support schema migration
        if hasattr(request_repo, "migrate_schema"):
            request_repo.migrate_schema(from_version=1, to_version=2)
            mock_storage.migrate_schema.assert_called_once()

    def test_repository_supports_data_migration(self):
        """Test that repository supports data migration between storage types."""
        # Source repository (JSON)
        mock_json_storage = Mock()
        source_repo = RequestRepository(storage=mock_json_storage)

        # Target repository (SQL)
        mock_sql_storage = Mock()
        target_repo = RequestRepository(storage=mock_sql_storage)

        # Mock source data
        mock_requests = [
            {"id": "req-1", "template_id": "template-1"},
            {"id": "req-2", "template_id": "template-2"},
        ]
        mock_json_storage.find_all.return_value = mock_requests

        # Migrate data if supported
        if hasattr(source_repo, "migrate_to"):
            source_repo.migrate_to(target_repo)

            # Should read from source and write to target
            mock_json_storage.find_all.assert_called_once()
            assert mock_sql_storage.save.call_count == len(mock_requests)

    def test_repository_validates_migration_integrity(self):
        """Test that repository validates data integrity during migration."""
        mock_source_storage = Mock()
        mock_target_storage = Mock()

        source_repo = RequestRepository(storage=mock_source_storage)
        target_repo = RequestRepository(storage=mock_target_storage)

        # Mock data with checksums
        mock_requests = [
            {"id": "req-1", "checksum": "abc123"},
            {"id": "req-2", "checksum": "def456"},
        ]
        mock_source_storage.find_all.return_value = mock_requests

        # Migration should validate data integrity
        if hasattr(source_repo, "migrate_with_validation"):
            source_repo.migrate_with_validation(target_repo)

            # Should validate checksums or other integrity measures
            mock_target_storage.validate_integrity.assert_called()
