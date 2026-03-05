# Security Hardening Implementation

> **Note:** This document is an internal implementation reference describing security controls built into ORB. It is not a vulnerability disclosure policy. To report a security vulnerability, follow the standard responsible disclosure process for this repository.

## Overview

This document describes the security hardening implementations for the Open Resource Broker (ORB) project, addressing OWASP Top 10 vulnerabilities and implementing defense-in-depth security controls.

## Implemented Security Features

### 1. JWT Token Blacklist (OWASP A02: Cryptographic Failures)

**Location:** `src/infrastructure/auth/token_blacklist/`

**Features:**
- Token revocation support for secure logout
- Redis-based blacklist with automatic expiration
- In-memory fallback for development/testing
- Automatic cleanup of expired tokens
- Thread-safe operations with async support

**Usage:**
```python
from infrastructure.auth.token_blacklist import InMemoryTokenBlacklist

blacklist = InMemoryTokenBlacklist()
await blacklist.add_token(token, expires_at)
is_blacklisted = await blacklist.is_blacklisted(token)
```

**Security Benefits:**
- Prevents use of stolen tokens after logout
- Mitigates session fixation attacks
- Supports token rotation strategies

### 2. Enhanced Bearer Token Strategy (OWASP A07: Authentication Failures)

**Location:** `src/infrastructure/auth/strategy/bearer_token_strategy_enhanced.py`

**Features:**
- JWT token validation with blacklist checking
- Rate limiting (10 attempts per minute per IP)
- Secret key strength validation (minimum 256 bits)
- Proper JWT signature verification
- Token format validation to prevent injection
- Security logging for all token operations

**Security Improvements:**
- Prevents brute force attacks via rate limiting
- Validates JWT structure and signature
- Checks token expiration and issued-at times
- Requires essential claims (exp, iat, sub)
- Sanitizes token input to prevent header injection

**Usage:**
```python
from infrastructure.auth.strategy.bearer_token_strategy_enhanced import EnhancedBearerTokenStrategy
from infrastructure.auth.token_blacklist import InMemoryTokenBlacklist

blacklist = InMemoryTokenBlacklist()
strategy = EnhancedBearerTokenStrategy(
    secret_key="your-256-bit-secret-key",
    blacklist=blacklist,
    rate_limit_enabled=True
)
```

### 3. Input Validation Framework (OWASP A03: Injection)

**Location:** `src/infrastructure/validation/`

**Features:**
- Input sanitization to prevent injection attacks
- Length validation with configurable limits
- Character whitelisting (alphanumeric, AWS regions, etc.)
- Type validation (integers, choices, etc.)
- Secure input function to replace direct `input()` calls

**Validation Functions:**
- `sanitize_input()` - Remove dangerous characters
- `validate_length()` - Enforce min/max length
- `validate_alphanumeric()` - Whitelist alphanumeric chars
- `validate_integer()` - Parse and validate integers
- `validate_choice()` - Validate against allowed choices
- `validate_aws_region()` - Validate AWS region format
- `secure_input()` - Secure replacement for `input()`

**Dangerous Characters Blocked:**
```
< > & | ; ` $ ( ) { } [ ] \n \r
```

**Usage:**
```python
from infrastructure.validation import secure_input, validate_aws_region

# Secure user input
region = secure_input(
    "Enter AWS region: ",
    default="us-east-1",
    validator=validate_aws_region,
    max_length=50
)
```

### 4. Enhanced Authentication Middleware (OWASP A05: Security Misconfiguration)

**Location:** `src/api/middleware/auth_middleware_enhanced.py`

**Features:**
- Path normalization to prevent traversal attacks
- Exact path matching for excluded paths (no prefix matching)
- Security headers on all responses
- Sanitized error messages to prevent information disclosure
- Request logging with IP tracking
- Header length limits to prevent DoS

**Security Headers Added:**
- `X-Frame-Options: DENY` - Prevent clickjacking
- `X-Content-Type-Options: nosniff` - Prevent MIME sniffing
- `X-XSS-Protection: 1; mode=block` - Enable XSS protection
- `Strict-Transport-Security` - Force HTTPS
- `Content-Security-Policy` - Restrict resource loading
- `Referrer-Policy` - Control referrer information
- `Permissions-Policy` - Disable unnecessary features

**Path Traversal Protection:**
```python
# Normalizes paths to prevent traversal
/health/../admin  -> /admin (blocked if not in excluded_paths)
/health/./../../etc/passwd -> /etc/passwd (blocked)
```

**Usage:**
```python
from api.middleware.auth_middleware_enhanced import EnhancedAuthMiddleware

app.add_middleware(
    EnhancedAuthMiddleware,
    auth_port=auth_strategy,
    excluded_paths=["/health", "/docs"],
    require_auth=True
)
```

## Security Testing

### Test Coverage

**Token Blacklist Tests:** `tests/unit/infrastructure/auth/test_token_blacklist.py`
- Token addition and removal
- Expiration handling
- Automatic cleanup
- Blacklist size tracking

**Input Validation Tests:** `tests/unit/infrastructure/validation/test_input_validator.py`
- Dangerous character detection
- Length validation
- Type validation
- Format validation

### Running Security Tests

```bash
# Run all security tests
python -m pytest tests/unit/infrastructure/auth/ -v
python -m pytest tests/unit/infrastructure/validation/ -v

