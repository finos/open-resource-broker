"""AWS IAM authentication strategy."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any, Optional

from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from orb.infrastructure.di.injectable import injectable
from orb.providers.aws.session_factory import AWSSessionFactory

if TYPE_CHECKING:
    pass

_DEFAULT_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 3},
)


@injectable
class IAMAuthStrategy(AuthPort):
    """Authentication strategy using AWS IAM credentials and policies."""

    _DEFAULT_ADMIN_ROLE_PATTERNS: frozenset[str] = frozenset({"Admin", "Administrator", "OrbAdmin"})

    def __init__(
        self,
        logger: LoggingPort,
        region: str = "us-east-1",
        profile: Optional[str] = None,
        required_actions: Optional[list[str]] = None,
        enabled: bool = True,
        assume_permissions: bool = False,
        admin_role_patterns: Optional[frozenset[str]] = None,
        admin_arns: Optional[list[str]] = None,
    ) -> None:
        """
        Initialize IAM authentication strategy.

        Args:
            logger: Logging port for dependency injection
            region: AWS region
            profile: AWS profile to use
            required_actions: Required IAM actions for access
            enabled: Whether this strategy is enabled
            assume_permissions: If True, grant all required_actions without evaluation.
                Only use in development/testing. Defaults to False (deny-all).
            admin_role_patterns: Frozenset of resource-name strings that trigger the
                admin role (matched by exact set-membership against the last ARN path
                segment).  Ignored when ``admin_arns`` is non-empty.
            admin_arns: Explicit allowlist of fully-qualified ARNs that receive the
                admin role.  Matched by exact equality after lowercasing both sides.
                When non-empty, ``admin_role_patterns`` is bypassed for admin checks.
        """
        self._logger = logger
        self._admin_role_patterns = admin_role_patterns or self._DEFAULT_ADMIN_ROLE_PATTERNS
        # Normalise to lowercase frozenset for O(1) exact-match lookup.
        # The only operation performed is `caller_arn_lower in self._admin_arns`,
        # which is a set __contains__ check — never a substring search.
        self._admin_arns: frozenset[str] = (
            frozenset(a.lower() for a in admin_arns) if admin_arns else frozenset()
        )
        self.region = region
        self.profile = profile
        # None means "operator did not configure required_actions" → apply defaults.
        # [] means "operator explicitly set required_actions to empty" → honour it (no actions required).
        if required_actions is None:
            self.required_actions: list[str] = [
                "ec2:DescribeInstances",
                "ec2:RunInstances",
                "ec2:TerminateInstances",
            ]
        else:
            self.required_actions = list(required_actions)
        self.enabled = enabled

        # assume_permissions is a dev-only escape hatch. It is only honoured when
        # the operator has explicitly set ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY=true
        # in the environment.  Without that env-var the flag is silently disabled
        # so that a misconfigured production deployment cannot accidentally bypass
        # real IAM evaluation.
        _dev_env_var = os.environ.get("ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY", "").lower()
        _dev_override_active = _dev_env_var == "true"

        if assume_permissions and not _dev_override_active:
            self._logger.critical(
                "IAM assume_permissions=True is set in config but "
                "ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY env var is not 'true'. "
                "Treating as assume_permissions=False to prevent privilege bypass in production. "
                "Set ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY=true only in non-production environments."
            )
            self._assume_permissions = False
        else:
            self._assume_permissions = assume_permissions

        if self._assume_permissions:
            self._logger.critical(
                "IAM assume_permissions=True is ACTIVE (ORB_IAM_ASSUME_PERMISSIONS_DEV_ONLY=true). "
                "All required_actions are granted without AWS evaluation. "
                "This MUST NOT be used in production."
            )

        # Initialize AWS session
        try:
            self.session = AWSSessionFactory.create_session(profile, region)
            self.sts_client = self.session.client("sts", config=_DEFAULT_CONFIG)
            self.iam_client = self.session.client("iam", config=_DEFAULT_CONFIG)

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

    @classmethod
    def from_auth_config(cls, auth_config: Any) -> IAMAuthStrategy:
        """
        Build strategy instance from AuthConfig.

        Extracts the provider_auth.iam sub-config and constructs an IAMAuthStrategy.
        A LoggingPort is obtained from the DI container when available; otherwise a
        plain logging adapter is used so the classmethod stays self-contained.

        Args:
            auth_config: AuthConfig instance with optional provider_auth.iam sub-config

        Returns:
            Configured IAMAuthStrategy
        """
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

        provider_auth = getattr(auth_config, "provider_auth", None)
        iam_cfg = getattr(provider_auth, "iam", None) if provider_auth is not None else None

        region: str = (
            getattr(iam_cfg, "region", "us-east-1") if iam_cfg is not None else "us-east-1"
        )
        profile: Optional[str] = getattr(iam_cfg, "profile", None) if iam_cfg is not None else None
        required_actions: list[str] = (
            getattr(iam_cfg, "required_actions", []) if iam_cfg is not None else []
        )
        assume_permissions: bool = (
            getattr(iam_cfg, "assume_permissions", False) if iam_cfg is not None else False
        )
        admin_arns: list[str] = getattr(iam_cfg, "admin_arns", []) if iam_cfg is not None else []

        # Intentional service-locator: from_auth_config is called by the
        # AuthRegistry as ``strategy_factory.from_auth_config(auth_config)``
        # with a fixed signature — no logger parameter can be threaded through
        # without changing the AuthRegistry protocol (a broad, cross-cutting
        # change).  The DI container is therefore queried here as a best-effort
        # bootstrap; a plain LoggingAdapter fallback ensures the classmethod
        # remains self-contained when the container is not yet initialised.
        try:
            from orb.domain.base.ports import LoggingPort
            from orb.infrastructure.di.container import get_container

            logger: LoggingPort = get_container().get(LoggingPort)
        except Exception:
            logger = LoggingAdapter()  # type: ignore[assignment]

        return cls(
            logger=logger,
            region=region,
            profile=profile,
            required_actions=required_actions,
            enabled=True,
            assume_permissions=assume_permissions,
            admin_arns=admin_arns,
        )

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

    async def _get_caller_identity(self) -> Optional[dict[str, Any]]:
        """
        Get AWS caller identity.

        The underlying boto3 call is executed via ``asyncio.to_thread`` so the
        event loop is not blocked during the synchronous network round-trip.

        Returns:
            Caller identity information
        """
        try:
            response: dict[str, Any] = await asyncio.to_thread(self.sts_client.get_caller_identity)
            return response
        except Exception as e:
            self._logger.error("Failed to get caller identity: %s", e)
            return None

    async def _check_permissions(self, identity: dict[str, Any]) -> list[str]:
        """
        Check IAM permissions for the caller using SimulatePrincipalPolicy.

        The caller's ARN (from ``identity``) is used as the policy-source ARN.
        All ``required_actions`` are evaluated in a single API call.  Only
        actions whose ``EvalDecision`` is ``"allowed"`` are returned; explicit-
        deny, implicit-deny, or any unknown decision are treated as denied.

        On any error (API failure, missing permissions to call IAM, network
        timeout, etc.) the method returns an empty list — fail secure.

        When the ``assume_permissions`` dev-only flag is active the evaluation
        is bypassed and all ``required_actions`` plus the hostfactory actions
        are returned unconditionally.

        Args:
            identity: Caller identity dict (must contain ``"Arn"``).

        Returns:
            List of IAM actions the caller is allowed to perform.
        """
        if self._assume_permissions:
            permissions = list(self.required_actions)
            permissions.extend(
                [
                    "hostfactory:list_templates",
                    "hostfactory:request_machines",
                    "hostfactory:get_status",
                ]
            )
            return permissions

        caller_arn = identity.get("Arn", "")
        if not caller_arn:
            self._logger.warning("Cannot evaluate IAM permissions: caller ARN is empty.")
            return []

        actions_to_check = list(self.required_actions)

        # ResourceArns is not passed here, so the simulation evaluates against
        # the wildcard resource ("*").  This means resource-scoped Condition
        # keys and resource-level denies in IAM policies are NOT evaluated,
        # which can produce an over-grant for resource-restricted policies
        # (e.g. ``ec2:TerminateInstances`` scoped to specific instance IDs).
        # Passing concrete ResourceArns would require threading request-context
        # resource identifiers through the auth layer — a broad refactor deferred
        # to a future enhancement.  The current behaviour is intentional and
        # documented here so callers understand the limitation.

        granted: list[str] = []
        try:
            # Paginate through all EvaluationResults pages (AWS returns ≤100
            # results per page and paginates via IsTruncated/Marker).  Using
            # the boto3 paginator ensures every page is consumed before a
            # permission decision is made, preventing silent over-grant or
            # wrong-deny when >100 actions are evaluated.
            paginator = self.iam_client.get_paginator("simulate_principal_policy")

            def _paginate() -> list[dict[str, Any]]:
                pages: list[dict[str, Any]] = []
                for page in paginator.paginate(
                    PolicySourceArn=caller_arn,
                    ActionNames=actions_to_check,
                ):
                    pages.extend(page.get("EvaluationResults", []))
                return pages

            all_results: list[dict[str, Any]] = await asyncio.to_thread(_paginate)

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            self._logger.error(
                "SimulatePrincipalPolicy failed (ClientError %s); denying all permissions.",
                error_code,
            )
            return []
        except Exception as exc:
            # Log only the exception type to avoid embedding caller ARN or
            # action lists (present in botocore exception messages) in log output.
            self._logger.error(
                "SimulatePrincipalPolicy raised an unexpected error (%s); denying all permissions.",
                type(exc).__name__,
            )
            return []

        for result in all_results:
            action = result.get("EvalActionName", "")
            decision = result.get("EvalDecision", "")
            if decision == "allowed":
                granted.append(action)

        return granted

    async def _determine_roles(self, identity: dict[str, Any], permissions: list[str]) -> list[str]:
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

            # Determine admin status.
            #
            # When an explicit ARN allowlist is configured, use it exclusively and
            # require an exact match (case-normalised).  This prevents two classes
            # of bypass:
            #   1. Substring bypass — `if admin_arn in caller_arn` would pass for
            #      any caller whose serialised ARN contains the target as a substring
            #      (e.g. `arn:aws:iam::EVIL:role/arn:aws:iam::ADMIN:role/OrbAdmin`).
            #   2. Cross-account bypass — the legacy name-pattern check only inspects
            #      the resource segment (e.g. "Admin") and does not verify the account
            #      ID, so a principal named "Admin" in any account would match.
            #
            # The frozenset.__contains__ check below is an exact equality test, not
            # a substring search.  Both sides are lowercased so the comparison is
            # case-insensitive (IAM ARN account/service fields are case-insensitive;
            # resource names are case-sensitive but normalising avoids misconfiguration).
            if self._admin_arns:
                # Explicit allowlist path — exact match only.
                # When admin_arns is configured, it is the COMPLETE set of admin
                # principals.  The unconditional :root grant does NOT apply here;
                # include the root ARN explicitly in admin_arns if root must have
                # admin access.  This prevents a cross-account bypass where any
                # :root credential from any AWS account would otherwise be granted
                # admin regardless of the configured allowlist.
                caller_arn_lower = arn.lower()
                if caller_arn_lower in self._admin_arns:
                    roles.append("admin")
            else:
                # Legacy name-pattern path — kept for backward compatibility when no
                # explicit admin_arns allowlist is configured.
                resource_name = arn.split("/")[-1] if "/" in arn else ""
                if arn.endswith(":root") or resource_name in self._admin_role_patterns:
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
