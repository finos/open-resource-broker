#!/usr/bin/env python3
"""Debug lazy registration specifically."""

import os
import sys
import asyncio

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def debug_lazy_registration():
    """Debug the lazy registration process."""
    print("=== Debugging Lazy Registration ===")
    
    # Step 1: Create container and check lazy loading config
    print("\n1. Creating container and checking lazy loading...")
    try:
        from infrastructure.di.container import get_container
        container = get_container()
        
        is_lazy = container.is_lazy_loading_enabled()
        print(f"✅ Container created, lazy loading enabled: {is_lazy}")
        
        # Check internal state
        if hasattr(container, '_on_demand_registrations'):
            on_demand_count = len(container._on_demand_registrations)
            print(f"On-demand registrations: {on_demand_count}")
        
        if hasattr(container, '_lazy_factories'):
            lazy_factory_count = len(container._lazy_factories)
            print(f"Lazy factories: {lazy_factory_count}")
            
    except Exception as e:
        print(f"❌ Container creation failed: {e}")
        return
    
    # Step 2: Register services and check on-demand registrations
    print("\n2. Registering services...")
    try:
        from infrastructure.di.services import register_all_services
        register_all_services(container)
        
        print("✅ Services registered")
        
        # Check on-demand registrations after service registration
        if hasattr(container, '_on_demand_registrations'):
            on_demand_regs = container._on_demand_registrations
            print(f"On-demand registrations after service registration: {len(on_demand_regs)}")
            
            # List the registered types
            for cls in on_demand_regs.keys():
                print(f"  - {cls.__name__}")
                
        else:
            print("❌ Container has no _on_demand_registrations attribute")
            
    except Exception as e:
        print(f"❌ Service registration failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 3: Try to get QueryBus and see if lazy registration triggers
    print("\n3. Testing QueryBus lazy registration...")
    try:
        from infrastructure.di.buses import QueryBus
        
        # Check if QueryBus is registered before getting it
        has_query_bus_before = container.has(QueryBus)
        print(f"QueryBus registered before get(): {has_query_bus_before}")
        
        # Try to get QueryBus - this should trigger lazy registration
        print("Attempting to get QueryBus...")
        query_bus = container.get(QueryBus)
        print(f"✅ QueryBus obtained: {type(query_bus)}")
        
        # Check if QueryBus is registered after getting it
        has_query_bus_after = container.has(QueryBus)
        print(f"QueryBus registered after get(): {has_query_bus_after}")
        
    except Exception as e:
        print(f"❌ QueryBus lazy registration failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 4: Check handler registration after lazy setup
    print("\n4. Checking handler registration after lazy setup...")
    try:
        from application.decorators import get_handler_registry_stats
        stats = get_handler_registry_stats()
        print(f"✅ Handler stats after lazy setup: {stats}")
        
        if stats['total_handlers'] == 0:
            print("❌ Still no handlers registered!")
            
            # Try manual handler discovery
            print("Attempting manual handler discovery...")
            from infrastructure.di.handler_discovery import create_handler_discovery_service
            discovery_service = create_handler_discovery_service(container)
            discovery_service.discover_and_register_handlers()
            
            stats_after_manual = get_handler_registry_stats()
            print(f"Handler stats after manual discovery: {stats_after_manual}")
            
    except Exception as e:
        print(f"❌ Handler check failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 5: Test query execution
    print("\n5. Testing query execution...")
    try:
        from application.dto.queries import ListTemplatesQuery
        query = ListTemplatesQuery()
        
        result = await query_bus.execute(query)
        print(f"✅ Query executed: {len(result)} templates")
        
    except Exception as e:
        print(f"❌ Query execution failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n=== Lazy Registration Debug Complete ===")

if __name__ == "__main__":
    asyncio.run(debug_lazy_registration())