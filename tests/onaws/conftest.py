"""onaws integration test configuration."""

import json
import os
import sys
from pathlib import Path

import pytest
from boto3 import Session
from botocore.exceptions import ClientError, NoCredentialsError

# Ensure repo root is on sys.path so hfmock.py and other root-level modules are importable
repo_root = Path(__file__).parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Ensure logs/ directory exists before any test module is imported
# (some test files create FileHandlers at module level)
logs_dir = repo_root / "logs"
logs_dir.mkdir(exist_ok=True)


def _get_aws_profile_and_region() -> tuple[str | None, str | None]:
    """Read profile and region from ORB config.

    Priority:
    1. ORB_CONFIG_DIR env var (per-test config dir)
    2. Project config/config.json (written by orb init)
    3. AWS_REGION / AWS_DEFAULT_REGION env vars
    """
    candidates = []
    config_dir = os.environ.get("ORB_CONFIG_DIR")
    if config_dir:
        candidates.append(os.path.join(config_dir, "config.json"))
    # Fall back to the project's real config written by orb init
    candidates.append(str(repo_root / "config" / "config.json"))

    for config_path in candidates:
        try:
            with open(config_path) as f:
                config = json.load(f)
            providers = config.get("provider", {}).get("providers", [])
            if providers:
                provider_config = providers[0].get("config", {})
                profile = provider_config.get("profile")
                region = provider_config.get("region")
                if profile or region:
                    return profile, region
        except Exception:
            pass

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    return None, region


def pytest_sessionstart(session: pytest.Session) -> None:
    """Check AWS credentials once before any AWS tests run.

    Only runs when --run-aws is passed. Calls sts:GetCallerIdentity and exits
    immediately if credentials are invalid so no tests are attempted.
    """
    if not session.config.getoption("--run-aws", default=False):
        return
    profile, region = _get_aws_profile_and_region()
    region = region or "eu-west-1"
    try:
        boto_session = Session(profile_name=profile, region_name=region)
        sts = boto_session.client("sts", region_name=region)
        identity = sts.get_caller_identity()
        print(
            f"\n✓ AWS credentials valid: {identity.get('Arn')} (account: {identity.get('Account')})"
        )
    except NoCredentialsError as e:
        pytest.exit(f"AWS credentials not found (profile={profile!r}): {e}", returncode=1)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        msg = e.response.get("Error", {}).get("Message", str(e))
        pytest.exit(f"AWS credentials invalid [{code}]: {msg}", returncode=1)
    except Exception as e:
        pytest.exit(f"AWS credential check failed: {e}", returncode=1)


@pytest.fixture(autouse=True)
def reset_di_container():
    """Reset DI container between tests.

    Each onaws test sets ORB_CONFIG_DIR to a per-test temp directory.
    ConfigurationManager is a DI singleton that caches the config path at
    construction time. Without a reset, the second test's container still
    reads/writes to the first test's work directory.
    """
    yield
    from infrastructure.di.container import reset_container

    reset_container()
