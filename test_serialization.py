#!/usr/bin/env python3
"""Test script to analyze RequestId serialization behavior."""

import sys
import os
sys.path.insert(0, '/Users/flamurg/src/aws/symphony/open-resource-broker/src')

from domain.request.request_identifiers import RequestId
from domain.request.value_objects import RequestType
import json

def test_request_id_serialization():
    """Test how RequestId gets serialized in different scenarios."""
    
    print("=== RequestId Serialization Analysis ===\n")
    
    # Create a RequestId
    request_id = RequestId.generate(RequestType.ACQUIRE)
    print(f"1. Created RequestId: {request_id}")
    print(f"   Type: {type(request_id)}")
    print(f"   Value: {request_id.value}")
    print(f"   Str: {str(request_id)}")
    
    # Test Pydantic model_dump
    print(f"\n2. Pydantic model_dump(): {request_id.model_dump()}")
    
    # Test JSON serialization
    try:
        json_str = json.dumps(request_id.model_dump())
        print(f"3. JSON serialized: {json_str}")
        
        # Test deserialization
        json_data = json.loads(json_str)
        print(f"4. JSON deserialized: {json_data}")
        print(f"   Type: {type(json_data)}")
        
        # Try to recreate RequestId
        if isinstance(json_data, dict) and 'value' in json_data:
            recreated = RequestId(value=json_data['value'])
            print(f"5. Recreated RequestId: {recreated}")
        else:
            recreated = RequestId(value=json_data)
            print(f"5. Recreated RequestId: {recreated}")
            
    except Exception as e:
        print(f"3. JSON serialization failed: {e}")
    
    # Test what happens when we serialize the whole object
    print(f"\n6. Direct JSON dumps of RequestId:")
    try:
        direct_json = json.dumps(request_id, default=str)
        print(f"   With default=str: {direct_json}")
    except Exception as e:
        print(f"   Failed: {e}")
    
    # Test repository serialization method
    print(f"\n7. Repository to_dict simulation:")
    serialized_value = str(request_id.value)
    print(f"   str(request_id.value): {serialized_value}")
    print(f"   Type: {type(serialized_value)}")

if __name__ == "__main__":
    test_request_id_serialization()
