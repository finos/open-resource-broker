# Provider Selection, Fallback and Pricing: Decision Record

Status: accepted. The maintainers accepted these decisions on #434, #439 and #442. This record changes no code; the work that implements each decision is listed under [Follow-up work](#follow-up-work).

D2, D3 and D5 follow from those decisions and are open to maintainer review.

This record covers three questions: how ORB balances load across provider instances and provider types, what happens when the selected provider cannot fulfil a request, and whether price is an input to either decision.

## Baseline

The following behaviour is current. Source files are named so each statement can be checked.

### Selection

- Provider selection resolves a request to one provider instance before any provisioning call. The precedence is the `--provider-name` override, the template's `provider_name`, the template's `provider_type`, the template's `provider_api`, then the configured default. See [Multi-Provider Design](multi-provider-design.md#provider-selection-and-load-balancing). The implementation is `ProviderSelectionService` in `src/orb/infrastructure/services/provider_selection_service.py`.
- `selection_policy` is one string on `ProviderConfig` (`src/orb/config/schemas/provider_strategy_schema.py`). It applies to the whole installation; it cannot vary per template or per provider type.
- `ProviderConfig` validates ten policy names. In `_apply_load_balancing_strategy`, three have distinct branches and the rest share one:

| Policy | Behaviour today |
|---|---|
| `WEIGHTED_ROUND_ROBIN` | Lowest `priority` number wins; `weight` breaks ties among instances of equal priority. |
| `FIRST_AVAILABLE` | First instance in the candidate list. |
| `HEALTH_BASED` | Lowest `priority` number. `_health_based_selection` does not read health data, so it is identical to the fallthrough. |
| `ROUND_ROBIN`, `LEAST_CONNECTIONS`, `FASTEST_RESPONSE`, `HIGHEST_SUCCESS_RATE`, `CAPABILITY_BASED`, `RANDOM`, `PERFORMANCE_BASED` | Fallthrough: lowest `priority` number. There is no rotation state, connection count, latency history or success-rate history behind any of them. |

- `LoadBalancingProviderStrategy` (`src/orb/providers/base/strategy/load_balancing/strategy.py`) wraps several `ProviderStrategy` objects and distributes individual operations across them. Only the factory `create_load_balancing_strategy` (`src/orb/providers/base/strategy/__init__.py`) constructs it in `src/orb`, and nothing in `src/orb` calls that factory. It is exercised by tests and by `docs/root/examples/provider_strategy_usage_examples.py`.
- `FallbackProviderStrategy` (`src/orb/providers/base/strategy/fallback_strategy.py`) wraps a primary strategy and fallbacks. In `src/orb`, only the factory `create_fallback_strategy` (`src/orb/providers/base/strategy/__init__.py`) and `register_fallback_provider` (`src/orb/providers/registration.py`) construct it, and nothing in `src/orb` calls either. `ProviderSelectionService` reads `get_fallback_strategy()` only when no provider configuration exists.

### Fulfilment and failure

- A request is served by exactly one provider instance. No code path splits a requested count across instances or types.
- A `QuotaError` during provisioning is caught in `ProvisioningOrchestrationService` (`src/orb/application/services/provisioning_orchestration_service.py`) and returned as `Failed(..., recoverable=False)`. The request is not retried on another instance, another type or with a lower count.
- Provider configuration carries no quota or available-capacity field, and no port reports remaining capacity before a request. Capacity limits surface as exceptions from a provisioning call.

### Pricing

- Templates carry `price_type` and an optional `max_price` (`src/orb/domain/template/template_aggregate.py`). They express a bid preference and ceiling. They are not a price quote.
- The Azure provider scores candidate VM size and zone combinations with `SpotPlacementPlanner` (`src/orb/providers/azure/services/spot_placement_planner.py`) and `SpotPlacementScoreAdapter` (`src/orb/providers/azure/infrastructure/services/spot_placement_score_adapter.py`). The score ranks candidates inside the provider; it is not a price.
- `OCIPricingService` (`src/orb/providers/oci/services/pricing_service.py`) returns a static hourly cost estimate from reference rates and template overrides, inside the OCI provider. Nothing outside that provider consumes it.
- No port returns a price for an instance type, region or provider, and nothing compares cost across providers.

## Decisions

### D1. Selection stays at selection time

Selection stays a per-request decision made before provisioning. ORB chooses one provider instance, then provisions through it.

Ruled out: operation-time balancing through `LoadBalancingProviderStrategy`. It distributes operations of one request across providers, but request and machine records each carry a single `provider_name` (`src/orb/domain/request/aggregate.py`, `src/orb/domain/machine/aggregate.py`), so distributing within a request would need a data-model change with no stated use case. It also duplicates the health and weight handling that selection already has.

### D2. One policy surface for types and instances

Balancing across provider types and across instances of one type uses the same `selection_policy`, priority and weight. The template's `provider_type`, `provider_api` and `provider_name` already narrow the candidate set before the policy ranks it, so capability differences between types are handled by narrowing, not by separate policy rules. No separate cross-type rules are added until a concrete requirement exists.

### D3. Policy scope: global, not per template

`selection_policy` stays installation-wide. A template that needs a specific placement pins it with `provider_name`, `provider_type` or `provider_api`. Per-template and per-type policies are not added without a use case that pinning does not cover.

### D4. Policies without distinct behaviour are removed

A policy that validates but behaves like the fallthrough misleads operators. `WEIGHTED_ROUND_ROBIN` and `FIRST_AVAILABLE` have distinct behaviour and stay. The seven policies below have none and are each removed:

