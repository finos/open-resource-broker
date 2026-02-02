#!/usr/bin/env python3
"""Debug the lazy CQRS setup function."""

import os
import sys
import asyncio

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def debug_lazy_cqrs_setup():
    """Debug the lazy CQRS setup function."""
    print("=== Debugging Lazy CQRS Setup Function ===")
    
    # Step 1: Create container and register services
    print("\n1. Setting up container...")
    try:
        from infrastructure.di.container import get_container
        container = get_container()
        
        from infrastructure.di.services import register_all_services
        register_all_services(container)
        
        print("✅ Container and services set up")
        
    except Exception as e:
        print(f"❌ Setup failed: {e}")
        return
    
    # Step 2: Check handler stats before CQRS setup
    print("\n2. Checking handler stats before CQRS setup...")
    try:
        from application.decorators import get_handler_registry_stats
        stats_before = get_handler_registry_stats()
        print(f"Handler stats before: {stats_before}")
        
    except Exception as e:
        print(f"❌ Handler stats check failed: {e}")
        return
    
    # Step 3: Manually call the lazy CQRS setup function
    print("\n3. Manually calling lazy CQRS setup...")
    try:
        # This is the function that should be called when QueryBus is accessed
        def setup_cqrs_lazy(c):
            """Setup CQRS infrastructure lazily when needed."""
            print("  setup_cqrs_lazy() called")
            from infrastructure.di.container import _setup_cqrs_infrastructure
            _setup_cqrs_infrastructure(c)
            print("  _setup_cqrs_infrastructure() completed")
        
        # Call it manually
        setup_cqrs_lazy(container)
        print("✅ Lazy CQRS setup completed")
        
    except Exception as e:
        print(f"❌ Lazy CQRS setup failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 4: Check handler stats after CQRS setup
    print("\n4. Checking handler stats after CQRS setup...")
    try:
        stats_after = get_handler_registry_stats()
        print(f"Handler stats after: {stats_after}")
        
        if stats_after['total_handlers'] > 0:
            print("✅ Handlers were registered by CQRS setup!")
        else:
            print("❌ CQRS setup did not register handlers")
            
    except Exception as e:
        print(f"❌ Handler stats check failed: {e}")
        return
    
    # Step 5: Test QueryBus creation and execution
    print("\n5. Testing QueryBus after manual setup...")
    try:
        from infrastructure.di.buses import QueryBus
        query_bus = container.get(QueryBus)
        
        from application.dto.queries import ListTemplatesQuery
        query = ListTemplatesQuery()
        
        result = await query_bus.execute(query)
        print(f"✅ Query executed: {len(result)} templates")
        
    except Exception as e:
        print(f"❌ QueryBus test failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Step 6: Compare with the actual lazy registration
    print("\n6. Testing actual lazy registration mechanism...")
    try:
        # Create a fresh container
        from infrastructure.di.container import DIContainer
        fresh_container = DIContainer()
        
        # Register services with lazy loading
        register_all_services(fresh_container)
        
        # Check on-demand registrations
        if hasattr(fresh_container, '_on_demand_registrations'):
            on_demand_regs = fresh_container._on_demand_registrations
            print(f"Fresh container on-demand registrations: {len(on_demand_regs)}")
            
            # Check if QueryBus has an on-demand registration
            from infrastructure.di.buses import QueryBus
            if QueryBus in on_demand_regs:
                print("✅ QueryBus has on-demand registration")
                
                # Try to trigger it by getting QueryBus
                print("Getting QueryBus to trigger on-demand registration...")
                fresh_query_bus = fresh_container.get(QueryBus)
                
                # Check handler stats
                fresh_stats = get_handler_registry_stats()
                print(f"Handler stats after fresh QueryBus get: {fresh_stats}")
                
            else:
                print("❌ QueryBus does not have on-demand registration")
                
    except Exception as e:
        print(f"❌ Fresh container test failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n=== Lazy CQRS Setup Debug Complete ===")

if __name__ == "__main__":
    asyncio.run(debug_lazy_cqrs_setup())