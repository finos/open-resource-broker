"""A manually advanced stand-in for the ``time`` module."""


class ManualClock:
    """Exposes ``time()`` and ``advance()``; time moves only when advanced."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
