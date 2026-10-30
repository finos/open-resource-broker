"""Platform-specific environment setup for config discovery."""

import os
import sys


# Setup environment variables for config discovery
def setup_environment():
    """Setup environment variables using platform-specific directories."""
    # Only set if not already set by user
    if os.environ.get("ORB_CONFIG_DIR"):
        return  # User has explicitly set ORB_* vars

    try:
        from orb.config.platform_dirs import (
            get_config_location,
            get_logs_location,
            get_scripts_location,
            get_work_location,
        )

        config_dir = str(get_config_location())
        work_dir = str(get_work_location())
        logs_dir = str(get_logs_location())
        scripts_dir = str(get_scripts_location())

        # Set ORB_* vars (standard) - bootstrap only
        os.environ.setdefault("ORB_CONFIG_DIR", config_dir)
        os.environ.setdefault("ORB_WORK_DIR", work_dir)
        os.environ.setdefault("ORB_LOG_DIR", logs_dir)
        os.environ.setdefault("ORB_SCRIPTS_DIR", scripts_dir)

    except Exception as e:
        # Config discovery failed - will be caught later with helpful error
        print(f"WARNING: Config directory detection failed: {e}", file=sys.stderr)
