"""Save aggregates with optimistic locking where the storage strategy supports it.

A strategy that sets ``supports_versioned_save`` takes the version an aggregate
was loaded at (or last saved at) and refuses to overwrite a row that moved on.
Other strategies keep their plain ``save`` calls.
"""

from typing import Any


def supports_versioned_save(strategy: Any) -> bool:
    """Whether the strategy implements the versioned save protocol."""
    return getattr(strategy, "supports_versioned_save", False) is True


def _record_saved(aggregate: Any, written: int) -> None:
    aggregate.persisted_revision.mark_saved(written)
    if aggregate.version != written:
        # Keep the aggregate equal to what is stored
        aggregate.version = written


def save_one(strategy: Any, entity_id: str, data: dict[str, Any], aggregate: Any) -> None:
    """Save one aggregate; the baseline moves only after the write committed."""
    if supports_versioned_save(strategy):
        written = strategy.save_versioned(entity_id, data, aggregate.persisted_revision.value)
        _record_saved(aggregate, written)
    else:
        strategy.save(entity_id, data)


def save_many(strategy: Any, entries: list[tuple[str, dict[str, Any], Any]]) -> bool:
    """Save aggregates in one transaction if the strategy supports it.

    Returns False when the strategy has no versioned batch, so the caller can use
    its own fallback.
    """
    if not supports_versioned_save(strategy):
        return False
    written = strategy.save_versioned_batch(
        [(entity_id, data, agg.persisted_revision.value) for entity_id, data, agg in entries]
    )
    for (_, _, aggregate), version in zip(entries, written):
        _record_saved(aggregate, version)
    return True
