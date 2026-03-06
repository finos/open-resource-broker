"""Batching policy for provisioning requests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BatchingPolicy:
    """Provider-agnostic batching policy for provisioning attempts.

    Splits a total count into batches no larger than max_batch_size.
    """

    max_batch_size: int = 1000

    def split(self, total: int) -> list[int]:
        """Split total into a list of batch sizes summing to total."""
        if total <= 0:
            return []
        batches = []
        remaining = total
        while remaining > 0:
            batch = min(remaining, self.max_batch_size)
            batches.append(batch)
            remaining -= batch
        return batches
