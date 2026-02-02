#!/usr/bin/env python3
"""Test CLI execution path."""

import sys
import os
sys.path.insert(0, 'src')

# Mimic the run.py setup
def setup_environment():
    """Setup environment variables using platform-specific directories."""
    if os.environ.get("ORB_CONFIG_DIR"):
        return

    try:
        from config.platform_dirs import get_config_location, get_logs_location, get_work_location

        config_dir = str(get_config_location())
        work_dir = str(get_work_location())
        logs_dir = str(get_logs_location())

        os.environ.setdefault("ORB_CONFIG_DIR", config_dir)
        os.environ.setdefault("ORB_WORK_DIR", work_dir)
        os.environ.setdefault("ORB_LOG_DIR", logs_dir)

    except Exception as e:
        print(f"WARNING: Config directory detection failed: {e}", file=sys.stderr)

def test_cli_path():
    """Test the CLI execution path."""
    print("Setting up environment...")
    setup_environment()
    
    print("Importing CLI main...")
    from cli.main import main
    
    print("Creating mock args for templates list...")
    import argparse
    
    # Mock sys.argv to simulate 'orb templates list'
    original_argv = sys.argv
    sys.argv = ['orb', 'templates', 'list']
    
    try:
        print("Running CLI main...")
        import asyncio
        result = asyncio.run(main())
        print(f"CLI result: {result}")
    except Exception as e:
        print(f"CLI error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        sys.argv = original_argv

if __name__ == "__main__":
    test_cli_path()