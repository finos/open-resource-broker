"""Provider conformance: every registered provider exports the same surface.

The registry is populated through the real registration path
(``register_all_providers``), never a hand-built list, so a provider that
silently fails to register shows up here as a failure rather than a skipped
case.  A second guard checks that each registered provider either subclasses
the shared contract bases under ``tests/providers/<name>/contract/`` or has an
explicit, reasoned entry in ``EXEMPT``.  ``EXEMPT`` is a closed set: an entry
for a provider that now subclasses the base is reported as stale, so the dict
can only shrink.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

PROVIDERS_DIR = Path(__file__).resolve().parents[1]

# Providers the project ships. A new provider must be added here and either
# gain contract subclasses or be exempted below.
KNOWN_PROVIDERS = ("aws", "azure", "gcp", "k8s", "oci")

BASE_CONTRACTS = (
    "BaseMonitoringContract",
    "BaseProvisioningContract",
    "BaseTemplateContract",
    "BaseValidationContract",
)

_NO_SUBCLASS = "no provider fixtures for the shared contract bases yet"

# Closed set keyed by (provider, base contract). Remove an entry when the
# provider gains the subclass; the stale check enforces this.
EXEMPT: dict[tuple[str, str], str] = {
    (provider, base): _NO_SUBCLASS
    for provider in ("azure", "gcp", "oci")
    for base in BASE_CONTRACTS
}


# Providers that register no validator factory today. Closed set: the entry
# must be removed once the provider registers one.
NO_VALIDATOR_FACTORY = {"oci": "#732: no validator factory is registered"}


def _contract_subclasses(provider: str) -> set[str]:
    """Names of shared contract bases subclassed under ``tests/providers/<provider>/contract``."""
    found: set[str] = set()
    for path in (PROVIDERS_DIR / provider / "contract").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                if name in BASE_CONTRACTS:
                    found.add(name)
    return found


def test_registry_yields_every_known_provider(registry) -> None:
    assert sorted(registry.get_registered_providers()) == sorted(KNOWN_PROVIDERS)


@pytest.mark.parametrize("provider", KNOWN_PROVIDERS)
class TestRegistrationExports:
    def test_config_factory(self, registry, provider) -> None:
        assert callable(registry.get_config_factory(provider))

    def test_strategy_factory(self, registry, provider) -> None:
        assert callable(registry._get_type_registration(provider).strategy_factory)

    def test_strategy_class_is_concrete(self, registry, provider) -> None:
        strategy_class = registry._get_type_registration(provider).strategy_class
        assert inspect.isclass(strategy_class)
        assert not inspect.isabstract(strategy_class), sorted(
            getattr(strategy_class, "__abstractmethods__", ())
        )
        assert callable(getattr(strategy_class, "get_capabilities", None))

    def test_validator_factory(self, registry, provider) -> None:
        validator_factory = registry._get_type_registration(provider).validator_factory
        if provider in NO_VALIDATOR_FACTORY:
            assert validator_factory is None, "stale NO_VALIDATOR_FACTORY entry"
        else:
            assert callable(validator_factory)


def test_contract_subclasses_match_exemptions(registry) -> None:
    """Every registered provider subclasses each base contract or is exempted."""
    missing = {
        (provider, base)
        for provider in registry.get_registered_providers()
        for base in BASE_CONTRACTS
        if base not in _contract_subclasses(provider)
    }
    unexempted = sorted(missing - set(EXEMPT))
    stale = sorted(set(EXEMPT) - missing)
    assert not unexempted, f"add a contract subclass or an EXEMPT entry for: {unexempted}"
    assert not stale, f"remove stale EXEMPT entries (provider now covered or unknown): {stale}"
