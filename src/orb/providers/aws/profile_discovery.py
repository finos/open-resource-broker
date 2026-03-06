"""AWS Profile Discovery - Discover available AWS profiles."""

import configparser
from pathlib import Path
from typing import Dict, List, Optional


def probe_instance_profile_credentials(region: Optional[str] = None) -> bool:
    """Check if credentials are available via the default chain (env vars, instance profile, etc.)."""
    try:
        import boto3

        session = boto3.Session(region_name=region)
        sts = session.client("sts")
        sts.get_caller_identity()
        return True
    except Exception:
        return False


def get_available_profiles() -> List[Dict[str, str]]:
    """Get available AWS profiles from config files.

    Returns:
        List of profile dictionaries with name and description
    """
    profiles = []

    config_file = Path.home() / ".aws" / "config"
    creds_file = Path.home() / ".aws" / "credentials"

    profile_names = set()

    for file_path in [config_file, creds_file]:
        if file_path.exists():
            try:
                config = configparser.ConfigParser()
                config.read(file_path)
                for section in config.sections():
                    if section.startswith("profile "):
                        profile_names.add(section[8:])  # Remove "profile " prefix
                    elif section == "default":
                        profile_names.add("default")
            except Exception as e:
                # Ignore parsing errors and continue
                from orb.infrastructure.logging.logger import get_logger

                logger = get_logger(__name__)
                logger.debug(f"Failed to parse AWS config file: {e}")

    for profile_name in sorted(profile_names):
        profiles.append({"name": profile_name, "description": f"Profile: {profile_name}"})

    # Probe for instance profile / environment credentials (no ~/.aws/config needed)
    if probe_instance_profile_credentials():
        profiles.append(
            {"name": None, "description": "Environment / Instance Profile (auto-discovered)"}
        )
    else:
        profiles.append({"name": None, "description": "Auto-discover credentials"})

    return profiles
