"""Unit tests for ErrorHandlingAdapter."""

import pytest

from orb.domain.base.exceptions import DomainException
from orb.infrastructure.adapters.error_handling_adapter import ErrorHandlingAdapter

pytestmark = pytest.mark.unit


class TestHandleExceptions:
    def test_wraps_function_and_preserves_behaviour(self):
        adapter = ErrorHandlingAdapter()

        @adapter.handle_exceptions
        def succeeds(x):
            return x * 2

        assert succeeds(3) == 6


class TestLogErrors:
    def test_wraps_function_and_preserves_behaviour(self):
        adapter = ErrorHandlingAdapter()

        @adapter.log_errors
        def succeeds(x):
            return x + 1

        assert succeeds(1) == 2


class TestRetryOnFailure:
    def test_returns_result_on_first_success(self):
        adapter = ErrorHandlingAdapter()
        calls = []

        @adapter.retry_on_failure(max_retries=3, delay=0)
        def always_succeeds():
            calls.append(1)
            return "ok"

        assert always_succeeds() == "ok"
        assert len(calls) == 1

    def test_retries_until_success(self):
        adapter = ErrorHandlingAdapter()
        attempts = {"count": 0}

        @adapter.retry_on_failure(max_retries=3, delay=0)
        def fails_twice_then_succeeds():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ValueError("not yet")
            return "recovered"

        assert fails_twice_then_succeeds() == "recovered"
        assert attempts["count"] == 3

    def test_raises_last_exception_after_exhausting_retries(self):
        adapter = ErrorHandlingAdapter()

        @adapter.retry_on_failure(max_retries=2, delay=0)
        def always_fails():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            always_fails()

    def test_default_retry_parameters(self):
        adapter = ErrorHandlingAdapter()
        attempts = {"count": 0}

        @adapter.retry_on_failure(delay=0)
        def fails_once_then_succeeds():
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise RuntimeError("retry me")
            return "done"

        assert fails_once_then_succeeds() == "done"


class TestHandleDomainExceptions:
    def test_returns_message_for_domain_exception(self):
        adapter = ErrorHandlingAdapter()
        exc = DomainException("something went wrong")

        result = adapter.handle_domain_exceptions(exc)

        assert result == "something went wrong"

    def test_returns_none_for_non_domain_exception(self):
        adapter = ErrorHandlingAdapter()

        result = adapter.handle_domain_exceptions(ValueError("other"))

        assert result is None
