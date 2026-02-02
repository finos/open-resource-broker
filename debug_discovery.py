#!/usr/bin/env python3
"""Test handler discovery and registration."""

import os
import sys
import asyncio

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def test_handler_discovery():
    """Test if handlers are being discovered and registered."""
    print("=== Testing Handler Discovery ===")
    
    # Step 1: Check if handlers are imported and decorated
    print("\n1. Checking handler imports and decorators...")
    try:
        # Import handlers to trigger decorator registration
        from application.queries import handlers
        from application.commands import request_handlers
        
        print("✅ Handler modules imported")
        
        # Check decorator registries
        from application.decorators import get_handler_registry_stats
        stats = get_handler_registry_stats()
        print(f"✅ Handler registry stats: {stats}")
        
        if stats['total_handlers'] == 0:
            print("❌ No handlers registered in decorators!")
            return
            
    except Exception as e:
        print(f"❌ Handler import failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 2: Check specific handler registration
    print("\n2. Checking specific handler registration...")
    try:
        from application.decorators import get_query_handler_for_type
        from application.dto.queries import ListTemplatesQuery
        
        handler_class = get_query_handler_for_type(ListTemplatesQuery)
        print(f"✅ Handler found for ListTemplatesQuery: {handler_class.__name__}")
        
    except KeyError as e:
        print(f"❌ Handler not found: {e}")
        
        # List all registered handlers
        from application.decorators import get_registered_query_handlers
        handlers = get_registered_query_handlers()
        print(f"Registered query handlers: {len(handlers)}")
        for query_type, handler_class in handlers.items():
            print(f"  - {query_type.__name__}: {handler_class.__name__}")
            
    except Exception as e:
        print(f"❌ Handler check failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Step 3: Test CQRS setup
    print("\n3. Testing CQRS setup...")
    try:
        from infrastructure.di.container import get_container, _setup_cqrs_infrastructure
        container = get_container()
        
        # Manually trigger CQRS setup
        _setup_cqrs_infrastructure(container)
        print("✅ CQRS infrastructure setup completed")
        
        # Check if handlers are now in DI container
        from application.dto.queries import ListTemplatesQuery
        from application.decorators import get_query_handler_for_type
        
        handler_class = get_query_handler_for_type(ListTemplatesQuery)
        
        # Try to get handler from container
        if container.has(handler_class):
            handler_instance = container.get(handler_class)
            print(f"✅ Handler instance created: {type(handler_instance)}")
        else:
            print(f"❌ Handler class {handler_class.__name__} not registered in DI container")
            
    except Exception as e:
        print(f"❌ CQRS setup failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Step 4: Test full application flow
    print("\n4. Testing full application flow...")
    try:
        from bootstrap import Application
        app = Application(skip_validation=True)
        success = await app.initialize()
        print(f"✅ Application initialized: {success}")
        
        # Test query execution
        from application.dto.queries import ListTemplatesQuery
        query = ListTemplatesQuery()
        
        query_bus = app.get_query_bus()
        result = await query_bus.execute(query)
        print(f"✅ Query executed successfully: {len(result)} templates")
        
    except Exception as e:
        print(f"❌ Application flow failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n=== Handler Discovery Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_handler_discovery())