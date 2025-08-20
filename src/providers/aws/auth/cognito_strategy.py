"""AWS Cognito authentication strategy."""

from typing import List, Optional

import boto3
import jwt
from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)


@injectable
class CognitoAuthStrategy(AuthPort):
    """Authentication strategy using AWS Cognito User Pools."""

    def __init__(
        self,
        logger: LoggingPort,
        user_pool_id: str,
        client_id: str,
        region: str = "us-east-1",
        jwks_url: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        """
        Initialize Cognito authentication strategy.

        Args:
            user_pool_id: Cognito User Pool ID
            client_id: Cognito App Client ID
            region: AWS region
            jwks_url: JWKS URL for token verification (auto-generated if not provided)
            enabled: Whether this strategy is enabled
        """
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.region = region
        self.enabled = enabled
        self._logger = logger

        # Generate JWKS URL if not provided
        if jwks_url:
            self.jwks_url = jwks_url
        else:
            self.jwks_url = (
                f"https://cognito-idp.{region}.amazonaws.com/"
                f"{user_pool_id}/.well-known/jwks.json"
            )

        # Initialize Cognito client
        try:
            self.cognito_client = boto3.client("cognito-idp", region_name=region)
        except Exception as e:
            self._logger.error("Failed to initialize Cognito client: %s", e)
            self.enabled = False

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """
        Authenticate request using Cognito JWT token.

        Args:
            context: Authentication context with Authorization header

        Returns:
            Authentication result based on Cognito token
        """
        if not self.enabled:
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Cognito authentication is disabled",
            )

        # Extract Bearer token from Authorization header
        auth_header = context.headers.get("authorization", "")

        if not auth_header.startswith("Bearer "):
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Missing or invalid Authorization header",
            )

        token = auth_header[7:]  # Remove "Bearer " prefix
        return await self.validate_token(token)

    async def validate_token(self, token: str) -> AuthResult:
        """
        Validate Cognito JWT token.

        Args:
            token: Cognito JWT token

        Returns:
            Authentication result with user information from Cognito
        """
        try:
            # Decode token without verification first to get header
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            if not kid:
                return AuthResult(status=AuthStatus.INVALID, error_message="Token missing key ID")

            # Get public key from JWKS (simplified - in production, cache this)
            public_key = await self._get_public_key(kid)
            if not public_key:
                return AuthResult(
                    status=AuthStatus.INVALID,
                    error_message="Unable to verify token signature",
                )

            # Verify and decode token
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}",
            )

            # Extract user information
            user_id = payload.get("sub")
            username = payload.get("cognito:username", payload.get("username"))
            email = payload.get("email")
            groups = payload.get("cognito:groups", [])

            # Map Cognito groups to roles
            roles = self._map_groups_to_roles(groups)

            # Generate permissions based on roles
            permissions = self._generate_permissions(roles)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=user_id,
                user_roles=roles,
                permissions=permissions,
                token=token,
                expires_at=payload.get("exp"),
                metadata={
                    "strategy": "cognito",
                    "username": username,
                    "email": email,
                    "cognito_groups": groups,
                    "token_use": payload.get("token_use"),
                    "client_id": payload.get("aud"),
                },
            )

        except jwt.ExpiredSignatureError:
            return AuthResult(status=AuthStatus.EXPIRED, error_message="Token has expired")
        except jwt.InvalidTokenError as e:
            return AuthResult(status=AuthStatus.INVALID, error_message=f"Invalid token: {str(e)}")
        except Exception as e:
            self._logger.error("Cognito token validation error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="Token validation failed")

    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """
        Refresh Cognito access token.

        Args:
            refresh_token: Cognito refresh token

        Returns:
            New authentication result with fresh token
        """
        try:
            response = self.cognito_client.initiate_auth(
                ClientId=self.client_id,
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters={"REFRESH_TOKEN": refresh_token},
            )

            auth_result = response.get("AuthenticationResult", {})
            new_access_token = auth_result.get("AccessToken")

            if not new_access_token:
                return AuthResult(status=AuthStatus.FAILED, error_message="Failed to refresh token")

            # Validate the new token to get user info
            return await self.validate_token(new_access_token)

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message=f"Cognito refresh error: {error_code}",
            )
        except Exception as e:
            self._logger.error("Token refresh error: %s", e)
            return AuthResult(status=AuthStatus.FAILED, error_message="Token refresh failed")

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke Cognito token.

        Args:
            token: Token to revoke

        Returns:
            True if token was revoked successfully
        """
        try:
            # Cognito doesn't have a direct revoke endpoint for access tokens
            # You would typically revoke the refresh token or sign out the user
            # This is a simplified implementation
            self._logger.info(
                "Token revocation requested (Cognito access tokens expire automatically)"
            )
            return True

        except Exception as e:
            self._logger.error("Token revocation error: %s", e)
            return False

    def get_strategy_name(self) -> str:
        """
        Get strategy name.

        Returns:
            Strategy name
        """
        return "cognito"

    def is_enabled(self) -> bool:
        """
        Check if strategy is enabled.

        Returns:
            Whether strategy is enabled
        """
        return self.enabled

    async def _get_public_key(self, kid: str) -> Optional[str]:
        """
        Get public key from Cognito JWKS endpoint.

        Args:
            kid: Key ID from token header

        Returns:
            Public key for token verification
        """
        try:
            # In production, you would cache JWKS and implement appropriate key rotation
            # This is a simplified implementation
            import requests

            # Add timeout to prevent hanging connections (security best practice)
            response = requests.get(self.jwks_url, timeout=30)
            jwks = response.json()

            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    # Convert JWK to PEM format (simplified)
                    # In production, use a appropriate JWK library
                    return key  # Return the key dict for now

            return None

        except Exception as e:
            self._logger.error("Failed to get public key: %s", e)
            return None

    def _map_groups_to_roles(self, groups: List[str]) -> List[str]:
        """
        Map Cognito groups to application roles.

        Args:
            groups: Cognito user groups

        Returns:
            Application roles
        """
        roles = ["user"]  # Default role

        group_role_mapping = {
            "admin": "admin",
            "administrators": "admin",
            "operators": "operator",
            "viewers": "viewer",
            "service-accounts": "service_account",
        }

        for group in groups:
            role = group_role_mapping.get(group.lower())
            if role and role not in roles:
                roles.append(role)

        return roles

    def _generate_permissions(self, roles: List[str]) -> List[str]:
        """
        Generate permissions based on roles.

        Args:
            roles: User roles

        Returns:
            List of permissions
        """
        permissions = []

        # Base permissions for all users
        permissions.extend(["hostfactory:list_templates", "hostfactory:get_status"])

        # Role-based permissions
        if "admin" in roles:
            permissions.extend(["hostfactory:*", "system:*"])
        elif "operator" in roles:
            permissions.extend(
                [
                    "hostfactory:request_machines",
                    "hostfactory:return_machines",
                    "hostfactory:manage_requests",
                ]
            )
        elif "viewer" in roles:
            permissions.extend(["hostfactory:view_*"])

        return list(set(permissions))  # Remove duplicates
