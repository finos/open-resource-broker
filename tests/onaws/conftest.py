"""onaws integration test configuration."""

import json
import logging
import os
import sys
import uuid
from pathlib import Path

import boto3
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


def pytest_configure(config) -> None:
    """Pre-flight check: verify orb init has been run."""
    config_dir = os.environ.get("ORB_CONFIG_DIR", ".")
    config_path = Path(config_dir)

    scripts_dir = config_path / "scripts"

    if not scripts_dir.exists():
        pytest.exit(
            "onaws pre-flight failed: scripts/ directory not found.\n"
            "Run 'orb init' first to set up the environment.\n"
            f"Looked in: {scripts_dir.resolve()}",
            returncode=1,
        )

    invoke_script = scripts_dir / "invoke_provider.sh"
    if not invoke_script.exists():
        pytest.exit(
            "onaws pre-flight failed: scripts/invoke_provider.sh not found.\n"
            "Run 'orb init' first to set up the environment.\n"
            f"Looked in: {invoke_script.resolve()}",
            returncode=1,
        )


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


@pytest.fixture(scope="session")
def test_session_id() -> str:
    """Generate a unique ID for this test session for targeted AWS resource cleanup."""
    return uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def reset_di_container():
    """Reset DI container between tests.

    Each onaws test sets ORB_CONFIG_DIR to a per-test temp directory.
    ConfigurationManager is a DI singleton that caches the config path at
    construction time. Without a reset, the second test's container still
    reads/writes to the first test's work directory.
    """
    yield
    from orb.infrastructure.di.container import reset_container

    reset_container()


@pytest.fixture(scope="session", autouse=True)
def nuclear_cleanup(test_session_id: str):
    """Session-scoped safety net: clean up resources from this test session after all tests.

    Runs once after the entire test session completes. Uses the session tag for
    targeted cleanup when available. Best-effort only — never raises so it
    cannot interfere with test result reporting.
    """
    yield

    try:
        from tests.onaws.cleanup_helpers import cleanup_all_orb_resources

        profile, region = _get_aws_profile_and_region()
        region = region or "eu-west-1"
        boto_session = boto3.Session(profile_name=profile, region_name=region)
        ec2 = boto_session.client("ec2", region_name=region)
        asg = boto_session.client("autoscaling", region_name=region)
        cleanup_all_orb_resources(ec2, autoscaling_client=asg, session_id=test_session_id)
    except Exception as exc:
        logging.getLogger("onaws.conftest").warning(
            "nuclear_cleanup: failed with %s", exc
        )
