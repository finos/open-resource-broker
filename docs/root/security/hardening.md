# Security Hardening Reference

This page describes the security controls built into ORB, addressing OWASP Top 10 risk categories with defense-in-depth measures. It is a factual reference, not a vulnerability disclosure policy — to report a security vulnerability, see [SECURITY.md](https://github.com/finos/open-resource-broker/blob/main/SECURITY.md).

## Implemented Security Controls

### 1. JWT Token Denylist (OWASP A02: Cryptographic Failures)

**Location:** `src/orb/infrastructure/auth/token_denylist/`

- Token revocation support for secure logout
- Redis-backed denylist (`RedisTokenDenylist`) with automatic expiration, for distributed deployments
- In-memory implementation (`InMemoryTokenDenylist`) for single-process and development use
- Automatic cleanup of expired entries
- Async interface defined by `TokenDenylistPort`

**Usage:**
```python
from orb.infrastructure.auth.token_denylist import InMemoryTokenDenylist

denylist = InMemoryTokenDenylist()
await denylist.add_token(token, expires_at)
is_denylisted = await denylist.is_denylisted(token)
```

### 2. Enhanced Bearer Token Strategy (OWASP A07: Authentication Failures)

**Location:** `src/orb/infrastructure/auth/strategy/bearer_token_strategy_enhanced.py`

- JWT validation with denylist checking
- Per-key rate limiting on validation attempts (configurable attempts and window)
- Secret key strength validation (minimum 256 bits / 32 bytes)
- JWT signature verification and expiry/issued-at checks
- Security audit logging for authentication events

**Usage:**
```python
from orb.infrastructure.auth.strategy.bearer_token_strategy_enhanced import (
    EnhancedBearerTokenStrategy,
)
from orb.infrastructure.auth.token_denylist import InMemoryTokenDenylist

denylist = InMemoryTokenDenylist()
strategy = EnhancedBearerTokenStrategy(
    secret_key="your-256-bit-secret-key",
    denylist=denylist,
    rate_limit_enabled=True,
)
```

### 3. Input Validation Framework (OWASP A03: Injection)

**Location:** `src/orb/infrastructure/validation/`

- Input sanitization to reject dangerous characters
- Length validation with configurable limits
- Character whitelisting (alphanumeric, alphanumeric-with-dash)
- Type and choice validation
- `secure_input()` as a drop-in, validated replacement for direct `input()` calls

**Validation functions:** `sanitize_input()`, `validate_length()`, `validate_alphanumeric()`, `validate_integer()`, `validate_choice()`, `secure_input()`.

Provider-specific validation, such as AWS region format checking, lives alongside the relevant provider (for example `src/orb/providers/aws/validation/region_validator.py`) rather than in the shared validation module.

**Dangerous characters blocked:**
```
< > & | ; ` $ ( ) { } [ ] \n \r
```

**Usage:**
```python
from orb.infrastructure.validation import secure_input, validate_choice

scheduler = secure_input(
    "Scheduler type: ",
    default="default",
    validator=lambda v: validate_choice(v, ["default", "hostfactory", "slurm"]),
    max_length=50,
)
```

### 4. Authentication Middleware and Security Headers (OWASP A05: Security Misconfiguration)

**Locations:** `src/orb/api/middleware/auth_middleware.py`, `src/orb/api/middleware/security_headers_middleware.py`

`AuthMiddleware`:
- Normalizes request paths before matching against excluded paths, closing path-traversal and trailing-slash bypasses
- Exact-match (not prefix-match) exclusion list
- Structured authentication audit logging, including client IP (via trusted-proxy aware IP resolution)

`SecurityHeadersMiddleware` adds the following headers to every response:
- `X-Frame-Options: DENY` — prevent clickjacking
- `X-Content-Type-Options: nosniff` — prevent MIME sniffing
- `Strict-Transport-Security` — force HTTPS, emitted only over HTTPS connections
- `Content-Security-Policy` — restrict resource loading
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` — disable unnecessary browser features

`X-XSS-Protection` is intentionally not set: it is deprecated and ignored by modern browsers.

**Usage:**
```python
from orb.api.middleware.auth_middleware import AuthMiddleware
from orb.api.middleware.security_headers_middleware import SecurityHeadersMiddleware

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    AuthMiddleware,
    auth_port=auth_strategy,
    excluded_paths=["/health", "/docs"],
    require_auth=True,
)
```

## Security Testing

**Token denylist tests:** `tests/unit/infrastructure/auth/test_token_denylist.py`
**Input validation tests:** `tests/unit/infrastructure/validation/test_input_validator.py`, `tests/unit/infrastructure/validation/test_secure_input.py`

```bash
python -m pytest tests/unit/infrastructure/auth/ -v
python -m pytest tests/unit/infrastructure/validation/ -v
```

## OWASP Top 10 Coverage

| Category | Controls |
|---|---|
| A01: Broken Access Control | Authorization checks in middleware, role-based access control, permission validation |
| A02: Cryptographic Failures | JWT token denylist, strong secret key validation, JWT signature verification |
| A03: Injection | Input sanitization framework, character whitelisting, length limits |
| A04: Insecure Design | Defense in depth, fail-secure defaults |
| A05: Security Misconfiguration | Security headers on all responses, sanitized error messages |
| A06: Vulnerable Components | `PyJWT` for JWT handling, dependency scanning in CI |
| A07: Authentication Failures | Rate limiting on token validation, token denylist, session handling via short-lived tokens |
| A08: Software and Data Integrity Failures | JWT signature verification, token integrity checks |
| A09: Logging and Monitoring Failures | Security audit logging, authentication attempt tracking, rate-limit violation logging |
| A10: Server-Side Request Forgery | Input validation on provider-supplied URLs and endpoints |

## Configuration

### Environment Variables

```bash
# JWT configuration
ORB_JWT_SECRET_KEY="your-256-bit-secret-key-here"
ORB_JWT_ALGORITHM="HS256"
ORB_JWT_EXPIRY=3600

# Rate limiting
ORB_RATE_LIMIT_ENABLED=true
ORB_RATE_LIMIT_MAX_ATTEMPTS=10
ORB_RATE_LIMIT_WINDOW=60

# Redis (optional, for a distributed token denylist)
ORB_REDIS_URL="redis://localhost:6379"
```

### Production Recommendations

1. **Secret key management:** use at least 256-bit (32-byte) secret keys, rotate them regularly, and store them in a secrets manager (AWS Secrets Manager, HashiCorp Vault, or equivalent).
2. **Rate limiting:** enable rate limiting in production and tune limits to observed traffic.
3. **Token denylist:** use the Redis-backed denylist for multi-process or multi-host deployments.
4. **Security headers:** enable HSTS in production and tailor the Content-Security-Policy to the deployment.
5. **Logging:** monitor authentication failures and rate-limit violations, and alert on sustained abuse.

## Security Scanning

```bash
ruff check --select S --ignore S311 src/
pip-audit
semgrep --config=auto src/
```

## Incident Response

### Suspected token compromise

1. Revoke the token: `await auth_strategy.revoke_token(compromised_token)`
2. Force re-authentication for the affected user or client
3. Rotate secret keys if the compromise could have exposed them
4. Review authentication audit logs for related activity

### Rate limit violations

1. Review logs for the source of the violations
2. Investigate for an attack pattern versus legitimate burst traffic
3. Block the source at the network layer if malicious
4. Adjust rate limits if the traffic was legitimate

## References

- [OWASP Top 10](https://owasp.org/Top10/)
- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/)
- [JWT Best Practices (RFC 8725)](https://www.rfc-editor.org/rfc/rfc8725)