# Run with coverage
python -m pytest tests/unit/infrastructure/auth/ --cov=infrastructure.auth
python -m pytest tests/unit/infrastructure/validation/ --cov=infrastructure.validation
```

## OWASP Top 10 Coverage

### A01: Broken Access Control
- ✅ Authorization checks in middleware
- ✅ Role-based access control (RBAC)
- ✅ Permission validation

### A02: Cryptographic Failures
- ✅ JWT token blacklist for secure logout
- ✅ Strong secret key validation (256+ bits)
- ✅ Proper JWT signature verification

### A03: Injection
- ✅ Input sanitization framework
- ✅ Character whitelisting
- ✅ Length limits on all inputs
- ✅ Parameterized queries (existing)

### A04: Insecure Design
- ✅ Security-first architecture
- ✅ Defense in depth
- ✅ Fail-secure defaults

### A05: Security Misconfiguration
- ✅ Security headers on all responses
- ✅ Secure defaults
- ✅ Error message sanitization

### A06: Vulnerable Components
- ✅ Using PyJWT library for proper JWT handling
- ✅ Regular dependency updates (existing)

### A07: Authentication Failures
- ✅ Rate limiting on authentication
- ✅ Token blacklist
- ✅ Strong password policies (existing)
- ✅ Secure session management

### A08: Software and Data Integrity Failures
- ✅ JWT signature verification
- ✅ Token integrity checks

### A09: Logging and Monitoring Failures
- ✅ Security event logging
- ✅ Authentication attempt tracking
- ✅ Rate limit violation logging

### A10: Server-Side Request Forgery (SSRF)
- ✅ Input validation on URLs
- ✅ Whitelist validation (existing)

## Migration Guide

### Replacing Direct input() Calls

**Before:**
```python
region = input("Enter region: ").strip() or "us-east-1"
```

**After:**
```python
from infrastructure.validation import secure_input, validate_aws_region

region = secure_input(
    "Enter region: ",
    default="us-east-1",
    validator=validate_aws_region
)
```

### Enabling Token Blacklist

**Before:**
```python
strategy = BearerTokenStrategy(secret_key="key")
```

**After:**
```python
from infrastructure.auth.token_blacklist import InMemoryTokenBlacklist
from infrastructure.auth.strategy.bearer_token_strategy_enhanced import EnhancedBearerTokenStrategy

blacklist = InMemoryTokenBlacklist()
strategy = EnhancedBearerTokenStrategy(
    secret_key="your-strong-secret-key-at-least-32-bytes",
    blacklist=blacklist
)
```

### Upgrading Middleware

**Before:**
```python
from api.middleware.auth_middleware import AuthMiddleware
```

**After:**
```python
from api.middleware.auth_middleware_enhanced import EnhancedAuthMiddleware
```

## Configuration

### Environment Variables

```bash
# JWT Configuration
ORB_JWT_SECRET_KEY="your-256-bit-secret-key-here"
ORB_JWT_ALGORITHM="HS256"
ORB_JWT_EXPIRY=3600

# Rate Limiting
ORB_RATE_LIMIT_ENABLED=true
ORB_RATE_LIMIT_MAX_ATTEMPTS=10
ORB_RATE_LIMIT_WINDOW=60

# Redis (optional, for distributed blacklist)
ORB_REDIS_URL="redis://localhost:6379"
```

### Production Recommendations

1. **Secret Key Management:**
   - Use at least 256-bit (32-byte) secret keys
   - Rotate keys regularly
   - Store in secure key management system (AWS Secrets Manager, HashiCorp Vault)

2. **Rate Limiting:**
   - Enable rate limiting in production
   - Adjust limits based on traffic patterns
   - Monitor rate limit violations

3. **Token Blacklist:**
   - Use Redis for distributed deployments
   - Configure automatic cleanup intervals
   - Monitor blacklist size

4. **Security Headers:**
   - Enable HSTS in production
   - Configure CSP based on application needs
   - Test headers with security scanners

5. **Logging:**
   - Enable security event logging
   - Monitor authentication failures
   - Set up alerts for suspicious activity

## Security Scanning

### Recommended Tools

```bash
# Static analysis
bandit -r src/

# Dependency scanning
safety check

# SAST scanning
semgrep --config=auto src/
```

## Incident Response

### Token Compromise

1. Revoke compromised token:
   ```python
   await auth_strategy.revoke_token(compromised_token)
   ```

2. Force user re-authentication

3. Rotate secret keys if necessary

4. Review logs for suspicious activity

### Rate Limit Violations

1. Check logs for offending IPs
2. Investigate for attack patterns
3. Consider IP blocking if malicious
4. Adjust rate limits if legitimate traffic

## Compliance

This implementation helps meet requirements for:
- OWASP ASVS (Application Security Verification Standard)
- PCI-DSS (Payment Card Industry Data Security Standard)
- SOC 2 (System and Organization Controls)
- GDPR (General Data Protection Regulation)

## References

- [OWASP Top 10 2021](https://owasp.org/Top10/)
- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/)
- [JWT Best Practices](https://tools.ietf.org/html/rfc8725)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
