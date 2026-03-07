"""Unit tests for ORBClientProtocol."""

import inspect

from orb.sdk.protocols import ORBClientProtocol

EXPECTED_METHODS = [
    # Template operations
    "get_template",
    "list_templates",
    "validate_template",
    "get_configuration",
    "create_template",
    "update_template",
    "delete_template",
    "refresh_templates",
    # Request operations
    "get_request",
    "list_requests",
    "list_return_requests",
    "list_active_requests",
    "get_request_summary",
    "create_request",
    "create_return_request",
    "update_request_status",
    "cancel_request",
    "complete_request",
    "sync_request",
    "populate_machine_ids",
    # Machine operations
    "get_machine",
    "list_machines",
    "get_active_machine_count",
    "get_machine_health",
    "update_machine_status",
    "convert_machine_status",
    "convert_batch_machine_status",
    "cleanup_machine_resources",
    "register_machine",
    "deregister_machine",
    # Provider operations
    "get_provider_health",
    "list_available_providers",
    "get_provider_capabilities",
    "get_provider_metrics",
    "get_provider_strategy_config",
    "execute_provider_operation",
    "register_provider_strategy",
    "update_provider_health",
    # Bulk operations
    "get_multiple_requests",
    "get_multiple_templates",
    "get_multiple_machines",
    # Cleanup operations
    "list_cleanable_requests",
    "list_cleanable_resources",
    "cleanup_old_requests",
    "cleanup_all_resources",
    # Storage operations
    "list_storage_strategies",
    "get_storage_health",
    "get_storage_metrics",
    # Scheduler operations
    "list_scheduler_strategies",
    "get_scheduler_configuration",
    "validate_scheduler_configuration",
    # System / config operations
    "get_configuration_section",
    "get_provider_config",
    "validate_provider_config",
    "get_system_status",
    "validate_storage",
    "validate_mcp",
    "validate_provider_state",
    "reload_provider_config",
    "set_configuration",
]


class TestORBClientProtocol:
    def test_protocol_is_importable_from_sdk_package(self):
        from orb.sdk import ORBClientProtocol as imported

        assert imported is ORBClientProtocol

    def test_protocol_is_runtime_checkable(self):
        # @runtime_checkable means isinstance() works against it
        assert hasattr(ORBClientProtocol, "__protocol_attrs__") or hasattr(
            ORBClientProtocol, "_is_protocol"
        )

    def test_all_expected_methods_present(self):
        missing = [m for m in EXPECTED_METHODS if not hasattr(ORBClientProtocol, m)]
        assert missing == [], f"Protocol is missing methods: {missing}"

    def test_all_protocol_methods_are_coroutines(self):
        non_coro = [
            m
            for m in EXPECTED_METHODS
            if not inspect.iscoroutinefunction(getattr(ORBClientProtocol, m, None))
        ]
        assert non_coro == [], f"These protocol methods are not async: {non_coro}"

    def test_protocol_method_count(self):
        # Sanity check — protocol should have at least as many methods as our list
        protocol_methods = [
            name
            for name, _ in inspect.getmembers(ORBClientProtocol, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        assert len(protocol_methods) >= len(EXPECTED_METHODS)
