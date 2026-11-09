"""Every domain exception class has an expected HTTP status.

Adding an exception class without deciding how it is reported to API callers fails
here, so a new error type cannot silently become a 500.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from http import HTTPStatus
from typing import Any

import pytest

import orb
from orb.domain.base.exceptions import (
    ApplicationError,
    BusinessRuleViolationError,
    ConcurrencyError,
    ConfigurationError,
    DomainException,
    DuplicateError,
    EntityNotFoundError,
    InfrastructureError,
    InvariantViolationError,
    OperationNotSupportedError,
    ProviderContractError,
    ProviderNotReadyError,
    QuotaError,
    QuotaExceededError,
    ValidationError,
)
from orb.domain.exceptions.image_resolution_error import ImageResolutionError
from orb.domain.machine.exceptions import (
    InvalidMachineStateError,
    MachineException,
    MachineNotFoundError,
    MachineProvisioningError,
    MachineValidationError,
)
from orb.domain.request.exceptions import (
    InvalidRequestStateError,
    RequestException,
    RequestNotFoundError,
    RequestProcessingError,
    RequestTimeoutError,
    RequestValidationError,
)
from orb.domain.request.termination_confirmation import (
    InstanceStillVisibleError,
    TerminationNotVerifiedError,
)
from orb.domain.template.exceptions import (
    InvalidTemplateConfigurationError,
    TemplateAlreadyExistsError,
    TemplateException,
    TemplateNotFoundError,
    TemplateValidationError,
)
from orb.infrastructure.error.http_response_handler import HTTPErrorResponseHandler

NOT_FOUND = HTTPStatus.NOT_FOUND
BAD_REQUEST = HTTPStatus.BAD_REQUEST
UNPROCESSABLE = HTTPStatus.UNPROCESSABLE_ENTITY
CONFLICT = HTTPStatus.CONFLICT
UNAVAILABLE = HTTPStatus.SERVICE_UNAVAILABLE
SERVER_ERROR = HTTPStatus.INTERNAL_SERVER_ERROR

# Client errors answer with a 4xx. Server-side failures, and errors without a specific
# mapping, answer with a 5xx. Every class defined under ``orb.domain`` must be listed;
# provider and infrastructure subclasses are checked against their nearest listed base.
EXPECTED: dict[type[DomainException], HTTPStatus] = {
    # Client errors
    ValidationError: BAD_REQUEST,
    MachineValidationError: BAD_REQUEST,
    RequestValidationError: BAD_REQUEST,
    TemplateValidationError: BAD_REQUEST,
    EntityNotFoundError: NOT_FOUND,
    MachineNotFoundError: NOT_FOUND,
    RequestNotFoundError: NOT_FOUND,
    TemplateNotFoundError: NOT_FOUND,
    BusinessRuleViolationError: UNPROCESSABLE,
    ConcurrencyError: CONFLICT,
    DuplicateError: CONFLICT,
    InstanceStillVisibleError: CONFLICT,
    TerminationNotVerifiedError: CONFLICT,
    # Backend and server-side failures
    InfrastructureError: UNAVAILABLE,
    ConfigurationError: SERVER_ERROR,
    ApplicationError: SERVER_ERROR,
    # Raised by the domain without a dedicated HTTP mapping
    InvariantViolationError: SERVER_ERROR,
    QuotaError: SERVER_ERROR,
    QuotaExceededError: SERVER_ERROR,
    ProviderContractError: SERVER_ERROR,
    ProviderNotReadyError: SERVER_ERROR,
    OperationNotSupportedError: SERVER_ERROR,
    MachineException: SERVER_ERROR,
    ImageResolutionError: SERVER_ERROR,
    InvalidMachineStateError: SERVER_ERROR,
    MachineProvisioningError: SERVER_ERROR,
    RequestException: SERVER_ERROR,
    InvalidRequestStateError: SERVER_ERROR,
    RequestProcessingError: SERVER_ERROR,
    RequestTimeoutError: SERVER_ERROR,
    TemplateException: SERVER_ERROR,
    InvalidTemplateConfigurationError: SERVER_ERROR,
    TemplateAlreadyExistsError: SERVER_ERROR,
    DomainException: SERVER_ERROR,
}


def _import_exception_modules() -> None:
    for module in pkgutil.walk_packages(orb.__path__, "orb."):
        name = module.name
        if (
            name.startswith(("orb.domain.", "orb.infrastructure."))
            and "exceptions" in name.rsplit(".", 1)[-1]
        ):
            importlib.import_module(name)
        elif name.startswith("orb.domain.") and not module.ispkg:
            importlib.import_module(name)


def _concrete_subclasses() -> list[type[DomainException]]:
    _import_exception_modules()
    found: dict[type, None] = {}

    def walk(cls: type) -> None:
        for sub in cls.__subclasses__():
            if sub not in found:
                found[sub] = None
                walk(sub)

    walk(DomainException)
    return list(found)


def _instantiate(cls: type[DomainException]) -> DomainException:
    parameters = [
        p
        for p in inspect.signature(cls.__init__).parameters.values()
        if p.name != "self" and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    required = [p for p in parameters if p.default is p.empty]
    return cls(*(["detail"] * len(required)))  # type: ignore[arg-type]


def _expected_for(cls: type[DomainException]) -> HTTPStatus | None:
    for ancestor in cls.__mro__:
        if ancestor in EXPECTED:
            return EXPECTED[ancestor]  # type: ignore[index]
    return None


def test_every_domain_package_exception_is_listed():
    unlisted = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in _concrete_subclasses()
        if cls.__module__.startswith("orb.domain.") and cls not in EXPECTED
    ]
    assert unlisted == [], f"exception classes without an expected HTTP status: {unlisted}"


def test_every_exception_class_resolves_to_an_expectation():
    unresolved = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in _concrete_subclasses()
        if _expected_for(cls) is None
    ]
    assert unresolved == []


def _cases() -> list[Any]:
    return [
        pytest.param(cls, id=f"{cls.__module__.removeprefix('orb.')}.{cls.__qualname__}")
        for cls in sorted(_concrete_subclasses(), key=lambda c: (c.__module__, c.__qualname__))
    ]


@pytest.mark.parametrize("cls", _cases())
def test_exception_is_reported_with_its_expected_status(cls):
    expected = _expected_for(cls)
    assert expected is not None
    response = HTTPErrorResponseHandler().handle_error_for_http(_instantiate(cls))

    if cls.__module__.startswith("orb.domain.") or cls in EXPECTED:
        assert response.http_status == expected
    else:
        # Provider and infrastructure subclasses inherit their base's mapping, except
        # that a more specific client-error base wins when one is mixed in.
        implied = {EXPECTED[ancestor] for ancestor in cls.__mro__ if ancestor in EXPECTED}
        assert response.http_status in implied


@pytest.mark.parametrize(
    ("exc_cls", "code"),
    [
        (InstanceStillVisibleError, "INSTANCE_STILL_VISIBLE"),
        (TerminationNotVerifiedError, "TERMINATION_NOT_VERIFIED"),
    ],
)
def test_termination_conflict_body_matches_confirm_route(exc_cls, code):
    response = HTTPErrorResponseHandler().handle_error_for_http(
        exc_cls("specific reason", details={"machine_ids": ["i-1", "i-2"]})
    )

    assert response.http_status == CONFLICT
    assert response.error_code == code
    assert response.message == "specific reason"
    assert response.details["machine_ids"] == ["i-1", "i-2"]
