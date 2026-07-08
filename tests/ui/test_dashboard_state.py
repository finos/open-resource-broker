"""M4 — Tests for DashboardState.duration_scatter_data and p95_duration.

Cases:
  (a) tz-aware ISO with +00:00 suffix
  (b) Z suffix (normalised to UTC by _parse_iso)
  (c) naive ISO (server default) → normalised to UTC
  (d) duration=None → falls back to completed_at - created_at
  (e) empty durations list → p95_duration returns 0
  (f) single duration → p95 is that value
  (g) p95 boundary — 100 durations, 95th value
"""

from __future__ import annotations

import datetime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(recent_requests: list) -> object:
    """Return a minimal DashboardState instance with only recent_requests set."""
    from orb.ui.pages.dashboard import DashboardState

    s = DashboardState.__new__(DashboardState)
    s._machines = {}
    s._requests = {}
    s._templates = {}
    s.recent_requests = recent_requests
    s.loading = False
    s.error = ""
    s.last_refresh = ""
    s._poll_started = False
    return s


def _req(
    *,
    created_at: str = "",
    completed_at: str = "",
    duration=None,
) -> dict:
    """Build a minimal request dict for dashboard scatter / p95 tests."""
    return {
        "request_id": "r-test",
        "status": "complete",
        "created_at": created_at,
        "completed_at": completed_at,
        "duration": duration,
    }


def _epoch_of(dt: datetime.datetime) -> int:
    _ep = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    return int((dt - _ep).total_seconds())


# ---------------------------------------------------------------------------
# duration_scatter_data
# ---------------------------------------------------------------------------


class TestDurationScatterData:
    """DashboardState.duration_scatter_data computed var."""

    def test_tz_aware_iso_plus_utc(self):
        """(a) tz-aware ISO with +00:00 → parsed correctly, x = epoch, y = seconds."""
        from orb.ui.pages.dashboard import DashboardState

        created = "2024-06-01T10:00:00+00:00"
        completed = "2024-06-01T10:01:00+00:00"

        s = _make_state([_req(created_at=created, completed_at=completed)])
        result = DashboardState.duration_scatter_data(s)

        assert len(result) == 1
        pt = result[0]
        assert pt["y"] == 60  # 60 seconds
        expected_x = _epoch_of(
            datetime.datetime(2024, 6, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        )
        assert pt["x"] == expected_x

    def test_z_suffix_normalised_to_utc(self):
        """(b) Z suffix → fromisoformat handles it after replace('Z', '+00:00')."""
        from orb.ui.pages.dashboard import DashboardState

        created = "2024-06-01T10:00:00Z"
        completed = "2024-06-01T10:02:30Z"

        s = _make_state([_req(created_at=created, completed_at=completed)])
        result = DashboardState.duration_scatter_data(s)

        assert len(result) == 1
        assert result[0]["y"] == 150  # 2 min 30 s

    def test_naive_iso_normalised_to_utc(self):
        """(c) Naive ISO (no tzinfo) → treated as UTC, x and y computed correctly."""
        from orb.ui.pages.dashboard import DashboardState

        created = "2024-06-01T10:00:00"
        completed = "2024-06-01T10:00:45"

        s = _make_state([_req(created_at=created, completed_at=completed)])
        result = DashboardState.duration_scatter_data(s)

        assert len(result) == 1
        assert result[0]["y"] == 45

    def test_duration_field_none_falls_back_to_timestamp_diff(self):
        """(d) duration=None → compute from completed_at - created_at."""
        from orb.ui.pages.dashboard import DashboardState

        s = _make_state(
            [
                _req(
                    created_at="2024-06-01T12:00:00Z",
                    completed_at="2024-06-01T12:05:00Z",
                    duration=None,
                )
            ]
        )
        result = DashboardState.duration_scatter_data(s)

        assert len(result) == 1
        assert result[0]["y"] == 300  # 5 min

    def test_precomputed_duration_field_used_directly(self):
        """When duration is supplied, no timestamp arithmetic should alter the value."""
        from orb.ui.pages.dashboard import DashboardState

        s = _make_state(
            [
                _req(
                    created_at="2024-06-01T12:00:00Z",
                    completed_at="2024-06-01T12:05:00Z",
                    duration=42,  # precomputed — overrides timestamp diff
                )
            ]
        )
        result = DashboardState.duration_scatter_data(s)

        assert len(result) == 1
        assert result[0]["y"] == 42

    def test_missing_timestamps_excluded_from_result(self):
        """Requests with no created_at/completed_at and duration=None → excluded."""
        from orb.ui.pages.dashboard import DashboardState

        s = _make_state([_req(created_at="", completed_at="", duration=None)])
        result = DashboardState.duration_scatter_data(s)

        assert result == []


# ---------------------------------------------------------------------------
# p95_duration
# ---------------------------------------------------------------------------


class TestP95Duration:
    """DashboardState.p95_duration computed var."""

    def test_empty_durations_returns_zero(self):
        """(e) No requests with computable duration → p95 is 0."""
        from orb.ui.pages.dashboard import DashboardState

        s = _make_state([])
        assert DashboardState.p95_duration(s) == 0

    def test_single_duration_returns_that_value(self):
        """(f) Single request → p95 equals that request's duration."""
        from orb.ui.pages.dashboard import DashboardState

        s = _make_state([_req(duration=77)])
        assert DashboardState.p95_duration(s) == 77

    def test_p95_boundary_100_durations(self):
        """(g) 100 sorted durations → p95 is the value at index 94 (0-based)."""
        from orb.ui.pages.dashboard import DashboardState

        # Durations: 1, 2, ..., 100. Sorted ascending → index 94 = value 95.
        # p95 formula: idx = max(0, int(100 * 0.95) - 1) = max(0, 95 - 1) = 94.
        requests = [_req(duration=i) for i in range(1, 101)]
        s = _make_state(requests)

        result = DashboardState.p95_duration(s)
        assert result == 95

    def test_p95_uses_timestamp_diff_when_duration_none(self):
        """p95 falls back to completed_at - created_at when duration field is None."""
        from orb.ui.pages.dashboard import DashboardState

        r = _req(
            created_at="2024-06-01T10:00:00Z",
            completed_at="2024-06-01T10:02:00Z",
            duration=None,
        )
        s = _make_state([r])
        assert DashboardState.p95_duration(s) == 120

    def test_p95_three_values_picks_highest_in_range(self):
        """Three values [10, 50, 90] → p95 index = max(0, int(3*0.95)-1) = max(0, 1) = 1 → 50."""
        from orb.ui.pages.dashboard import DashboardState

        requests = [_req(duration=d) for d in [90, 10, 50]]  # unsorted input
        s = _make_state(requests)

        result = DashboardState.p95_duration(s)
        # sorted = [10, 50, 90]; idx = max(0, int(3*0.95)-1) = max(0, 2-1) = 1
        assert result == 50
