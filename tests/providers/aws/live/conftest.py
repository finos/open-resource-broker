"""Live AWS integration test configuration.

Tests in this subtree require real AWS credentials and a pre-configured ORB
environment (``orb init``).  They are skipped by default; pass ``--live`` (or
the legacy ``--run-aws``) to enable them.
"""

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

def _find_repo_root(start: Path) -> Path:
    """Walk up from *start* until we find pyproject.toml. Hard-fail otherwise.

    Robust against test-tree reshuffles: counting parents (e.g. `parent ** 5`)
    silently breaks the moment a test file moves up or down the tree. The
    pyproject.toml marker is stable as long as the repo has one.
    """
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"Could not locate repo root (no pyproject.toml) above {start}")


# Ensure repo root is on sys.path so hfmock.py and other root-level modules are importable
repo_root = _find_repo_root(Path(__file__).resolve().parent)
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Ensure logs/ directory exists before any test module is imported
# (some test files create FileHandlers at module level)
logs_dir = repo_root / "logs"
logs_dir.mkdir(exist_ok=True)


def _is_live_run(config) -> bool:
    """Return True when live tests have been explicitly requested."""
    return config.getoption("--live", default=False) or config.getoption("--run-aws", default=False)


def _get_aws_profile_and_region() -> tuple[str | None, str | None]:
    """Read profile and region from ORB config.

    Priority (matches `orb init` discovery + operator overrides):
    1. ORB_CONFIG_DIR env var (per-test or per-deployment config dir)
    2. ~/.orb/config.json (default user-level location written by `orb init`)
    3. <repo>/config/config.json (in-repo dev fallback — only present when
       running tests from a checkout that ran `orb init` inside it)
    4. AWS_REGION / AWS_DEFAULT_REGION env vars (region only)
    """
    candidates: list[str] = []
    config_dir = os.environ.get("ORB_CONFIG_DIR")
    if config_dir:
        candidates.append(os.path.join(config_dir, "config.json"))
    # User-default location for `orb init`
    candidates.append(str(Path.home() / ".orb" / "config.json"))
    # In-repo dev fallback (the directory exists only in source checkouts)
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
    """Pre-flight check: verify orb init has been run (only when --live is active)."""
    if not _is_live_run(config):
        return

    config_dir = os.environ.get("ORB_CONFIG_DIR", ".")
    config_path = Path(config_dir)

    scripts_dir = config_path / "scripts"

    if not scripts_dir.exists():
        pytest.exit(
            "live pre-flight failed: scripts/ directory not found.\n"
            "Run 'orb init' first to set up the environment.\n"
            f"Looked in: {scripts_dir.resolve()}",
            returncode=1,
        )

    invoke_script = scripts_dir / "invoke_provider.sh"
    if not invoke_script.exists():
        pytest.exit(
            "live pre-flight failed: scripts/invoke_provider.sh not found.\n"
            "Run 'orb init' first to set up the environment.\n"
            f"Looked in: {invoke_script.resolve()}",
            returncode=1,
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Check AWS credentials once before any AWS tests run.

    Only runs when --live (or --run-aws) is passed.  Calls sts:GetCallerIdentity
    and exits immediately if credentials are invalid so no tests are attempted.
    """
    if not _is_live_run(session.config):
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
        from tests.providers.aws.live.cleanup_helpers import cleanup_all_orb_resources

        profile, region = _get_aws_profile_and_region()
        region = region or "eu-west-1"
        boto_session = boto3.Session(profile_name=profile, region_name=region)
        ec2 = boto_session.client("ec2", region_name=region)
        asg = boto_session.client("autoscaling", region_name=region)
        cleanup_all_orb_resources(ec2, autoscaling_client=asg, session_id=test_session_id)
    except Exception as exc:
        logging.getLogger("onaws.conftest").warning("nuclear_cleanup: failed with %s", exc)
