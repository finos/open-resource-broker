#!/usr/bin/env python3
"""Debug script to test registration flow."""

import os
import sys
import asyncio

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def test_registration_flow():
    """Test the registration flow step by step."""
    print("=== Testing Registration Flow ===")
    
    # Step 1: Test basic imports
    print("\n1. Testing basic imports...")
    try:
        from bootstrap import Application
        print("✅ Bootstrap import successful")
    except Exception as e:
        print(f"❌ Bootstrap import failed: {e}")
        return
    
    # Step 2: Test DI container creation
    print("\n2. Testing DI container creation...")
    try:
        from infrastructure.di.container import get_container
        container = get_container()
        print(f"✅ DI container created: {type(container)}")
    except Exception as e:
        print(f"❌ DI container creation failed: {e}")
        return
    
    # Step 3: Test service registration
    print("\n3. Testing service registration...")
    try:
        from infrastructure.di.services import register_all_services
        register_all_services(container)
        print("✅ Service registration completed")
    except Exception as e:
        print(f"❌ Service registration failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 4: Test provider registration specifically
    print("\n4. Testing provider registration...")
    try:
        from infrastructure.di.provider_services import register_provider_services
        register_provider_services(container)
        print("✅ Provider services registered")
    except Exception as e:
        print(f"❌ Provider service registration failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 5: Test application initialization
    print("\n5. Testing application initialization...")
    try:
        app = Application(skip_validation=True)
        success = await app.initialize()
        print(f"✅ Application initialized: {success}")
        
        # Check provider info
        provider_info = app.get_provider_info()
        print(f"Provider info: {provider_info}")
        
    except Exception as e:
        print(f"❌ Application initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 6: Test template listing
    print("\n6. Testing template listing...")
    try:
        query_bus = app.get_query_bus()
        print(f"✅ Query bus obtained: {type(query_bus)}")
        
        from application.dto.queries import ListTemplatesQuery
        query = ListTemplatesQuery()
        result = await query_bus.execute(query)
        print(f"✅ Template query executed: {len(result)} templates found")
        
    except Exception as e:
        print(f"❌ Template listing failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n=== Registration Flow Test Complete ===")

if __name__ == "__main__":
    asyncio.run(test_registration_flow())