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
