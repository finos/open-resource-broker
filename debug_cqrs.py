#!/usr/bin/env python3
"""Debug the exact failure point."""

import sys
import os
sys.path.insert(0, 'src')

def test_cqrs_setup():
    """Test CQRS setup directly."""
    from infrastructure.di.container import get_container, _setup_cqrs_infrastructure
    from domain.base.ports.logging_port import LoggingPort
    from infrastructure.di.buses import QueryBus
    
    print("Getting container...")
    container = get_container()
    
    print(f"Container has LoggingPort: {container.has(LoggingPort)}")
    
    print("Testing LoggingPort resolution...")
    try:
        logging_port = container.get(LoggingPort)
        print(f"LoggingPort resolved: {logging_port}")
    except Exception as e:
        print(f"LoggingPort resolution failed: {e}")
        return
    
    print("Testing CQRS setup...")
    try:
        _setup_cqrs_infrastructure(container)
        print("CQRS setup successful")
    except Exception as e:
        print(f"CQRS setup failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("Testing QueryBus after CQRS setup...")
    try:
        query_bus = container.get(QueryBus)
        print(f"QueryBus resolved: {query_bus}")
    except Exception as e:
        print(f"QueryBus resolution failed: {e}")

if __name__ == "__main__":
    test_cqrs_setup()