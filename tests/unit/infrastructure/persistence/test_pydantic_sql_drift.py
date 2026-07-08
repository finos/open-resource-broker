"""Pydantic ↔ SQL drift guard.

ORB keeps the domain aggregate (pydantic) and the SQL ORM mapping
(sqlalchemy) as two separate layers — see audit notes for rationale.
The drift hazard is real: a required-on-aggregate field that is nullable
in SQL silently accepts NULL inserts, which then fail aggregate
validation at load time and surface as 5xx in production.

This test enforces the invariant: for every domain field with no
default value (i.e. required at construction time), the corresponding
SQL column MUST be `nullable=False`. The reverse direction (SQL
nullable=False but pydantic Optional) is allowed — that's just a
stricter database invariant.

A second check guards ``default_factory`` fields (lists and dicts):
even though these have a factory-supplied default they are still
susceptible to NULL reads from SQL when legacy rows pre-date the NOT
NULL constraint.  For each such field we assert either:
  (a) the SQL column is NOT NULL, or
  (b) the field appears in ``NULLABLE_WITH_COERCION`` — an allowlist
      that names the read-side coercion site in ``_apply_nullable_defaults``.

Per-aggregate ALLOWED_MISMATCHES set captures the small handful of
legacy / infra-derived fields that are intentionally divergent. Each
entry needs a comment explaining why.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.storage.sql.models import (
    MachineModel,
    RequestModel,
    TemplateModel,
)

# ---------------------------------------------------------------------------
# Mismatches we intentionally tolerate. Add entries here with a justifying
# comment when the drift is by design (legacy columns, infra-derived,
# computed properties, etc.).
# ---------------------------------------------------------------------------

# Pydantic field name → reason it's allowed to mismatch.
ALLOWED_MISMATCHES: dict[type[BaseModel], dict[str, str]] = {
    Machine: {
        # provider_type has a domain default of "aws" so the aggregate
        # never sees it missing; SQL keeps the column nullable because
        # legacy rows did not set it explicitly.
        "provider_type": "domain default supplies value when SQL is NULL",
        # status defaults to PENDING; legacy rows may have NULL.
        "status": "domain default supplies value when SQL is NULL",
    },
    Request: {
        "status": "domain default supplies value when SQL is NULL",
    },
    Template: {
        # Template price_type has a domain default of "ondemand".
        "price_type": "domain default supplies value when SQL is NULL",
    },
}


AGGREGATE_TO_MODEL = [
    (Machine, MachineModel),
    (Request, RequestModel),
    (Template, TemplateModel),
]

# ---------------------------------------------------------------------------
# Allowlist for default_factory fields that are nullable in SQL but are
# safely coerced to empty containers on read.
#
# Format: aggregate class → {field_name: coercion_site}
# The coercion_site string names the helper that performs the coercion so a
# future drift (field added to the aggregate without a corresponding coercion)
# triggers the test.
# ---------------------------------------------------------------------------
NULLABLE_WITH_COERCION: dict[type[BaseModel], dict[str, str]] = {
    Machine: {
        # MachineSerializer._apply_nullable_defaults coerces all of these.
        "tags": "MachineSerializer._apply_nullable_defaults",
        "metadata": "MachineSerializer._apply_nullable_defaults",
        "provider_data": "MachineSerializer._apply_nullable_defaults",
        "security_group_ids": "MachineSerializer._apply_nullable_defaults",
    },
    Request: {
        # RequestSerializer._apply_nullable_defaults coerces all of these.
        "metadata": "RequestSerializer._apply_nullable_defaults",
        "error_details": "RequestSerializer._apply_nullable_defaults",
        "provider_data": "RequestSerializer._apply_nullable_defaults",
        "resource_ids": "RequestSerializer._apply_nullable_defaults",
        "machine_ids": "RequestSerializer._apply_nullable_defaults",
    },
    Template: {
        # TemplateSerializer._apply_nullable_defaults coerces all of these.
        "tags": "TemplateSerializer._apply_nullable_defaults",
        "metadata": "TemplateSerializer._apply_nullable_defaults",
        "provider_data": "TemplateSerializer._apply_nullable_defaults",
        "security_group_ids": "TemplateSerializer._apply_nullable_defaults",
        "subnet_ids": "TemplateSerializer._apply_nullable_defaults",
        "network_zones": "TemplateSerializer._apply_nullable_defaults",
        "machine_types": "TemplateSerializer._apply_nullable_defaults",
        "machine_types_ondemand": "TemplateSerializer._apply_nullable_defaults",
        "machine_types_priority": "TemplateSerializer._apply_nullable_defaults",
    },
}


def _required_pydantic_fields(model: type[BaseModel]) -> set[str]:
    """Return the set of pydantic field names that are required.

    A field is "required" when ``default`` is ``PydanticUndefined`` AND
    ``default_factory`` is also unset — i.e. the constructor must be
    given a value.
    """
    required: set[str] = set()
    for name, info in model.model_fields.items():
        if info.default is not PydanticUndefined:
            continue
        if info.default_factory is not None:
            continue
        required.add(name)
    return required


def _sql_columns(model: type) -> dict[str, bool]:
    """Return SQL column name → nullable mapping for the ORM model."""
    table = model.__table__  # type: ignore[attr-defined]
    return {col.name: bool(col.nullable) for col in table.columns}


@pytest.mark.parametrize(
    ("aggregate", "sql_model"),
    AGGREGATE_TO_MODEL,
    ids=[a.__name__ for a, _ in AGGREGATE_TO_MODEL],
)
def test_required_pydantic_fields_match_sql_not_null(
    aggregate: type[BaseModel],
    sql_model: type,
) -> None:
    """Every required pydantic field must map to a NOT NULL SQL column.

    Mismatches surface in production as load-time aggregate validation
    failures (the SQL row exists with NULL but the aggregate refuses
    to construct from it). Failing here is much cheaper.
    """
    required_pydantic = _required_pydantic_fields(aggregate)
    sql_nullable = _sql_columns(sql_model)
    allowed = ALLOWED_MISMATCHES.get(aggregate, {})

    drift: list[str] = []
    for field in sorted(required_pydantic):
        if field in allowed:
            continue
        if field not in sql_nullable:
            drift.append(f"{field}: required in pydantic but no SQL column")
            continue
        if sql_nullable[field]:
            drift.append(f"{field}: required in pydantic but SQL column is nullable")

    assert not drift, (
        f"{aggregate.__name__} ↔ {sql_model.__name__} drift:\n  "
        + "\n  ".join(drift)
        + "\n\nFix: either add `nullable=False` on the SQL column "
        "(+ alembic migration) or document the divergence in "
        "ALLOWED_MISMATCHES with a reason."
    )


def _default_factory_pydantic_fields(model: type[BaseModel]) -> set[str]:
    """Return pydantic field names that have a ``default_factory`` (list/dict fields)."""
    return {name for name, info in model.model_fields.items() if info.default_factory is not None}


@pytest.mark.parametrize(
    ("aggregate", "sql_model"),
    AGGREGATE_TO_MODEL,
    ids=[a.__name__ for a, _ in AGGREGATE_TO_MODEL],
)
def test_default_factory_pydantic_fields_have_nullable_safe_coercion(
    aggregate: type[BaseModel],
    sql_model: type,
) -> None:
    """Every default_factory pydantic field must be either NOT NULL in SQL or
    listed in NULLABLE_WITH_COERCION (with a named read-side coercion site).

    A field with default_factory=list or default_factory=dict will silently
    receive None if the SQL column is nullable and the row was written before
    the column was constrained.  The None bypasses the factory and causes a
    model_validate failure.  This test ensures that each such field is either
    protected at the DB layer (NOT NULL) or has an explicit read-side coercion
    in _apply_nullable_defaults.
    """
    factory_fields = _default_factory_pydantic_fields(aggregate)
    sql_nullable = _sql_columns(sql_model)
    coercion_allowlist = NULLABLE_WITH_COERCION.get(aggregate, {})

    unguarded: list[str] = []
    for field in sorted(factory_fields):
        if field not in sql_nullable:
            # No SQL column — field is computed or excluded from persistence.
            continue
        if not sql_nullable[field]:
            # SQL column is NOT NULL — DB-layer protection is sufficient.
            continue
        if field in coercion_allowlist:
            # Explicitly allowed: read-side coercion is in place.
            continue
        unguarded.append(f"{field}: nullable SQL column with default_factory but no coercion entry")

    assert not unguarded, (
        f"{aggregate.__name__} ↔ {sql_model.__name__} unguarded nullable list/dict columns:\n  "
        + "\n  ".join(unguarded)
        + "\n\nFix: either add `nullable=False` on the SQL column "
        "(+ alembic migration) or add the field to NULLABLE_WITH_COERCION "
        "with the _apply_nullable_defaults coercion site."
    )
