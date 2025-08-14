"""Unit tests for CQRSHandlerRegistry component."""

import threading

from src.infrastructure.di.components.cqrs_registry import CQRSHandlerRegistry


class TestCQRSHandlerRegistry:
    """Test cases for CQRSHandlerRegistry."""

    def setup_method(self):
        """Set up test fixtures."""
        self.registry = CQRSHandlerRegistry()

    def test_initialization(self):
        """Test registry initialization."""
        assert self.registry is not None
        stats = self.registry.get_stats()
        assert stats["command_handlers"] == 0
        assert stats["query_handlers"] == 0
        assert stats["event_types"] == 0
        assert stats["total_event_handlers"] == 0

    def test_register_command_handler(self):
        """Test registering a command handler."""

        class TestCommand:
            pass

        class TestCommandHandler:
            def handle(self, command: TestCommand):
                return "handled"

        self.registry.register_command_handler(TestCommand, TestCommandHandler)

        assert self.registry.has_command_handler(TestCommand)
        handler_type = self.registry.get_command_handler_type(TestCommand)
        assert handler_type == TestCommandHandler

    def test_register_query_handler(self):
        """Test registering a query handler."""

        class TestQuery:
            pass

        class TestQueryHandler:
            def handle(self, query: TestQuery):
                return "result"

        self.registry.register_query_handler(TestQuery, TestQueryHandler)

        assert self.registry.has_query_handler(TestQuery)
        handler_type = self.registry.get_query_handler_type(TestQuery)
        assert handler_type == TestQueryHandler

    def test_register_event_handler(self):
        """Test registering an event handler."""

        class TestEvent:
            pass

        class TestEventHandler:
            def handle(self, event: TestEvent):
                pass

        self.registry.register_event_handler(TestEvent, TestEventHandler)

        assert self.registry.has_event_handlers(TestEvent)
        handler_types = self.registry.get_event_handler_types(TestEvent)
        assert len(handler_types) == 1
        assert handler_types[0] == TestEventHandler

    def test_register_multiple_event_handlers(self):
        """Test registering multiple handlers for the same event."""

        class TestEvent:
            pass

        class TestEventHandler1:
            def handle(self, event: TestEvent):
                pass

        class TestEventHandler2:
            def handle(self, event: TestEvent):
                pass

        class TestEventHandler3:
            def handle(self, event: TestEvent):
                pass

        self.registry.register_event_handler(TestEvent, TestEventHandler1)
        self.registry.register_event_handler(TestEvent, TestEventHandler2)
        self.registry.register_event_handler(TestEvent, TestEventHandler3)

        assert self.registry.has_event_handlers(TestEvent)
        handler_types = self.registry.get_event_handler_types(TestEvent)
        assert len(handler_types) == 3
        assert TestEventHandler1 in handler_types
        assert TestEventHandler2 in handler_types
        assert TestEventHandler3 in handler_types

    def test_register_duplicate_event_handler(self):
        """Test that registering the same event handler twice doesn't create duplicates."""

        class TestEvent:
            pass

        class TestEventHandler:
            def handle(self, event: TestEvent):
                pass

        self.registry.register_event_handler(TestEvent, TestEventHandler)
        self.registry.register_event_handler(TestEvent, TestEventHandler)

        handler_types = self.registry.get_event_handler_types(TestEvent)
        assert len(handler_types) == 1
        assert handler_types[0] == TestEventHandler

    def test_get_nonexistent_command_handler(self):
        """Test getting handler for non-existent command."""

        class NonExistentCommand:
            pass

        assert not self.registry.has_command_handler(NonExistentCommand)
        handler_type = self.registry.get_command_handler_type(NonExistentCommand)
        assert handler_type is None

    def test_get_nonexistent_query_handler(self):
        """Test getting handler for non-existent query."""

        class NonExistentQuery:
            pass

        assert not self.registry.has_query_handler(NonExistentQuery)
        handler_type = self.registry.get_query_handler_type(NonExistentQuery)
        assert handler_type is None

    def test_get_nonexistent_event_handlers(self):
        """Test getting handlers for non-existent event."""

        class NonExistentEvent:
            pass

        assert not self.registry.has_event_handlers(NonExistentEvent)
        handler_types = self.registry.get_event_handler_types(NonExistentEvent)
        assert len(handler_types) == 0

    def test_clear_registry(self):
        """Test clearing all registrations."""

        class TestCommand:
            pass

        class TestQuery:
            pass

        class TestEvent:
            pass

        class TestCommandHandler:
            pass

        class TestQueryHandler:
            pass

        class TestEventHandler:
            pass

        # Register handlers
        self.registry.register_command_handler(TestCommand, TestCommandHandler)
        self.registry.register_query_handler(TestQuery, TestQueryHandler)
        self.registry.register_event_handler(TestEvent, TestEventHandler)

        # Verify registrations
        assert self.registry.has_command_handler(TestCommand)
        assert self.registry.has_query_handler(TestQuery)
        assert self.registry.has_event_handlers(TestEvent)

        # Clear registry
        self.registry.clear()

        # Verify all cleared
        assert not self.registry.has_command_handler(TestCommand)
        assert not self.registry.has_query_handler(TestQuery)
        assert not self.registry.has_event_handlers(TestEvent)

        stats = self.registry.get_stats()
        assert stats["command_handlers"] == 0
        assert stats["query_handlers"] == 0
        assert stats["event_types"] == 0
        assert stats["total_event_handlers"] == 0

    def test_get_stats(self):
        """Test getting registry statistics."""

        class TestCommand1:
            pass

        class TestCommand2:
            pass

        class TestQuery1:
            pass

        class TestEvent1:
            pass

        class TestEvent2:
            pass

        class TestCommandHandler1:
            pass

        class TestCommandHandler2:
            pass

        class TestQueryHandler1:
            pass

        class TestEventHandler1:
            pass

        class TestEventHandler2:
            pass

        class TestEventHandler3:
            pass

        # Register various handlers
        self.registry.register_command_handler(TestCommand1, TestCommandHandler1)
        self.registry.register_command_handler(TestCommand2, TestCommandHandler2)
        self.registry.register_query_handler(TestQuery1, TestQueryHandler1)
        self.registry.register_event_handler(TestEvent1, TestEventHandler1)
        self.registry.register_event_handler(
            TestEvent1, TestEventHandler2
        )  # Multiple handlers for same event
        self.registry.register_event_handler(TestEvent2, TestEventHandler3)

        stats = self.registry.get_stats()

        assert stats["command_handlers"] == 2
        assert stats["query_handlers"] == 1
        assert stats["event_types"] == 2
        assert stats["total_event_handlers"] == 3

    def test_thread_safety(self):
        """Test thread safety of registry operations."""

        class TestCommand:
            pass

        class TestQuery:
            pass

        class TestEvent:
            pass

        results = []
        errors = []

        def register_handlers(thread_id: int):
            try:
                # Create thread-specific handler classes
                command_handler = type(f"CommandHandler_{thread_id}", (), {})
                query_handler = type(f"QueryHandler_{thread_id}", (), {})
                event_handler = type(f"EventHandler_{thread_id}", (), {})

                # Register handlers
                self.registry.register_command_handler(
                    f"Command_{thread_id}", command_handler)
                self.registry.register_query_handler(
                    f"Query_{thread_id}", query_handler)
                self.registry.register_event_handler(
                    f"Event_{thread_id}", event_handler)

                # Verify registrations
                if (
                    self.registry.has_command_handler(f"Command_{thread_id}")
                    and self.registry.has_query_handler(f"Query_{thread_id}")
                    and self.registry.has_event_handlers(f"Event_{thread_id}")
                ):
                    results.append(thread_id)
                else:
                    errors.append(
                        f"Thread {thread_id}: Registration verification failed")

            except Exception as e:
                errors.append(f"Thread {thread_id}: {str(e)}")

        # Create multiple threads
        threads = []
        for i in range(10):
            thread = threading.Thread(target=register_handlers, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 10
        assert sorted(results) == list(range(10))

    def test_handler_overwrite(self):
        """Test that registering a new handler for the same command/query overwrites the previous one."""

        class TestCommand:
            pass

        class TestCommandHandler1:
            pass

        class TestCommandHandler2:
            pass

        # First registration
        self.registry.register_command_handler(TestCommand, TestCommandHandler1)
        handler_type = self.registry.get_command_handler_type(TestCommand)
        assert handler_type == TestCommandHandler1

        # Second registration should overwrite
        self.registry.register_command_handler(TestCommand, TestCommandHandler2)
        handler_type = self.registry.get_command_handler_type(TestCommand)
        assert handler_type == TestCommandHandler2
        assert handler_type != TestCommandHandler1

    def test_event_handler_list_isolation(self):
        """Test that event handler lists are properly isolated between different events."""

        class TestEvent1:
            pass

        class TestEvent2:
            pass

        class TestEventHandler1:
            pass

        class TestEventHandler2:
            pass

        self.registry.register_event_handler(TestEvent1, TestEventHandler1)
        self.registry.register_event_handler(TestEvent2, TestEventHandler2)

        handlers1 = self.registry.get_event_handler_types(TestEvent1)
        handlers2 = self.registry.get_event_handler_types(TestEvent2)

        assert len(handlers1) == 1
        assert len(handlers2) == 1
        assert handlers1[0] == TestEventHandler1
        assert handlers2[0] == TestEventHandler2

        # Modifying one list shouldn't affect the other
        handlers1.append("should_not_affect_registry")
        handlers2_after = self.registry.get_event_handler_types(TestEvent2)
        assert len(handlers2_after) == 1
        assert handlers2_after[0] == TestEventHandler2


class TestCQRSHandlerRegistryEdgeCases:
    """Test edge cases and error conditions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.registry = CQRSHandlerRegistry()

    def test_register_with_none_types(self):
        """Test behavior when registering with None types."""
        # This should not crash but also shouldn't create valid registrations
        try:
            self.registry.register_command_handler(None, None)
            self.registry.register_query_handler(None, None)
            self.registry.register_event_handler(None, None)
        except Exception:
            # It's acceptable for this to raise an exception
            pass

    def test_empty_event_handlers_list(self):
        """Test behavior with empty event handlers list."""

        class TestEvent:
            pass

        # Initially should have no handlers
        assert not self.registry.has_event_handlers(TestEvent)
        handlers = self.registry.get_event_handler_types(TestEvent)
        assert len(handlers) == 0

        # After registering and clearing, should still work
        class TestEventHandler:
            pass

        self.registry.register_event_handler(TestEvent, TestEventHandler)
        assert self.registry.has_event_handlers(TestEvent)

        self.registry.clear()
        assert not self.registry.has_event_handlers(TestEvent)
        handlers = self.registry.get_event_handler_types(TestEvent)
        assert len(handlers) == 0

    def test_stats_consistency(self):
        """Test that statistics remain consistent across operations."""

        class TestEvent:
            pass

        class TestEventHandler1:
            pass

        class TestEventHandler2:
            pass

        # Initial state
        stats = self.registry.get_stats()
        initial_event_types = stats["event_types"]
        initial_total_handlers = stats["total_event_handlers"]

        # Add handlers
        self.registry.register_event_handler(TestEvent, TestEventHandler1)
        self.registry.register_event_handler(TestEvent, TestEventHandler2)

        stats = self.registry.get_stats()
        assert stats["event_types"] == initial_event_types + 1
        assert stats["total_event_handlers"] == initial_total_handlers + 2

        # Clear and verify
        self.registry.clear()
        stats = self.registry.get_stats()
        assert stats["event_types"] == 0
        assert stats["total_event_handlers"] == 0
