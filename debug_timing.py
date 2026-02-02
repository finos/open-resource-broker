#!/usr/bin/env python3
"""Test registration timing and order."""

import os
import sys
import asyncio
import time

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def log_step(step, message):
    """Log a step with timestamp."""
    timestamp = time.strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {step}: {message}")

async def test_registration_timing():
    """Test the exact timing and order of registrations."""
    print("=== Testing Registration Timing and Order ===")
    
    # Step 1: Import bootstrap
    log_step("1", "Importing bootstrap...")
    try:
        from bootstrap import Application
        log_step("1", "✅ Bootstrap imported")
    except Exception as e:
        log_step("1", f"❌ Bootstrap import failed: {e}")
        return
    
    # Step 2: Create application (no initialization yet)
    log_step("2", "Creating application instance...")
    try:
        app = Application(skip_validation=True)
        log_step("2", "✅ Application instance created")
    except Exception as e:
        log_step("2", f"❌ Application creation failed: {e}")
        return
    
    # Step 3: Check handler registry before initialization
    log_step("3", "Checking handler registry before initialization...")
    try:
        from application.decorators import get_handler_registry_stats
        stats_before = get_handler_registry_stats()
        log_step("3", f"✅ Handler stats before init: {stats_before}")
    except Exception as e:
        log_step("3", f"❌ Handler stats check failed: {e}")
        return
    
    # Step 4: Initialize application
    log_step("4", "Initializing application...")
    try:
        success = await app.initialize()
        log_step("4", f"✅ Application initialized: {success}")
    except Exception as e:
        log_step("4", f"❌ Application initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 5: Check handler registry after initialization
    log_step("5", "Checking handler registry after initialization...")
    try:
        stats_after = get_handler_registry_stats()
        log_step("5", f"✅ Handler stats after init: {stats_after}")
        
        if stats_after['total_handlers'] == 0:
            log_step("5", "❌ No handlers registered after initialization!")
        else:
            log_step("5", f"✅ {stats_after['total_handlers']} handlers registered")
            
    except Exception as e:
        log_step("5", f"❌ Handler stats check failed: {e}")
        return
    
    # Step 6: Check DI container state
    log_step("6", "Checking DI container state...")
    try:
        from infrastructure.di.container import get_container
        container = get_container()
        
        # Check if container has the expected services
        from config.managers.configuration_manager import ConfigurationManager
        from domain.base.ports import LoggingPort
        
        has_config = container.has(ConfigurationManager)
        has_logging = container.has(LoggingPort)
        
        log_step("6", f"✅ Container state - Config: {has_config}, Logging: {has_logging}")
        
    except Exception as e:
        log_step("6", f"❌ Container check failed: {e}")
        return
    
    # Step 7: Check CQRS buses
    log_step("7", "Checking CQRS buses...")
    try:
        query_bus = app.get_query_bus()
        command_bus = app.get_command_bus()
        
        log_step("7", f"✅ Buses created - Query: {type(query_bus)}, Command: {type(command_bus)}")
        
    except Exception as e:
        log_step("7", f"❌ Bus creation failed: {e}")
        return
    
    # Step 8: Test specific handler resolution
    log_step("8", "Testing specific handler resolution...")
    try:
        from application.dto.queries import ListTemplatesQuery
        from application.decorators import get_query_handler_for_type
        
        handler_class = get_query_handler_for_type(ListTemplatesQuery)
        log_step("8", f"✅ Handler class found: {handler_class.__name__}")
        
        # Try to get handler instance from container
        container = get_container()
        if container.has(handler_class):
            handler_instance = container.get(handler_class)
            log_step("8", f"✅ Handler instance created: {type(handler_instance)}")
        else:
            log_step("8", f"❌ Handler class not in DI container")
            
    except Exception as e:
        log_step("8", f"❌ Handler resolution failed: {e}")
        return
    
    # Step 9: Test query execution
    log_step("9", "Testing query execution...")
    try:
        from application.dto.queries import ListTemplatesQuery
        query = ListTemplatesQuery()
        
        result = await query_bus.execute(query)
        log_step("9", f"✅ Query executed: {len(result)} templates found")
        
    except Exception as e:
        log_step("9", f"❌ Query execution failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 10: Check provider registration
    log_step("10", "Checking provider registration...")
    try:
        provider_info = app.get_provider_info()
        log_step("10", f"✅ Provider info: {provider_info}")
        
    except Exception as e:
        log_step("10", f"❌ Provider check failed: {e}")
        return
    
    print("\n=== Registration Timing Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_registration_timing())