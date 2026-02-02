#!/usr/bin/env python3
"""Debug templates list command."""

import sys
import os
sys.path.insert(0, 'src')

def test_templates_list():
    """Test templates list to see where it fails."""
    try:
        from interface.template_command_handlers import handle_list_templates
        import argparse
        import asyncio
        
        print("Creating args...")
        args = argparse.Namespace()
        
        print("Executing templates list...")
        result = asyncio.run(handle_list_templates(args))
        print(f"Result: {result}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_templates_list()