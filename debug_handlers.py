#!/usr/bin/env python3
"""Test handler registration specifically."""

import os
import sys
import asyncio

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def test_handler_registration():
    """Test if handlers are being registered properly."""
    print("=== Testing Handler Registration ===")
    
    # Step 1: Create application
    print("\n1. Creating application...")
    try:
        from bootstrap import Application
        app = Application(skip_validation=True)
        success = await app.initialize()
        print(f"✅ Application initialized: {success}")
    except Exception as e:
        print(f"❌ Application initialization failed: {e}")
        return
    
    # Step 2: Get query bus and check registered handlers
    print("\n2. Checking registered handlers...")
    try:
        query_bus = app.get_query_bus()
        
        # Check if query bus has handlers registered
        if hasattr(query_bus, '_handlers'):
            handlers = query_bus._handlers
            print(f"✅ Query handlers registered: {len(handlers)}")
            for query_type, handler in handlers.items():
                print(f"  - {query_type.__name__}: {handler.__class__.__name__}")
        else:
            print("❌ Query bus has no _handlers attribute")
            
        # Check command bus too
        command_bus = app.get_command_bus()
        if hasattr(command_bus, '_handlers'):
            handlers = command_bus._handlers
            print(f"✅ Command handlers registered: {len(handlers)}")
            for command_type, handler in handlers.items():
                print(f"  - {command_type.__name__}: {handler.__class__.__name__}")
        else:
            print("❌ Command bus has no _handlers attribute")
            
    except Exception as e:
        print(f"❌ Handler check failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 3: Test specific query execution
    print("\n3. Testing specific query execution...")
    try:
        from application.dto.queries import ListTemplatesQuery
        query = ListTemplatesQuery()
        
        # Check if handler exists for this query
        if hasattr(query_bus, '_handlers') and ListTemplatesQuery in query_bus._handlers:
            handler = query_bus._handlers[ListTemplatesQuery]
            print(f"✅ Handler found for ListTemplatesQuery: {handler.__class__.__name__}")
            
            # Execute the query
            result = await query_bus.execute(query)
            print(f"✅ Query executed successfully: {type(result)}")
            
        else:
            print("❌ No handler registered for ListTemplatesQuery")
            
    except Exception as e:
        print(f"❌ Query execution failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Step 4: Check DI container registrations
    print("\n4. Checking DI container registrations...")
    try:
        from infrastructure.di.container import get_container
        container = get_container()
        
        # Check if container has factories
        if hasattr(container, '_factories'):
            factories = container._factories
            print(f"✅ DI factories registered: {len(factories)}")
            
            # Look for handler-related registrations
            handler_factories = [k for k in factories.keys() if 'Handler' in str(k)]
            print(f"Handler factories: {len(handler_factories)}")
            for factory in handler_factories[:10]:  # Show first 10
                print(f"  - {factory}")
                
        else:
            print("❌ Container has no _factories attribute")
            
    except Exception as e:
        print(f"❌ Container check failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n=== Handler Registration Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_handler_registration())