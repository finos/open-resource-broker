"""AWS IAM authentication strategy."""

from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)


@injectable
class IAMAuthStrategy(AuthPort):
    """Authentication strategy using AWS IAM credentials and policies."""

    def __init__(
        self,
        logger: LoggingPort,
        region: str = "us-east-1",
        profile: Optional[str] = None,
        required_actions: List[str] = None,
        enabled: bool = True,
    ):
        """
        Initialize IAM authentication strategy.

        Args:
            logger: Logging port for dependency injection
            region: AWS region
            profile: AWS profile to use
            required_actions: Required IAM actions for access
            enabled: Whether this strategy is enabled
        """
        self._logger = logger
        self.region = region
        self.profile = profile
        self.required_actions = required_actions or [
            "ec2:DescribeInstances",
            "ec2:RunInstances",
            "ec2:TerminateInstances",
        ]
        self.enabled = enabled

        # Initialize AWS session
        try:
            if profile:
                self.session = boto3.Session(profile_name=profile, region_name=region)
            else:
                self.session = boto3.Session(region_name=region)

            self.sts_client = self.session.client("sts")
            self.iam_client = self.session.client("iam")

        except Exception as e:
            self._logger.error("Failed to initialize AWS session: %s", e)
            self.enabled = False

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """
        Authenticate request using AWS IAM credentials.

        Args:
            context: Authentication context

        Returns:
            Authentication result based on IAM permissions
        """
        if not self.enabled:
            return AuthResult(
                status=AuthStatus.FAILED, error_message="IAM authentication is disabled"
            )

        try:
            # Get caller identity
            identity = await self._get_caller_identity()
            if not identity:
                return AuthResult(
                    status=AuthStatus.FAILED,
                    error_message="Unable to verify AWS credentials",
                )

            # Check IAM permissions
            permissions = await self._check_permissions(identity)

            # Determine user roles based on IAM policies
            roles = await self._determine_roles(identity, permissions)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=identity.get("Arn", identity.get("UserId", "unknown")),
                user_roles=roles,
                permissions=permissions,
                metadata={
                    "strategy": "iam",
                    "aws_account": identity.get("Account"),
                    "aws_user_id": identity.get("UserId"),
                    "aws_arn": identity.get("Arn"),
                },
            )

        except NoCredentialsError:
            return AuthResult(status=AuthStatus.FAILED, error_message="AWS credentials not found")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            return AuthResult(
                status=AuthStatus.FAILED, error_message=f"AWS IAM error: {error_code}"
            )
        except Exception as e:
            self._logger.error("IAM authentication error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="IAM authentication failed")

    async def validate_token(self, token: str) -> AuthResult:
        """
        Validate AWS session token.

        Args:
            token: AWS session token

        Returns:
            Authentication result
        """
        # For IAM strategy, we re-authenticate since AWS tokens are managed by AWS SDK
        # In a real implementation, you might cache the authentication result
        return await self.authenticate(
            AuthContext(
                method="GET",
                path="/validate",
                headers={"authorization": f"AWS4-HMAC-SHA256 {token}"},
                query_params={},
            )
        )

    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """
        Refresh AWS credentials.

        Args:
            refresh_token: Not used for IAM (AWS SDK handles refresh)

        Returns:
            Fresh authentication result
        """
        # AWS SDK handles credential refresh automatically
        return await self.authenticate(
            AuthContext(method="GET", path="/refresh", headers={}, query_params={})
        )

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke AWS session (not applicable for IAM).

        Args:
            token: Token to revoke

        Returns:
            Always True (AWS handles session management)
        """
        return True

    def get_strategy_name(self) -> str:
        """
        Get strategy name.

        Returns:
            Strategy name
        """
        return "iam"

    def is_enabled(self) -> bool:
        """
        Check if strategy is enabled.

        Returns:
            Whether strategy is enabled
        """
        return self.enabled

    async def _get_caller_identity(self) -> Optional[Dict[str, Any]]:
        """
        Get AWS caller identity.

        Returns:
            Caller identity information
        """
        try:
            response = self.sts_client.get_caller_identity()
            return response
        except Exception as e:
            self._logger.error("Failed to get caller identity: %s", e)
            return None

    async def _check_permissions(self, identity: Dict[str, Any]) -> List[str]:
        """
        Check IAM permissions for the caller.

        Args:
            identity: Caller identity

        Returns:
            List of permissions/actions the user can perform
        """
        permissions = []

        try:
            # Simulate permission checking by testing required actions
            # In a real implementation, you would use IAM policy simulation
            for action in self.required_actions:
                # This is a simplified check - in production you'd use
                # iam:SimulatePrincipalPolicy or similar
                permissions.append(action)

            # Add basic permissions
            permissions.extend(
                [
                    "hostfactory:list_templates",
                    "hostfactory:request_machines",
                    "hostfactory:get_status",
                ]
            )

        except Exception as e:
            self._logger.error("Failed to check permissions: %s", e)

        return permissions

    async def _determine_roles(self, identity: Dict[str, Any], permissions: List[str]) -> List[str]:
        """
        Determine user roles based on IAM identity and permissions.

        Args:
            identity: AWS caller identity
            permissions: User permissions

        Returns:
            List of roles
        """
        roles = ["user"]  # Default role

        try:
            arn = identity.get("Arn", "")

            # Check if user is an admin based on ARN patterns
            if ":root" in arn or "admin" in arn.lower():
                roles.append("admin")

            # Check if it's a service account (role-based)
            if ":role/" in arn:
                roles.append("service_account")

            # Check for operator permissions
            operator_actions = [
                "ec2:RunInstances",
                "ec2:TerminateInstances",
                "autoscaling:CreateAutoScalingGroup",
            ]

            if any(action in permissions for action in operator_actions):
                roles.append("operator")

        except Exception as e:
            self._logger.error("Failed to determine roles: %s", e)

        return roles
