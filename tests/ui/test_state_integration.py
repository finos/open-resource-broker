"""Phase 2 — state-integration + chaining tests.

Covers behaviours that need cross-state or yield-chain semantics:

1. yield / await chaining verified: filter setters reset pagination and trigger
   load — tested via mocked api calls on real (testable) state instances.
2. Substate inheritance sanity: provider_schemas visibility via Design C.
3. LocalStorage round-trip semantics for AppState sidebar + onboarding.
4. poll_health refactor: _poll_health_tick extracted and tested directly.
5. DashboardState fallback path: aggregate zero + list non-zero.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Harness helpers (mirrors test_state_load_more.py pattern)
# ---------------------------------------------------------------------------


def _make_testable_subclass(StateClass):
    """Return a subclass with no-op async context manager for ``async with self:``."""

    class _TestableState(StateClass):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    _TestableState.__name__ = f"Testable{StateClass.__name__}"
    _TestableState.__qualname__ = _TestableState.__name__
    return _TestableState


# ---------------------------------------------------------------------------
# 1. yield / await chaining — filter setters reset pagination and call load
# ---------------------------------------------------------------------------


class TestMachinesStateFilterChaining:
    """MachinesState.set_status_filter and set_provider_filter reset cursors + call load."""

    def _make_state(self):
        from orb.ui.pages.machines import MachinesState

        TestableState = _make_testable_subclass(MachinesState)
        s = TestableState.__new__(TestableState)
        s.status_filter = "all"
        s.provider_filter = "All"
        s.next_cursor = "some-cursor"
        s.api_total_count = 42
        s.loading = False
        s.error = ""
        s.machines = []
        s.page_size = 200
        s.last_sync_time = ""
        s._normalize_visible_columns = lambda: None
        s.visible_columns = ""
        return s

    @pytest.mark.asyncio
    async def test_set_status_filter_resets_cursor_and_total(self):
        """After set_status_filter: next_cursor == '' and api_total_count == 0."""
        s = self._make_state()

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(
                return_value={"machines": [], "next_cursor": "", "total_count": 0}
            )
            await s.set_status_filter("running")

        assert s.next_cursor == ""
        assert s.api_total_count == 0
        assert s.status_filter == "running"

    @pytest.mark.asyncio
    async def test_set_status_filter_calls_load(self):
        """set_status_filter must call the API (i.e. trigger a load)."""
        s = self._make_state()

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(
                return_value={"machines": [{"machine_id": "i-123"}], "total_count": 1}
            )
            await s.set_status_filter("running")

        mock_api.list_machines.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_provider_filter_resets_cursor_and_total(self):
        """After set_provider_filter: next_cursor == '' and api_total_count == 0."""
        s = self._make_state()

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(
                return_value={"machines": [], "next_cursor": "", "total_count": 0}
            )
            await s.set_provider_filter("aws")

        assert s.next_cursor == ""
        assert s.api_total_count == 0
        assert s.provider_filter == "aws"

    @pytest.mark.asyncio
    async def test_set_provider_filter_calls_load(self):
        """set_provider_filter must trigger a list_machines call."""
        s = self._make_state()

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(return_value={"machines": [], "total_count": 0})
            await s.set_provider_filter("aws")

        mock_api.list_machines.assert_awaited_once()


class TestRequestsStateFilterChaining:
    """RequestsState.set_tab and set_provider_filter trigger load."""

    def _make_state(self):
        from orb.ui.pages.requests import RequestsState

        TestableState = _make_testable_subclass(RequestsState)
        s = TestableState.__new__(TestableState)
        s.tab = "all"
        s.provider_filter = "All"
        s.next_cursor = "cursor-r"
        s.api_total_count = 10
        s.loading = False
        s.error = ""
        s.requests = []
        s.page_size = 200
        s.last_refresh = ""
        s._normalize_visible_columns = lambda: None
        s.visible_columns = ""
        return s

    @pytest.mark.asyncio
    async def test_set_tab_yields_load(self):
        """set_tab is an async generator that yields RequestsState.load.

        We collect all yielded items and assert the last is RequestsState.load.
        """
        from orb.ui.pages.requests import RequestsState

        s = self._make_state()
        # set_tab yields RequestsState.load — collect the generator items
        gen = s.set_tab("complete")
        items = []
        async for item in gen:
            items.append(item)

        assert len(items) >= 1, "set_tab should yield at least one item"
        assert items[-1] is RequestsState.load

    @pytest.mark.asyncio
    async def test_set_tab_updates_tab_value(self):
        """set_tab must update self.tab before yielding."""
        s = self._make_state()

        gen = s.set_tab("failed")
        # Consume the generator (allows the body before yield to execute)
        async for _ in gen:
            pass

        assert s.tab == "failed"

    @pytest.mark.asyncio
    async def test_set_provider_filter_resets_cursors(self):
        """set_provider_filter resets next_cursor and api_total_count before load."""
        s = self._make_state()

        with patch("orb.ui.pages.requests.api") as mock_api:
            mock_api.list_requests = AsyncMock(return_value={"requests": [], "total_count": 0})
            await s.set_provider_filter("aws")

        assert s.next_cursor == ""
        assert s.api_total_count == 0
        assert s.provider_filter == "aws"


class TestTemplatesStateFilterChaining:
    """TemplatesState.set_provider_filter triggers load; set_filter is client-side."""

    def _make_state(self):
        from orb.ui.pages.templates import TemplatesState

        TestableState = _make_testable_subclass(TemplatesState)
        s = TestableState.__new__(TestableState)
        s.active_filter = "All"
        s.provider_filter = "All"
        s.loading = False
        s.error = ""
        s.templates = []
        s.page_size = 200
        s.last_refresh = ""
        s.next_cursor = ""
        s.api_total_count = 0
        s._normalize_visible_columns = lambda: None
        s.visible_columns = ""
        return s

    def test_set_filter_updates_active_filter(self):
        """set_filter is synchronous and only updates active_filter (client-side)."""
        s = self._make_state()
        s.set_filter("aws")
        assert s.active_filter == "aws"

    def test_set_filter_all_clears_filter(self):
        """set_filter('All') resets to no-filter state."""
        s = self._make_state()
        s.set_filter("aws")
        s.set_filter("All")
        assert s.active_filter == "All"

    @pytest.mark.asyncio
    async def test_set_provider_filter_calls_load(self):
        """set_provider_filter must trigger the API list call."""
        s = self._make_state()

        with patch("orb.ui.pages.templates.api") as mock_api:
            mock_api.list_templates = AsyncMock(return_value={"templates": [], "total_count": 0})
            await s.set_provider_filter("aws")

        mock_api.list_templates.assert_awaited_once()
        assert s.provider_filter == "aws"


# ---------------------------------------------------------------------------
# 2. Substate inheritance sanity — provider_schemas visibility
# ---------------------------------------------------------------------------


class TestSubstateProviderSchemasVisibility:
    """Verify Design C: MachinesState/RequestsState/TemplatesState all inherit
    provider_schemas from AppState via substate.  Setting the field on an
    instance and calling dynamic_columns must return a non-empty list.
    """

    _AWS_SCHEMA = {
        "aws": [
            {
                "key": "aws_test",
                "path": "provider_data.test",
                "label": "Test",
                "kind": "text",
                "resource_type": "machines",
            }
        ]
    }

    def _machines_state(self):
        from orb.ui.pages.machines import MachinesState

        s = MachinesState.__new__(MachinesState)
        s.provider_schemas = self._AWS_SCHEMA
        s.provider_filter = "aws"
        return s

    def _requests_state(self):
        from orb.ui.pages.requests import RequestsState

        s = RequestsState.__new__(RequestsState)
        s.provider_schemas = self._AWS_SCHEMA
        s.provider_filter = "aws"
        return s

    def _templates_state(self):
        from orb.ui.pages.templates import TemplatesState

        s = TemplatesState.__new__(TemplatesState)
        s.provider_schemas = self._AWS_SCHEMA
        s.provider_filter = "aws"
        return s

    def test_machines_dynamic_columns_non_empty(self):
        """MachinesState.dynamic_columns returns columns from injected schemas."""
        from orb.ui.pages.machines import MachinesState

        s = self._machines_state()
        cols = MachinesState.dynamic_columns(s)
        assert isinstance(cols, list)
        assert len(cols) >= 1, "Expected at least one dynamic column from AWS schema"

    def test_requests_dynamic_columns_non_empty(self):
        """RequestsState.dynamic_columns returns columns from injected schemas."""
        from orb.ui.pages.requests import RequestsState

        # requests schemas use resource_type="requests"
        schemas = {
            "aws": [
                {
                    "key": "req_test",
                    "path": "provider_data.req_test",
                    "label": "Req Test",
                    "kind": "text",
                    "resource_type": "requests",
                }
            ]
        }
        s = RequestsState.__new__(RequestsState)
        s.provider_schemas = schemas
        s.provider_filter = "aws"
        cols = RequestsState.dynamic_columns(s)
        assert isinstance(cols, list)
        assert len(cols) >= 1, "Expected at least one dynamic column from AWS schema"

    def test_templates_dynamic_columns_non_empty(self):
        """TemplatesState.dynamic_columns returns columns from injected schemas."""
        from orb.ui.pages.templates import TemplatesState

        # templates schemas use resource_type="templates"
        schemas = {
            "aws": [
                {
                    "key": "tmpl_test",
                    "path": "provider_data.tmpl_test",
                    "label": "Tmpl Test",
                    "kind": "text",
                    "resource_type": "templates",
                }
            ]
        }
        s = TemplatesState.__new__(TemplatesState)
        s.provider_schemas = schemas
        s.provider_filter = "aws"
        cols = TemplatesState.dynamic_columns(s)
        assert isinstance(cols, list)
        assert len(cols) >= 1, "Expected at least one dynamic column from AWS schema"

    def test_canary_machines_provider_schemas_field_exists(self):
        """Canary: if MachinesState reverts to rx.State (losing AppState inheritance),
        setting provider_schemas on an instance would still work but dynamic_columns
        would use an empty dict.  Here we verify the field is visible after set.
        """
        s = self._machines_state()
        assert s.provider_schemas == self._AWS_SCHEMA


# ---------------------------------------------------------------------------
# 3. LocalStorage round-trip semantics
# ---------------------------------------------------------------------------


class TestAppStateLocalStorage:
    """AppState LocalStorage-backed fields behave correctly in pure-Python context."""

    def _make_state(self):
        from orb.ui.state import AppState

        s = AppState.__new__(AppState)
        # Apply LocalStorage defaults (conftest stub sets str defaults)
        s.sidebar_collapsed = "false"
        s._onboarding_dismissed_raw = "false"
        return s

    def test_toggle_sidebar_sets_true_when_false(self):
        """toggle_sidebar should flip 'false' → 'true'."""
        from orb.ui.state import AppState

        s = self._make_state()
        # is_collapsed reads sidebar_collapsed; precompute for the var chain
        object.__setattr__(s, "is_collapsed", AppState.is_collapsed(s))
        AppState.toggle_sidebar(s)
        assert s.sidebar_collapsed == "true"

    def test_toggle_sidebar_sets_false_when_true(self):
        """toggle_sidebar should flip 'true' → 'false'."""
        from orb.ui.state import AppState

        s = self._make_state()
        s.sidebar_collapsed = "true"
        object.__setattr__(s, "is_collapsed", AppState.is_collapsed(s))
        AppState.toggle_sidebar(s)
        assert s.sidebar_collapsed == "false"

    def test_is_collapsed_true_when_sidebar_collapsed_true(self):
        """is_collapsed computed var returns True when sidebar_collapsed == 'true'."""
        from orb.ui.state import AppState

        s = self._make_state()
        s.sidebar_collapsed = "true"
        assert AppState.is_collapsed(s) is True

    def test_is_collapsed_false_when_sidebar_collapsed_false(self):
        """is_collapsed returns False when sidebar_collapsed == 'false'."""
        from orb.ui.state import AppState

        s = self._make_state()
        assert AppState.is_collapsed(s) is False

    def test_double_toggle_sidebar_returns_to_original(self):
        """Two toggles must return to the original collapsed state."""
        from orb.ui.state import AppState

        s = self._make_state()
        for _ in range(2):
            object.__setattr__(s, "is_collapsed", AppState.is_collapsed(s))
            AppState.toggle_sidebar(s)
        assert s.sidebar_collapsed == "false"

    def test_dismiss_onboarding_sets_raw_to_true(self):
        """dismiss_onboarding persists 'true' in _onboarding_dismissed_raw."""
        from orb.ui.state import AppState

        s = self._make_state()
        AppState.dismiss_onboarding(s)
        assert s._onboarding_dismissed_raw == "true"

    def test_onboarding_dismissed_true_after_dismiss(self):
        """onboarding_dismissed computed var returns True after dismiss."""
        from orb.ui.state import AppState

        s = self._make_state()
        AppState.dismiss_onboarding(s)
        assert AppState.onboarding_dismissed(s) is True

    def test_onboarding_dismissed_false_initially(self):
        """onboarding_dismissed returns False before dismiss."""
        from orb.ui.state import AppState

        s = self._make_state()
        assert AppState.onboarding_dismissed(s) is False


# ---------------------------------------------------------------------------
# 4. poll_health refactor — _poll_health_tick extracted for unit testing
# ---------------------------------------------------------------------------


class TestPollHealthTick:
    """AppState._poll_health_tick executes one poll iteration without sleeping."""

    def _make_state_with_async_cm(self):
        from orb.ui.state import AppState

        TestableState = _make_testable_subclass(AppState)
        s = TestableState.__new__(TestableState)
        s.health = {}
        s.info = {}
        s.health_error = ""
        s._poll_started = False
        return s

    @pytest.mark.asyncio
    async def test_poll_health_tick_sets_health_on_success(self):
        """_poll_health_tick must set self.health from the API response."""
        s = self._make_state_with_async_cm()

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_health = AsyncMock(return_value={"status": "ok", "checks": {}})
            mock_api.get_info = AsyncMock(return_value={"version": "1.2.3"})
            await s._poll_health_tick()

        assert s.health == {"status": "ok", "checks": {}}
        assert s.info == {"version": "1.2.3"}
        assert s.health_error == ""

    @pytest.mark.asyncio
    async def test_poll_health_tick_sets_error_on_http_failure(self):
        """_poll_health_tick must set health_error when get_health raises httpx.HTTPError."""
        import httpx

        s = self._make_state_with_async_cm()

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_health = AsyncMock(side_effect=httpx.HTTPError("connection refused"))
            mock_api.get_info = AsyncMock(return_value={})
            await s._poll_health_tick()

        assert s.health_error == "connection refused"

    @pytest.mark.asyncio
    async def test_poll_health_tick_sets_error_on_generic_exception(self):
        """_poll_health_tick must set health_error on any exception."""
        s = self._make_state_with_async_cm()

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_health = AsyncMock(side_effect=RuntimeError("unexpected"))
            mock_api.get_info = AsyncMock(return_value={})
            await s._poll_health_tick()

        assert "unexpected" in s.health_error

    @pytest.mark.asyncio
    async def test_poll_health_tick_clears_error_on_recovery(self):
        """A successful tick after an error must clear health_error."""
        s = self._make_state_with_async_cm()
        s.health_error = "previous error"

        with patch("orb.ui.state.api") as mock_api:
            mock_api.get_health = AsyncMock(return_value={"status": "ok"})
            mock_api.get_info = AsyncMock(return_value={})
            await s._poll_health_tick()

        assert s.health_error == ""


# ---------------------------------------------------------------------------
# 5. DashboardState fallback path unit test
# ---------------------------------------------------------------------------


class TestDashboardStateFallback:
    """DashboardState.load fallback: aggregate templates.total == 0 → list endpoint."""

    def _make_state(self):
        from orb.ui.pages.dashboard import DashboardState

        TestableState = _make_testable_subclass(DashboardState)
        s = TestableState.__new__(TestableState)
        s._machines = {}
        s._requests = {}
        s._templates = {}
        s.recent_requests = []
        s.loading = False
        s.error = ""
        s.last_refresh = ""
        s._poll_started = False
        return s

    @pytest.mark.asyncio
    async def test_aggregate_wins_when_nonzero(self):
        """(a) When aggregate returns templates.total > 0, that value is used."""
        s = self._make_state()

        with patch("orb.ui.pages.dashboard.api") as mock_api:
            mock_api.get_dashboard_summary = AsyncMock(
                return_value={
                    "machines": {"total": 5, "by_status": {}},
                    "requests": {"total": 3, "in_flight": 1, "by_status": {}},
                    "templates": {"total": 7, "by_provider_api": {}},
                    "recent_activity": [],
                }
            )
            # list_templates should NOT be called when aggregate is non-zero
            mock_api.list_templates = AsyncMock(return_value={"templates": [], "total_count": 0})
            await s.load()

        from orb.ui.pages.dashboard import DashboardState

        assert DashboardState.total_templates(s) == 7
        mock_api.list_templates.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_aggregate_zero_list_nonzero_list_wins(self):
        """(b) Aggregate returns templates.total == 0, list endpoint returns 4 → use 4."""
        s = self._make_state()

        with patch("orb.ui.pages.dashboard.api") as mock_api:
            mock_api.get_dashboard_summary = AsyncMock(
                return_value={
                    "machines": {"total": 0, "by_status": {}},
                    "requests": {"total": 0, "in_flight": 0, "by_status": {}},
                    "templates": {"total": 0, "by_provider_api": {}},
                    "recent_activity": [],
                }
            )
            mock_api.list_templates = AsyncMock(return_value={"templates": [], "total_count": 4})
            await s.load()

        from orb.ui.pages.dashboard import DashboardState

        assert DashboardState.total_templates(s) == 4

    @pytest.mark.asyncio
    async def test_both_zero_tile_shows_zero(self):
        """(c) Both aggregate and list return 0 → total_templates == 0."""
        s = self._make_state()

        with patch("orb.ui.pages.dashboard.api") as mock_api:
            mock_api.get_dashboard_summary = AsyncMock(
                return_value={
                    "machines": {"total": 0, "by_status": {}},
                    "requests": {"total": 0, "in_flight": 0, "by_status": {}},
                    "templates": {"total": 0, "by_provider_api": {}},
                    "recent_activity": [],
                }
            )
            mock_api.list_templates = AsyncMock(return_value={"templates": [], "total_count": 0})
            await s.load()

        from orb.ui.pages.dashboard import DashboardState

        assert DashboardState.total_templates(s) == 0
