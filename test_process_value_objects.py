#!/usr/bin/env python3
"""Test script to verify process_value_objects function."""

import sys
import os
sys.path.insert(0, '/Users/flamurg/src/aws/symphony/open-resource-broker/src')

from domain.request.request_identifiers import RequestId
from domain.request.value_objects import RequestType
from infrastructure.utilities.common.serialization import process_value_objects
import json

def test_process_value_objects():
    """Test if process_value_objects correctly handles value objects."""
    
    print("=== Testing process_value_objects Function ===\n")
    
    # Create test objects
    request_id = RequestId.generate(RequestType.ACQUIRE)
    
    print(f"1. Original RequestId: {request_id}")
    print(f"   Type: {type(request_id)}")
    print(f"   Value: {request_id.value}")
    
    # Test model_dump
    model_dump_result = request_id.model_dump()
    print(f"\n2. model_dump() result: {model_dump_result}")
    print(f"   Type: {type(model_dump_result)}")
    
    # Test process_value_objects on model_dump result
    processed_result = process_value_objects(model_dump_result)
    print(f"\n3. process_value_objects(model_dump()) result: {processed_result}")
    print(f"   Type: {type(processed_result)}")
    
    # Test process_value_objects on the object directly
    direct_processed = process_value_objects(request_id)
    print(f"\n4. process_value_objects(request_id) result: {direct_processed}")
    print(f"   Type: {type(direct_processed)}")
    
    # Test on a dict with value objects
    test_dict = {"request_id": request_id, "other": "value"}
    dict_processed = process_value_objects(test_dict)
    print(f"\n5. Dict with value object: {test_dict}")
    print(f"   Processed: {dict_processed}")

if __name__ == "__main__":
    test_process_value_objects()
