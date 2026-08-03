"""Tests for the ProviderStrategy.get_resource_id_pattern() classmethod slot."""

from orb.providers.base.strategy.provider_strategy import ProviderStrategy


def test_base_default_returns_none():
    """ProviderStrategy.get_resource_id_pattern() must return None by default.

    The default implementation signals 'no pattern enforced', so provider-agnostic
    validation code can skip ID-format checks for providers that have not opted in.
    """
    assert ProviderStrategy.get_resource_id_pattern() is None


def test_return_type_is_optional_str():
    """The return value must be None or a string — never another type."""
    result = ProviderStrategy.get_resource_id_pattern()
    assert result is None or isinstance(result, str)


def test_classmethod_callable_without_instance():
    """get_resource_id_pattern must be callable on the class, not just on instances.

    Provider-agnostic code will call this before any provider instance exists
    (e.g. at boot time during config validation).
    """
    # Should not raise — no AWSProviderConfig, no credentials, no I/O needed.
    result = ProviderStrategy.get_resource_id_pattern()
    assert result is None
