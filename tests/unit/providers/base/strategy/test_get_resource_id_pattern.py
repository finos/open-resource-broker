"""Tests for the ProviderStrategy.get_resource_id_pattern() classmethod slot."""

from orb.providers.base.strategy.provider_strategy import ProviderStrategy


def test_base_default_returns_none():
    """ProviderStrategy.get_resource_id_pattern() must return None by default.

    The default implementation signals that the provider does not advertise
    one common identifier format.
    """
    assert ProviderStrategy.get_resource_id_pattern() is None


def test_return_type_is_optional_str():
    """The return value must be None or a string — never another type."""
    result = ProviderStrategy.get_resource_id_pattern()
    assert result is None or isinstance(result, str)


def test_classmethod_callable_without_instance():
    """get_resource_id_pattern must be callable on the class, not just on instances.

    Introspection does not require a configured provider instance, credentials,
    or I/O.
    """
    result = ProviderStrategy.get_resource_id_pattern()
    assert result is None
