#!/usr/bin/env python3
"""Debug QueryBus creation."""

import sys
import os
sys.path.insert(0, 'src')

def test_query_bus():
    """Test QueryBus creation."""
    from infrastructure.di.container import get_container
    from infrastructure.di.buses import QueryBus
    from domain.base.ports.logging_port import LoggingPort
    
    print("Getting container...")
    container = get_container()
    print(f"Container has LoggingPort: {container.has(LoggingPort)}")
    print(f"Container has QueryBus: {container.has(QueryBus)}")
    
    print("Getting LoggingPort...")
    try:
        logging_port = container.get(LoggingPort)
        print(f"LoggingPort: {logging_port}")
    except Exception as e:
        print(f"LoggingPort error: {e}")
        import traceback
        traceback.print_exc()
    
    print("Getting QueryBus...")
    try:
        query_bus = container.get(QueryBus)
        print(f"QueryBus: {query_bus}")
    except Exception as e:
        print(f"QueryBus error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_query_bus()