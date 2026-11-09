"""AWS retry classifier: which failures are transient and which are permanent."""

import time
from email.utils import formatdate

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectionError as BotoConnectionError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    NoCredentialsError,
    ParamValidationError,
    ProxyConnectionError,
    ReadTimeoutError,
    SSLError,
)

from orb.infrastructure.resilience import retry_classifier_registry as reg
from orb.infrastructure.resilience.retry_classifier_registry import (
    is_non_retryable,
    register_retry_classifier,
)
from orb.providers.aws.resilience.aws_retry_errors import AWS_TRANSIENT_ERROR_CODES
from orb.providers.aws.resilience.retry_classifier import (
    AWSRetryClassifier,
    call_overrides,
    register_aws_retry_classifier,
)


def _client_error(code: str, status: int = 400, date: str | None = None) -> ClientError:
    metadata: dict = {"HTTPStatusCode": status}
    if date is not None:
        metadata["HTTPHeaders"] = {"date": date}
    return ClientError(
        {"Error": {"Code": code, "Message": "m"}, "ResponseMetadata": metadata}, "Op"
    )


@pytest.fixture
def classifier() -> AWSRetryClassifier:
    return AWSRetryClassifier()


@pytest.mark.unit
class TestClientErrors:
    @pytest.mark.parametrize("code", sorted(AWS_TRANSIENT_ERROR_CODES))
    def test_transient_codes_are_retried(self, classifier, code):
        assert classifier.is_non_retryable(_client_error(code)) is False

    @pytest.mark.parametrize(
        "code",
        [
            "InvalidParameterValue",
            "UnauthorizedOperation",
            "AccessDenied",
            "ValidationError",
            "InstanceLimitExceeded",
            "VcpuLimitExceeded",
            "MaxSpotInstanceCountExceeded",
            "ServiceQuotaExceededException",
            "InvalidLaunchTemplateId.NotFound",
            "InvalidInstanceID.NotFound",
            "InsufficientInstanceCapacity",
        ],
    )
    def test_permanent_codes_are_attempted_once(self, classifier, code):
        assert classifier.is_non_retryable(_client_error(code)) is True

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_retryable_http_status_overrides_unknown_code(self, classifier, status):
        assert classifier.is_non_retryable(_client_error("SomethingNew", status)) is False

    def test_clock_skew_codes_are_retried(self, classifier):
        for code in ("RequestExpired", "RequestInTheFuture", "RequestTimeTooSkewed"):
            assert classifier.is_non_retryable(_client_error(code)) is False

    def test_signature_error_retried_only_when_the_clock_is_skewed(self, classifier):
        skewed = formatdate(time.time() - 3600, usegmt=True)
        in_sync = formatdate(time.time() - 5, usegmt=True)
        assert (
            classifier.is_non_retryable(_client_error("SignatureDoesNotMatch", date=skewed))
            is False
        )
        assert (
            classifier.is_non_retryable(_client_error("InvalidSignatureException", date=skewed))
            is False
        )
        assert (
            classifier.is_non_retryable(_client_error("SignatureDoesNotMatch", date=in_sync))
            is True
        )
        assert classifier.is_non_retryable(_client_error("SignatureDoesNotMatch")) is True

    @pytest.mark.parametrize("status", [400, 500, 503])
    @pytest.mark.parametrize(
        "code",
        [
            "InsufficientInstanceCapacity",
            "InsufficientFreeAddressesInSubnet",
            "MaxSpotInstanceCountExceeded",
            "InstanceLimitExceeded",
            "VcpuLimitExceeded",
            "SpotMaxPriceTooLow",
            "Unsupported",
        ],
    )
    def test_capacity_and_quota_codes_are_never_retried_whatever_the_http_status(
        self, classifier, code, status
    ):
        # EC2 answers capacity errors with HTTP 500, which the status rule would retry.
        assert classifier.is_non_retryable(_client_error(code, status)) is True

    def test_capacity_shortfall_is_left_to_the_capacity_fallback(self):
        assert "InsufficientInstanceCapacity" not in AWS_TRANSIENT_ERROR_CODES


@pytest.mark.unit
class TestBotocoreErrors:
    @pytest.mark.parametrize(
        "error",
        [
            ConnectTimeoutError(endpoint_url="https://ec2"),
            ReadTimeoutError(endpoint_url="https://ec2"),
            EndpointConnectionError(endpoint_url="https://ec2"),
            ConnectionClosedError(endpoint_url="https://ec2"),
            ProxyConnectionError(proxy_url="http://proxy"),
            BotoConnectionError(error="reset"),
            HTTPClientError(error="socket reset"),
            SSLError(endpoint_url="https://ec2", error="handshake"),
        ],
    )
    def test_connection_problems_are_retried(self, classifier, error):
        assert classifier.is_non_retryable(error) is False

    @pytest.mark.parametrize("error", [NoCredentialsError(), ParamValidationError(report="bad")])
    def test_other_botocore_errors_are_permanent(self, classifier, error):
        assert classifier.is_non_retryable(error) is True

    def test_non_aws_exceptions_are_left_alone(self, classifier):
        assert classifier.is_non_retryable(RuntimeError("x")) is False
        assert classifier.is_non_retryable(ValueError("x")) is False


@pytest.mark.unit
class TestPerCallOverrides:
    def test_extra_retryable_code_is_scoped_to_the_call(self, classifier):
        error = _client_error("InvalidInstanceID.NotFound")
        with call_overrides(retryable=frozenset({"InvalidInstanceID.NotFound"})):
            assert classifier.is_non_retryable(error) is False
        assert classifier.is_non_retryable(error) is True

    def test_extra_non_retryable_code_wins_over_the_table(self, classifier):
        error = _client_error("Throttling")
        with call_overrides(non_retryable=frozenset({"Throttling"})):
            assert classifier.is_non_retryable(error) is True
        assert classifier.is_non_retryable(error) is False


@pytest.mark.unit
class TestRegistration:
    def test_registration_is_idempotent_and_coexists_with_other_classifiers(self):
        class _Other:
            def is_non_retryable(self, exception):
                return isinstance(exception, KeyError)

        saved = list(reg._classifiers)
        reg._classifiers.clear()
        try:
            other = _Other()
            register_retry_classifier(other)  # type: ignore[arg-type]
            register_aws_retry_classifier()
            register_aws_retry_classifier()

            assert sum(isinstance(c, AWSRetryClassifier) for c in reg._classifiers) == 1
            assert is_non_retryable(KeyError("x")) is True
            assert is_non_retryable(_client_error("InvalidParameterValue")) is True
            assert is_non_retryable(_client_error("Throttling")) is False
            assert is_non_retryable(RuntimeError("x")) is False
        finally:
            reg._classifiers[:] = saved
