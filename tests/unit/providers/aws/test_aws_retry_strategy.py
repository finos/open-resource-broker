"""Regression tests for AWSRetryStrategy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from orb.providers.aws.resilience.aws_retry_strategy import AWSRetryStrategy


def _make_strategy(service: str = "ec2") -> AWSRetryStrategy:
    """Build an AWSRetryStrategy with a mock logger."""
    return AWSRetryStrategy(logger=MagicMock(), service=service)


def _make_client_error(code: str, message: str = "test error") -> Exception:
    """Build a minimal botocore-style exception with a .response attribute."""
    exc = Exception(message)
    exc.response = {"Error": {"Code": code, "Message": message}}  # type: ignore[attr-defined]
    return exc


class TestAWSRetryStrategyOnRetry:
    """on_retry must not raise AttributeError (regression: was calling calculate_delay)."""

    def test_on_retry_does_not_raise_attribute_error(self) -> None:
        """on_retry previously called self.calculate_delay which does not exist."""
        strategy = _make_strategy()
        exc = _make_client_error("RequestLimitExceeded")

        # Should complete without AttributeError
        strategy.on_retry(attempt=0, exception=exc)

    def test_on_retry_uses_get_delay_value(self) -> None:
        """on_retry logs the delay returned by get_delay — verify the values are consistent."""
        strategy = _make_strategy()
        exc = _make_client_error("Throttling")

        expected_delay = strategy.get_delay(1)

        # Patch get_delay to return a fixed value so the log message is deterministic
        with patch.object(strategy, "get_delay", return_value=expected_delay) as mock_get_delay:
            strategy.on_retry(attempt=1, exception=exc)

        mock_get_delay.assert_called_once_with(1)

    def test_on_retry_logs_warning_with_delay(self) -> None:
        """on_retry emits a warning that includes the delay and attempt info."""
        logger = MagicMock()
        strategy = AWSRetryStrategy(logger=logger, service="ec2")
        exc = _make_client_error("InternalError")

        strategy.on_retry(attempt=0, exception=exc)

        logger.warning.assert_called_once()
        call_args = logger.warning.call_args
        # Positional args: message, service, attempt+1, max_attempts, delay, error_code
        pos = call_args[0]
        assert pos[1] == "ec2"  # service
        assert pos[2] == 1  # attempt + 1 == 1

    def test_on_retry_increments_attempt_display(self) -> None:
        """Attempt number logged is 1-based (attempt+1)."""
        logger = MagicMock()
        strategy = AWSRetryStrategy(logger=logger, service="s3")
        exc = _make_client_error("SlowDown")

        strategy.on_retry(attempt=2, exception=exc)

        call_args = logger.warning.call_args[0]
        assert call_args[2] == 3  # attempt 2 displayed as 3

    def test_on_retry_unknown_error_still_works(self) -> None:
        """on_retry handles a plain (non-botocore) exception without crashing."""
        strategy = _make_strategy()
        exc = ValueError("something went wrong")

        # Should not raise — get_aws_error_info returns {"code": "Unknown", ...}
        strategy.on_retry(attempt=0, exception=exc)