| Policy | Decision | Reason |
|---|---|---|
| `ROUND_ROBIN` | Remove | No rotation state exists; the policy resolves to the lowest `priority` number. |
| `RANDOM` | Remove | It resolves to the lowest `priority` number, and random choice has no use case that `WEIGHTED_ROUND_ROBIN` does not cover. |
| `CAPABILITY_BASED` | Remove | Capability matching is already step four of the selection precedence; as a policy it adds nothing. |
| `LEAST_CONNECTIONS` | Remove | The selection service has no per-provider connection or in-flight operation count. |
| `FASTEST_RESPONSE` | Remove | The selection service has no latency history. |
| `HIGHEST_SUCCESS_RATE` | Remove | The selection service has no per-provider success history. |
| `PERFORMANCE_BASED` | Remove | It needs the same missing performance data as the two rows above. |

Removal has two steps. First the policy is deprecated under the [deprecation policy](../developer_guide/deprecation.md), with a warning when configuration names it. Then validation rejects it. A removed policy can return when the data it needs exists.

`HEALTH_BASED` is separate. It validates today but is identical to the fallthrough, because `_health_based_selection` does not read health data. The author's recommendation, not an accepted decision, is to implement it by excluding unhealthy instances before ranking, using the probe behind `orb providers health`. If it is not implemented, it falls under the same removal.

### D5. Unused strategy classes are removed

`LoadBalancingProviderStrategy` and `FallbackProviderStrategy` are not the foundation of future balancing or fallback. D1 rules out the first. The second wraps a primary strategy with fallback strategies at the operation layer, whereas D6 retries a whole request against another provider instance. Both classes, their factory functions in `src/orb/providers/base/strategy/__init__.py`, `register_fallback_provider`, and the usage example are removed after confirming that no supported public interface depends on them. The selection service's no-configuration branch that reads `get_fallback_strategy()` is reviewed in the same change.

### D6. Fallback retries the whole request, reactively

- Fallback retries an entire request against the next candidate. Splitting a request's count across providers is not implemented.
- Fallback is reactive: it follows a failed provisioning attempt. A proactive capacity or quota check is not added. A provider capacity-check port is reconsidered after reactive fallback has run long enough to show which failures it cannot absorb.
- The first trigger is `QuotaError`, which today produces a non-recoverable failure.
- Candidates come from the same narrowing as selection, ordered by the active `selection_policy`. A request pinned by `provider_name` has no fallback. A request pinned by `provider_type` falls back only within that type. A request pinned by `provider_api` falls back only to instances that support it.
- Fallback applies to the initial provisioning call only. Moving running capacity between providers is out of scope.
- A request is still served by exactly one provider instance, so the existing provider reference on request and machine records stays correct. A failed attempt is reported to the caller and is not hidden; this avoids the silent substitution that the AWS spot-to-on-demand fallback performs inside one provider instance.

### D7. Price is informational; no pricing port now

- ORB does not add a cross-provider pricing port. Price is not an input to selection or fallback ordering.
- `price_type` and `max_price` remain a per-template bid preference.
- Provider-local scoring and estimates, the Azure placement score and the OCI estimate, stay inside their providers.
- Adding placement scoring for AWS spot capacity is the next step on price-related behaviour. The pricing question is revisited with that work, using it to decide whether a shared "rank these candidates" capability is worth defining.

Ruled out: a pricing port feeding selection. It requires price sources for every provider, with different billing models and no quote API in the current providers, and it would make ordering depend on data ORB cannot currently obtain or validate.

## Follow-up work

Sizes are rough: S under a day, M a few days, L a week or more. Each item is tracked in the issue linked in the table.

| # | Work | Size | Decision | Tracked in |
|---|---|---|---|---|
| 1 | Deprecate, then remove, `ROUND_ROBIN`, `RANDOM`, `CAPABILITY_BASED`, `LEAST_CONNECTIONS`, `FASTEST_RESPONSE`, `HIGHEST_SUCCESS_RATE` and `PERFORMANCE_BASED` | M | D4 | [#434](https://github.com/finos/open-resource-broker/issues/434) |
| 2 | Decide `HEALTH_BASED`: implement it using provider health results (recommended), or remove it with the policies in item 1 | M | D4 | [#434](https://github.com/finos/open-resource-broker/issues/434) |
| 3 | Remove `LoadBalancingProviderStrategy`, `FallbackProviderStrategy`, their factories, `register_fallback_provider` and the usage example | M | D5 | [#434](https://github.com/finos/open-resource-broker/issues/434) |
| 4 | Whole-request fallback on `QuotaError` across ordered candidates, honouring pinned `provider_name`, `provider_type` and `provider_api`, with the failed attempt visible to the caller | L | D6 | [#439](https://github.com/finos/open-resource-broker/issues/439) |
| 5 | AWS spot placement scoring, then revisit D7 | L | D7 | [#442](https://github.com/finos/open-resource-broker/issues/442) |
| 6 | Update [Multi-Provider Design](multi-provider-design.md) and the [Provider Strategy Guide](../provider_strategy_guide.md) when items 1 to 5 land | S | D4 to D7 | [#434](https://github.com/finos/open-resource-broker/issues/434) |

## Related documentation

- [Multi-Provider Design](multi-provider-design.md)
- [Provider Strategy Guide](../provider_strategy_guide.md)
- [Multi-Provider Configuration](../configuration/multi-provider.md)
