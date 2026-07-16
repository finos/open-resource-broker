"""Storage-completeness assertion for bootstrap.

Call :func:`assert_storage_registration_complete` after all provider
registrations have run (i.e. after :func:`~orb.bootstrap.provider_services.register_provider_services`).
It checks that the storage backend selected in the operator's configuration
is actually registered in the :class:`~orb.infrastructure.storage.registry.StorageRegistry`.

This catches the common misconfiguration where an operator sets
``storage.strategy = "dynamodb"`` but has not installed the AWS extra,
so DynamoDB is never registered and every repository access would fail with
a generic :class:`~orb.infrastructure.storage.registry.UnsupportedStorageError`
at the first request — far from startup.  The assertion surfaces the problem
immediately, at startup, with an actionable message.
"""

from __future__ import annotations


class StorageCompletenessError(RuntimeError):
    """Raised when the configured storage backend is not registered.

    The message names the configured storage type and suggests the likely
    cause (e.g. a missing provider extra) so the operator knows exactly what
    to fix without inspecting code.
    """


def assert_storage_registration_complete() -> None:
    """Assert that the configured storage backend is registered in the StorageRegistry.

    Reads ``storage.strategy`` from the application configuration and verifies
    it against :func:`~orb.infrastructure.storage.registration.is_storage_type_available`.

    Must be called **after** all provider-owned storage types are registered —
    i.e. after :func:`~orb.bootstrap.provider_services.register_provider_services`
    runs (step 8 in :mod:`orb.bootstrap.services`).  Calling it earlier would
    produce a false-positive for provider-backed backends like ``dynamodb`` or
    ``aurora`` that are only registered during provider initialisation.

    Raises:
        StorageCompletenessError: when the configured storage type is not
            present in the registry.  The error message names the storage type
            and gives a fix hint (install the appropriate provider extra).
    """
    from orb.config.managers.configuration_manager import ConfigurationManager
    from orb.infrastructure.storage.registration import is_storage_type_available

    config = ConfigurationManager()
    storage_type = config.get_storage_strategy()

    if not is_storage_type_available(storage_type):
        raise StorageCompletenessError(
            f"Configured storage backend {storage_type!r} is not registered.\n"
            f"The application was started with storage.strategy = {storage_type!r} "
            f"but no factory for that backend was found in StorageRegistry.\n"
            f"Likely cause: the provider extra that supplies this backend is not installed "
            f"(e.g. 'pip install orb[aws]' for dynamodb/aurora).\n"
            f"Fix: install the required extra, or change storage.strategy to a backend "
            f"that is available (e.g. 'json' or 'sql')."
        )
