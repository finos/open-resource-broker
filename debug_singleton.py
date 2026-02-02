#!/usr/bin/env python3
"""Debug container singleton behavior."""

import sys
import os
sys.path.insert(0, 'src')

def test_container_singleton():
    """Test container singleton behavior."""
    from infrastructure.di.container import get_container
    from domain.base.ports.logging_port import LoggingPort
    
    print("Getting container instance 1...")
    container1 = get_container()
    print(f"Container 1 ID: {id(container1)}")
    print(f"Container 1 has LoggingPort: {container1.has(LoggingPort)}")
    
    print("Getting container instance 2...")
    container2 = get_container()
    print(f"Container 2 ID: {id(container2)}")
    print(f"Container 2 has LoggingPort: {container2.has(LoggingPort)}")
    
    print(f"Same instance: {container1 is container2}")
    
    if container1.has(LoggingPort):
        try:
            logging_port1 = container1.get(LoggingPort)
            print(f"Container 1 LoggingPort: {logging_port1}")
        except Exception as e:
            print(f"Container 1 LoggingPort error: {e}")
    
    if container2.has(LoggingPort):
        try:
            logging_port2 = container2.get(LoggingPort)
            print(f"Container 2 LoggingPort: {logging_port2}")
        except Exception as e:
            print(f"Container 2 LoggingPort error: {e}")

if __name__ == "__main__":
    test_container_singleton()