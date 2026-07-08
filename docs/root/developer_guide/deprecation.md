# Deprecating Fields, Parameters, and APIs

How to deprecate a field, config key, parameter, method, or endpoint so the
deprecation signal actually reaches the people who need to see it.

## The core principle: match the signal to the audience

A deprecation is only useful if the person still using the old thing finds
out. The right mechanism depends entirely on **who** consumes the surface:

| Audience | Surface | Correct signal |
|----------|---------|----------------|
| **Operators** | Config YAML/JSON, REST request bodies (anything deserialized via Pydantic `model_validate`) | `logger.warning` (+ `Field(deprecated=)` for schema) |
| **Operators** | Config keys detected in a loaded dict at startup | `logger.warning` |
| **Developers** | Python/SDK API — constructors, `@property`, classmethods, functions called from Python | `warnings.warn(..., DeprecationWarning, stacklevel=2)` |

### Why `warnings.warn` is wrong for operator-facing surfaces

`DeprecationWarning` is designed for **library consumers** — it surfaces via
test runners, linters, and `python -W all`. It does **not** reach operators:

- Operators run the packaged server; they never pass `-W`.
- `pyproject.toml` sets `filterwarnings = ["ignore::DeprecationWarning"]`, so
  the warning is suppressed even in our own test suite.
- A `DeprecationWarning` on a config-file load path is effectively silent in
  production and in CI.

Operators read **logs**. So an operator-facing deprecation must emit a
`logger.warning`.

## Pattern 1 — Operator-facing Pydantic field deprecation (the common case)

When you rename a field on a Pydantic model that operators populate via config
or REST (e.g. `instance_type` → `machine_type`):

```python
import logging

from pydantic import AliasChoices, BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class Template(BaseModel):
    # 1. AliasChoices keeps old data deserialising.
    # 2. Field(deprecated=...) surfaces `deprecated: true` in the OpenAPI /
    #    JSON schema so schema tooling and API docs flag it.
    machine_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("machine_type", "instance_type"),
    )

    # 3. A model_validator(mode="before") runs on the RAW input dict on EVERY
    #    entry path — __init__ kwargs AND model_validate()/YAML — so the
    #    logger.warning fires no matter how the object was built. This is the
    #    load-bearing part: it closes the gap AliasChoices alone leaves open.
    @model_validator(mode="before")
    @classmethod
    def _warn_deprecated_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "instance_type" in data and "machine_type" not in data:
                logger.warning(
                    "Template field 'instance_type' is deprecated; "
                    "use 'machine_type' instead."
                )
        return data
```

`AliasChoices` alone is **not** enough — it accepts the old key *silently*.
The `model_validator(mode="before")` is what makes the deprecation visible.

## Pattern 2 — Operator-facing config-key deprecation

When a top-level config key is renamed or relocated and you detect it in a
loaded dict at startup:

```python
if "dynamodb_strategy" in storage_config:
    logger.warning(
        "Config key 'storage.dynamodb_strategy' is deprecated and will be "
        "removed in ORB 3.0; move it under "
        "provider.providers[N].config.storage.dynamodb."
    )
    # ... migrate the value ...
```

Emit the `logger.warning` at the point of detection. A `warnings.warn` here is
invisible to operators.

## Pattern 3 — Developer-facing Python/SDK deprecation

For a Python API surface consumed by developers (SDK constructors, properties,
classmethods), `warnings.warn` is the **correct** tool — it is what developers'
test suites and `python -W all` surface:

```python
import warnings


class SDKConfig:
    @property
    def region(self) -> Optional[str]:
        warnings.warn(
            "SDKConfig.region is deprecated and will be removed in the next "
            "major release; read provider_config['region'] instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.provider_config.get("region")
```

Always pass `stacklevel=2` so the warning points at the *caller's* line, not
the property body.

## Pattern 4 — Deprecated functions/methods with active callers

If a function is deprecated but still has callers, emit `warnings.warn` inside
the body on every call:

```python
def register_all_provider_types() -> None:
    warnings.warn(
        "register_all_provider_types() is deprecated; "
        "call register_all_providers() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return register_all_providers()
```

Only `raise NotImplementedError` when **zero** callers remain — a hard break is
inappropriate while callers still exist.

## Always include a removal horizon

State when the deprecated surface will be removed (e.g. "removed in ORB 3.0" /
"the next major release"). An open-ended deprecation gives no migration
deadline and tends to live forever.

## Checklist

- [ ] Identified the audience: operator (config/REST) or developer (Python/SDK)?
- [ ] Operator-facing → `logger.warning` on the deserialization / detection path
- [ ] Operator-facing Pydantic field → `model_validator(mode="before")` +
      `AliasChoices` + `Field(deprecated=)`
- [ ] Developer-facing → `warnings.warn(DeprecationWarning, stacklevel=2)`
- [ ] Named the replacement in the message
- [ ] Stated the removal version
- [ ] Added a test asserting the signal fires (use `caplog` for `logger.warning`,
      `pytest.warns` for `DeprecationWarning`)
