"""The cell registry covers the live provider registry and cannot rot.

An exempt cell is strict: the moment a cell is both exempt and driven, the suite
fails until the exemption is removed.
"""

from __future__ import annotations

import pytest

from tests.providers.contract.fresh_registry import freshly_registered_providers
from tests.providers.lifecycle.cells import APPLICABILITY, CELLS, EXEMPT, SCENARIOS
from tests.providers.lifecycle.oracle import INVARIANT_IDS, UNEXERCISED_UNTIL_BEHAVIOUR


def _bases(keys) -> set[tuple[str, str]]:
    return {(provider, api.split("-")[0]) for provider, api in keys}


def _live_apis() -> dict[str, list[str]]:
    """Registered provider types and, where a provider is covered, its api names."""
    from orb.providers.aws.domain.template.value_objects import ProviderApi
    from orb.providers.k8s.value_objects import KubernetesProviderApi

    api_names = {
        "aws": [api.value for api in ProviderApi],
        "k8s": [api.value for api in KubernetesProviderApi],
    }
    with freshly_registered_providers() as registry:
        return {p: api_names.get(p, []) for p in registry.get_registered_providers()}


def test_every_live_provider_and_api_has_a_cell_or_an_exemption():
    covered = _bases(CELLS) | _bases(k for k in EXEMPT if k[1] != "*")
    wholesale = {provider for provider, api in EXEMPT if api == "*"}
    missing = []
    for provider, apis in _live_apis().items():
        if provider in wholesale:
            continue
        if not apis:
            missing.append((provider, "*"))
        missing += [(provider, api) for api in apis if (provider, api) not in covered]
    assert not missing, f"add a cell or an EXEMPT entry for: {sorted(missing)}"


def test_no_entry_names_an_unknown_provider_or_api():
    live = _live_apis()
    unknown = []
    for provider, api in [*CELLS, *EXEMPT]:
        if provider not in live:
            unknown.append((provider, api))
        elif api != "*" and api.split("-")[0] not in live[provider]:
            unknown.append((provider, api))
    assert not unknown, f"entries for cells that do not exist: {unknown}"


def test_no_cell_is_also_exempt():
    both = set(CELLS) & set(EXEMPT)
    assert not both, f"cells that are driven and exempt: {sorted(both)}"


@pytest.mark.parametrize("key", sorted(k for k in EXEMPT if k[1] != "*"))
@pytest.mark.xfail(strict=True, reason="exempt cells have no lifecycle scenario yet")
def test_exempt_cell_is_not_driven(key):
    """Becomes an unexpected pass, and fails the suite, once an exempt cell is driven."""
    assert key in CELLS


def test_applicability_is_complete_and_reasoned():
    assert set(APPLICABILITY) == set(CELLS.values())
    for cell, rows in APPLICABILITY.items():
        assert set(rows) == set(SCENARIOS), f"{cell.name}: {set(rows) ^ set(SCENARIOS)}"
        for scenario, reason in rows.items():
            assert reason is None or (isinstance(reason, str) and len(reason) > 10), (
                cell.name,
                scenario,
            )


def test_exemption_reasons_name_the_gap():
    assert all(len(reason) > 10 for reason in EXEMPT.values())


def test_invariant_ids_are_unique_and_exemptions_are_known():
    assert len(set(INVARIANT_IDS)) == len(INVARIANT_IDS)
    assert set(UNEXERCISED_UNTIL_BEHAVIOUR) <= set(INVARIANT_IDS)
