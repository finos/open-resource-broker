#!/usr/bin/env python3
"""Debug DI registration issue."""

import sys
import os
sys.path.insert(0, 'src')

def test_di_registration():
    """Test DI registration to see what's happening."""
    from infrastructure.di.container import DIContainer
    from infrastructure.di.services import register_all_services
    from domain.base.ports.logging_port import LoggingPort
    
    print("Creating DI container...")
    container = DIContainer()
    
    print("Registering services...")
    register_all_services(container)
    
    print("Checking if LoggingPort is registered...")
    print(f"LoggingPort registered: {container.has(LoggingPort)}")
    
    if container.has(LoggingPort):
        print("Getting LoggingPort registration...")
        try:
            registration = container._service_registry.get_registration(LoggingPort)
            print(f"Registration: {registration}")
            print(f"Factory: {registration.factory}")
            print(f"Implementation type: {registration.implementation_type}")
            print(f"Instance: {registration.instance}")
        except Exception as e:
            print(f"Error getting registration: {e}")
    
    print("Trying to get LoggingPort...")
    try:
        logging_port = container.get(LoggingPort)
        print(f"Successfully got LoggingPort: {logging_port}")
        print(f"Type: {type(logging_port)}")
    except Exception as e:
        print(f"Error getting LoggingPort: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_di_registration()