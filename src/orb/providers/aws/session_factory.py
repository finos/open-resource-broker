"""AWS Session Factory - Centralized boto3 session creation."""

from typing import Optional

import boto3

from orb.providers.aws.utilities.boto_config import get_boto3_config

# Default timeout config for one-off clients created outside of AWSClient
_DEFAULT_CONFIG = get_boto3_config()


class AWSSessionFactory:
    """Factory for creating AWS sessions with credential chain fallback."""

    @staticmethod
    def create_session(
        profile: Optional[str] = None,
        region: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
    ) -> boto3.Session:
        """Create AWS session with credential chain fallback.

        When explicit key credentials are supplied they are passed directly to
        boto3.Session so the operator-configured keys are used instead of the
        ambient credential chain.  When they are absent (None) boto3 falls back
        to its normal chain (env vars, ~/.aws/credentials, instance profile,
        etc.).  Empty strings are treated as absent — never pass them to boto3
        because boto3 treats "" as a non-None credential and the call will fail
        or use an unexpected identity.

        Args:
            profile: AWS profile name (optional)
            region: AWS region (optional)
            aws_access_key_id: Explicit access key ID (plain str, optional)
            aws_secret_access_key: Explicit secret access key (plain str, optional)
            aws_session_token: Explicit session token (plain str, optional)

        Returns:
            Configured boto3 session
        """
        # Build kwargs dict; only include credential keys when they have a
        # non-empty value so boto3 credential-chain fallback is unaffected.
        kwargs: dict = {}
        if region:
            kwargs["region_name"] = region
        if profile:
            kwargs["profile_name"] = profile
        if aws_access_key_id:
            kwargs["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key:
            kwargs["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            kwargs["aws_session_token"] = aws_session_token
        return boto3.Session(**kwargs)

    @staticmethod
    def discover_credentials(profile: Optional[str] = None, region: Optional[str] = None) -> dict:
        """Discover AWS credentials and return metadata.

        Args:
            profile: AWS profile name (optional)
            region: AWS region (optional)

        Returns:
            Dict with success status and credential metadata
        """
        try:
            session = AWSSessionFactory.create_session(profile, region)
            identity = session.client("sts", config=_DEFAULT_CONFIG).get_caller_identity()
            return {
                "success": True,
                "profile": profile,
                "region": session.region_name,
                "account": identity["Account"],
                "identity": identity["Arn"],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
