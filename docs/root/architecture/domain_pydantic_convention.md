# Pydantic in the Domain Layer

The domain layer builds aggregates, value objects and events on pydantic. This is a deliberate convention, not debt: validation, immutability and serialisation come from one well-tested library instead of hand-written equivalents.

## Rules

- Only the modules on the allowlist in `tests/unit/architecture/test_domain_pydantic_convention.py` may import pydantic, with a single import statement each. A new importer needs an allowlist change in review.
- The domain never imports `pydantic_settings`, `BaseSettings` or `pydantic.v1`. Settings belong to the configuration layer.
- The domain never imports `orb.config`, `orb.infrastructure` or `orb.providers`.

## Enforcement

`test_domain_pydantic_convention.py` parses every module under `src/orb/domain` and fails on any deviation from these rules.
