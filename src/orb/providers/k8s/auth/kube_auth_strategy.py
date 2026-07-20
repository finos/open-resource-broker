"""Kubernetes inbound HTTP authentication strategy.

Validates caller Kubernetes ServiceAccount JWTs via the k8s
``authentication.k8s.io/v1 TokenReview`` API, then maps the
authenticated ServiceAccount (namespace:name) to an ORB role.

Design rationale — TokenReview vs JWKS
=======================================

AWS Cognito uses JWKS validation: it fetches the IdP's ``/.well-known/
jwks.json`` endpoint, caches the public keys, and validates the JWT
signature locally.

For Kubernetes ServiceAccount tokens there are two equivalent options:

1. **JWKS** — fetch ``/.well-known/openid-configuration`` from the API
   server's OIDC issuer, get the JWKS URI, cache the keys, and validate
   the projected SA JWT signature locally.  This works but requires ORB
   to know the issuer URL and to handle key rotation; it also means ORB
   is performing cryptographic validation that the API server already
   does authoritatively.

2. **TokenReview** — submit the raw token to the Kubernetes
   ``authentication.k8s.io/v1 TokenReview`` API.  The API server
   validates the token against its own signing keys, checks expiry, and
   returns the authenticated ServiceAccount principal (or a failure) in
   a single round-trip.  TokenReview is the k8s-native equivalent of
   Cognito JWKS validation and is the approach endorsed by the k8s
   auth documentation for in-cluster token delegation.

**TokenReview is preferred here** because:

- No need to manage JWKS caching or key-rotation windows.
- The API server is the authoritative signer — there is no possibility
  of a local validation accepting a token the cluster itself would
  reject.
- Requires only a ``system:auth-delegator`` ClusterRoleBinding (or a
  targeted ``tokenreviews: create`` RBAC grant) for the ORB pod's
  ServiceAccount — a narrow, auditable privilege.
- JWKS would require ORB to know the issuer URL (cluster-specific
  config) and to refresh keys on rotation; TokenReview is self-
  contained.

Role mapping
============

The authenticated principal is ``<namespace>:<serviceaccount>``.  A
static mapping dict (``sa_role_mapping``) maps each principal to one or
more ORB roles; principals not in the map receive the ``["user"]``
default.  Operators can override the mapping via
``K8sInboundAuthConfig.sa_role_mapping`` in ``config.json``.

The mapping mirrors Cognito's ``group → role`` table in
``cognito_strategy.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from orb.infrastructure.di.injectable import injectable

if TYPE_CHECKING:
    pass


@injectable
class KubeAuthStrategy(AuthPort):
    """Inbound HTTP auth strategy for Kubernetes-deployed ORB.

    Validates Bearer tokens via the Kubernetes ``TokenReview`` API
    (``authentication.k8s.io/v1``) and maps the authenticated
    ServiceAccount to an ORB role.

    Args:
        logger: Injected logging port.
        kubernetes_client: A :class:`~orb.providers.k8s.infrastructure.k8s_client.K8sClient`
            instance.  When ``None`` the strategy attempts to construct
            one via ``in_cluster`` config on first use.
        sa_role_mapping: Dict mapping ``"<namespace>:<sa-name>"`` → list
            of ORB role strings.  Principals absent from the map receive
            ``["user"]``.
        audiences: Optional list of token audiences to restrict
            validation scope.  ``None`` (default) means the API server
            accepts any audience it considers valid for the token.
        enabled: When ``False`` every authenticate call returns FAILED
            immediately (allows config-gated disablement).
    """

    def __init__(
        self,
        logger: LoggingPort,
        kubernetes_client: Optional[Any] = None,
        sa_role_mapping: Optional[dict[str, list[str]]] = None,
        audiences: Optional[list[str]] = None,
        enabled: bool = True,
    ) -> None:
        self._logger = logger
        self._kubernetes_client = kubernetes_client
        self._sa_role_mapping: dict[str, list[str]] = sa_role_mapping or {}
        self._audiences = audiences
        self.enabled = enabled

    # ------------------------------------------------------------------
    # AuthPort interface
    # ------------------------------------------------------------------

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """Authenticate an inbound request using a Kubernetes SA token.

        Extracts the ``Bearer`` token from the ``Authorization`` header
        and delegates to :meth:`validate_token`.
        """
        if not self.enabled:
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Kubernetes inbound auth is disabled",
            )

        auth_header = context.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Missing or invalid Authorization header",
            )

        token = auth_header[7:]  # Strip "Bearer " prefix
        # Fail-closed: reject empty or whitespace-only tokens locally to avoid
        # issuing a TokenReview for a clearly invalid credential.  The API server
        # would reject such a request too, but catching it here avoids a round-trip
        # and prevents accidentally logging an empty token in downstream error paths.
        if not token or not token.strip():
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Bearer token is empty; authentication rejected",
            )
        return await self.validate_token(token)

    async def validate_token(self, token: str) -> AuthResult:
        """Validate a ServiceAccount JWT via the Kubernetes TokenReview API.

        Submits the token to ``authentication.k8s.io/v1 TokenReview``.
        On success, maps the ``namespace:serviceaccount`` principal to
        ORB roles and returns an :class:`AuthResult` with
        ``status=SUCCESS``.

        The blocking ``create_token_review`` SDK call is offloaded via
        ``asyncio.to_thread`` to avoid blocking the event loop.
        """
        try:
            result = await asyncio.to_thread(self._do_token_review, token)
            return result
        except Exception as exc:
            self._logger.error(
                "TokenReview API error during token validation: %s", exc, exc_info=True
            )
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="TokenReview API error; token validation failed",
            )

    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """Not applicable for SA tokens — SA tokens are projected and cannot be refreshed."""
        return AuthResult(
            status=AuthStatus.FAILED,
            error_message="ServiceAccount token refresh is not supported; obtain a new projected token",
        )

    async def revoke_token(self, token: str) -> bool:
        """Not directly applicable — token lifecycle is managed by Kubernetes.

        Returns ``True`` (no-op) because SA token revocation is handled
        by deleting or rotating the ServiceAccount in the cluster, not by
        an ORB API call.
        """
        self._logger.debug(
            "KubeAuthStrategy: token revocation is a no-op "
            "(ServiceAccount token lifecycle is managed by Kubernetes)"
        )
        return True

    def get_strategy_name(self) -> str:
        """Return the strategy identifier registered in ``AuthRegistry``."""
        return "kubernetes"

    def is_enabled(self) -> bool:
        """Return whether this strategy is active."""
        return self.enabled

    # ------------------------------------------------------------------
    # Factory classmethod — mirrors CognitoAuthStrategy.from_auth_config
    # ------------------------------------------------------------------

    @classmethod
    def from_auth_config(cls, auth_config: Any) -> KubeAuthStrategy:
        """Build a :class:`KubeAuthStrategy` from an ``AuthConfig`` instance.

        Reads ``auth_config.provider_auth.kubernetes`` (or falls back to
        safe defaults when absent).  The DI container is consulted
        opportunistically for a ``LoggingPort``; a plain ``LoggingAdapter``
        is used when the container is not yet initialised.

        This is an intentional service-locator pattern, matching the same
        approach used in :meth:`CognitoAuthStrategy.from_auth_config` —
        the ``AuthRegistry`` calls all strategy factories via
        ``strategy_factory.from_auth_config(auth_config)`` with a fixed
        signature that does not thread a logger through.
        """
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

        provider_auth = getattr(auth_config, "provider_auth", None)
        k8s_auth_cfg = (
            getattr(provider_auth, "kubernetes", None) if provider_auth is not None else None
        )

        sa_role_mapping: dict[str, list[str]] = (
            getattr(k8s_auth_cfg, "sa_role_mapping", {}) if k8s_auth_cfg is not None else {}
        )
        audiences: Optional[list[str]] = (
            getattr(k8s_auth_cfg, "audiences", None) if k8s_auth_cfg is not None else None
        )

        # Intentional service-locator: fixed factory signature prevents
        # threading a logger argument through.
        try:
            from orb.domain.base.ports import LoggingPort
            from orb.infrastructure.di.container import get_container

            logger: LoggingPort = get_container().get(LoggingPort)
        except Exception:
            logger = LoggingAdapter()  # type: ignore[assignment]

        # Resolve the K8sClient from the DI container when available.
        # Falls back to None; the strategy will construct an in-cluster
        # client on first use.
        kubernetes_client = None
        try:
            from orb.infrastructure.di.container import get_container
            from orb.providers.k8s.infrastructure.k8s_client import K8sClient

            kubernetes_client = get_container().get(K8sClient)
        except Exception:
            pass  # type: ignore[return]

        # Respect the per-instance enabled flag from config rather than hardcoding
        # True.  The registration-gate (inbound_auth_enabled=False → strategy never
        # registered) is the primary control, but honouring the per-instance flag
        # gives operators defense-in-depth and a consistent is_enabled() signal.
        enabled: bool = getattr(k8s_auth_cfg, "enabled", True) if k8s_auth_cfg is not None else True
        return cls(
            logger=logger,
            kubernetes_client=kubernetes_client,
            sa_role_mapping=sa_role_mapping,
            audiences=audiences,
            enabled=enabled,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_k8s_client(self) -> Any:
        """Return the K8sClient, constructing an in-cluster one if needed."""
        if self._kubernetes_client is not None:
            return self._kubernetes_client

        from orb.providers.k8s.auth.in_cluster import load_in_cluster_config
        from orb.providers.k8s.configuration.config import K8sProviderConfig
        from orb.providers.k8s.infrastructure.k8s_client import K8sClient

        load_in_cluster_config()
        self._kubernetes_client = K8sClient(
            config=K8sProviderConfig(in_cluster=True),  # type: ignore[call-arg]
            logger=self._logger,
        )
        return self._kubernetes_client

    def _do_token_review(self, token: str) -> AuthResult:
        """Execute the blocking TokenReview API call.

        This method is called inside ``asyncio.to_thread`` so the event
        loop is not blocked by the synchronous kubernetes SDK call.

        Returns an :class:`AuthResult` directly so the thread boundary is
        clean — no coroutines needed inside the thread.
        """
        try:
            from kubernetes.client import (  # type: ignore[import-untyped]
                AuthenticationV1Api,
                V1TokenReview,
                V1TokenReviewSpec,
            )

            client = self._get_k8s_client()
            auth_api = AuthenticationV1Api(client.api_client)

            spec_kwargs: dict[str, Any] = {"token": token}
            if self._audiences is not None:
                spec_kwargs["audiences"] = self._audiences

            review = V1TokenReview(spec=V1TokenReviewSpec(**spec_kwargs))
            response: Any = auth_api.create_token_review(review)
            status = getattr(response, "status", None)

            if status is None or not getattr(status, "authenticated", False):
                error = (
                    getattr(status, "error", "Token not authenticated")
                    if status
                    else "Empty TokenReview status"
                )
                self._logger.debug("KubeAuthStrategy: TokenReview rejected — %s", error)
                return AuthResult(
                    status=AuthStatus.INVALID,
                    error_message=f"Token rejected by Kubernetes API server: {error}",
                )

            # Authenticated — extract the ServiceAccount user info.
            # The ``user`` field has the form:
            #   username: "system:serviceaccount:<namespace>:<name>"
            #   groups:   ["system:serviceaccounts", "system:serviceaccounts:<ns>", ...]
            user_info = getattr(status, "user", None)
            username: str = getattr(user_info, "username", "") if user_info is not None else ""
            user_uid: str = getattr(user_info, "uid", "") if user_info is not None else ""

            # Normalise the principal to "namespace:name" for role lookup.
            principal = self._extract_sa_principal(username)
            roles = self._map_principal_to_roles(principal)
            permissions = self._generate_permissions(roles)

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=user_uid or username,
                user_roles=roles,
                permissions=permissions,
                metadata={
                    "strategy": "kubernetes",
                    "username": username,
                    "principal": principal,
                },
            )

        except Exception as exc:
            # Re-raise so the caller's asyncio.to_thread handler logs it.
            raise RuntimeError(f"TokenReview call failed: {exc}") from exc

    @staticmethod
    def _extract_sa_principal(username: str) -> str:
        """Extract ``"namespace:name"`` from a Kubernetes SA username.

        Kubernetes encodes ServiceAccount usernames as::

            system:serviceaccount:<namespace>:<name>

        This method strips the ``"system:serviceaccount:"`` prefix so the
        remaining ``"<namespace>:<name>"`` string can be used as a role-
        mapping key.

        For non-SA usernames (e.g. human users in ``system:masters``) the
        full username is returned unchanged.

        Args:
            username: The ``user.username`` field from a TokenReview response.

        Returns:
            ``"<namespace>:<sa-name>"`` or the original username string.
        """
        _SA_PREFIX = "system:serviceaccount:"
        if username.startswith(_SA_PREFIX):
            return username[len(_SA_PREFIX) :]
        return username

    def _map_principal_to_roles(self, principal: str) -> list[str]:
        """Map an SA principal to ORB roles.

        Checks the operator-supplied ``sa_role_mapping`` for an exact
        match of ``"<namespace>:<sa-name>"``.  Falls back to a wildcard
        namespace lookup (``"*:<sa-name>"``) and then to ``["user"]``.

        Args:
            principal: ``"<namespace>:<sa-name>"`` or raw username.

        Returns:
            Non-empty list of ORB role strings.
        """
        # Exact match.
        if principal in self._sa_role_mapping:
            return list(self._sa_role_mapping[principal])

        # Wildcard-namespace match: "*:<sa-name>".
        if ":" in principal:
            _ns, sa_name = principal.split(":", 1)
            wildcard_key = f"*:{sa_name}"
            if wildcard_key in self._sa_role_mapping:
                return list(self._sa_role_mapping[wildcard_key])

        return ["user"]

    def _generate_permissions(self, roles: list[str]) -> list[str]:
        """Derive ORB permissions from role list.

        Mirrors :meth:`CognitoAuthStrategy._generate_permissions`.
        """
        permissions: list[str] = ["hostfactory:list_templates", "hostfactory:get_status"]

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

        return list(set(permissions))
