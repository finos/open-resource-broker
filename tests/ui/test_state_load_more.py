"""H9 — Rewritten load_more tests for Machines / Requests / Templates.

Each test calls the real ``load_more`` coroutine on a real (lightweight)
state instance.  The Reflex lock (``async with self:``) is satisfied by
dynamically creating a per-test subclass that adds ``__aenter__`` /
``__aexit__`` so that the ``async with self:`` pattern in the background
events works in a plain pytest-asyncio environment without starting the
Reflex event loop.

Python's data model requires ``__aenter__`` / ``__aexit__`` to be looked
up on the *type*, not the instance.  We therefore create a one-off
subclass per state class and instantiate that instead of patching instance
attributes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Harness helper
# ---------------------------------------------------------------------------


def _make_testable_subclass(StateClass):
    """Return a subclass of *StateClass* with async context-manager support.

    ``async with self:`` in Reflex's background event handlers acquires a
    per-state asyncio lock.  In unit tests there is no Reflex runtime, so
    we add a no-op async CM that simply yields the instance.
    """

    class _TestableState(StateClass):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    _TestableState.__name__ = f"Testable{StateClass.__name__}"
    _TestableState.__qualname__ = _TestableState.__name__
    return _TestableState


# ---------------------------------------------------------------------------
# MachinesState.load_more
# ---------------------------------------------------------------------------


class TestMachinesStateLoadMore:
    """Calls the real load_more on a MachinesState instance."""

    def _make_state(self, *, page1=None, next_cursor="cursor-m"):
        from orb.ui.pages.machines import MachinesState

        TestableState = _make_testable_subclass(MachinesState)
        s = TestableState.__new__(TestableState)
        s.machines = list(page1 or [])
        s.loading_more = False
        s.next_cursor = next_cursor
        s.status_filter = "all"
        s.page_size = 200
        s.error = ""
        s.api_total_count = len(s.machines)
        return s

    @pytest.mark.asyncio
    async def test_load_more_appends_rows(self):
        """load_more must concatenate new rows to existing machines, not replace."""
        page1 = [{"machine_id": "i-page1", "status": "running"}]
        page2 = [{"machine_id": "i-page2", "status": "running"}]

        s = self._make_state(page1=page1)

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(
                return_value={
                    "machines": page2,
                    "next_cursor": "",
                    "total_count": 2,
                }
            )
            await s.load_more()

        assert len(s.machines) == 2
        ids = [m["machine_id"] for m in s.machines]
        assert "i-page1" in ids
        assert "i-page2" in ids
        assert s.next_cursor == ""
        assert s.api_total_count == 2

    @pytest.mark.asyncio
    async def test_load_more_no_op_when_cursor_empty(self):
        """load_more must exit immediately (no API call) when next_cursor is empty."""
        page1 = [{"machine_id": "i-existing", "status": "running"}]
        s = self._make_state(page1=page1, next_cursor="")

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(
                return_value={"machines": [], "next_cursor": "", "total_count": 0}
            )
            await s.load_more()

        mock_api.list_machines.assert_not_called()
        assert len(s.machines) == 1
        assert s.loading_more is False

    @pytest.mark.asyncio
    async def test_load_more_no_op_when_already_loading(self):
        """load_more must exit immediately when loading_more is already True."""
        page1 = [{"machine_id": "i-existing", "status": "running"}]
        s = self._make_state(page1=page1)
        s.loading_more = True  # simulate concurrent invocation

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(
                return_value={"machines": [], "next_cursor": "", "total_count": 0}
            )
            await s.load_more()

        mock_api.list_machines.assert_not_called()
        assert len(s.machines) == 1

    @pytest.mark.asyncio
    async def test_load_more_sets_error_on_api_failure(self):
        """load_more must set error field and clear loading_more on API exception."""
        s = self._make_state(page1=[{"machine_id": "i-existing", "status": "running"}])

        with patch("orb.ui.pages.machines.api") as mock_api:
            mock_api.list_machines = AsyncMock(side_effect=RuntimeError("network error"))
            await s.load_more()

        assert "Failed to load more machines" in s.error
        assert s.loading_more is False


# ---------------------------------------------------------------------------
# RequestsState.load_more
# ---------------------------------------------------------------------------


class TestRequestsStateLoadMore:
    """Calls the real load_more on a RequestsState instance."""

    def _make_state(self, *, page1=None, next_cursor="cursor-r", tab="all"):
        from orb.ui.pages.requests import RequestsState

        TestableState = _make_testable_subclass(RequestsState)
        s = TestableState.__new__(TestableState)
        s.requests = list(page1 or [])
        s.loading_more = False
        s.next_cursor = next_cursor
        s.tab = tab
        s.page_size = 200
        s.error = ""
        s.api_total_count = len(s.requests)
        return s

    @pytest.mark.asyncio
    async def test_load_more_appends_and_sorts_rows(self):
        """load_more must merge and sort (newest first) new rows onto existing."""
        page1 = [{"request_id": "req-p1", "status": "complete", "created_at": "2024-01-01"}]
        page2 = [{"request_id": "req-p2", "status": "complete", "created_at": "2024-01-02"}]

        s = self._make_state(page1=page1)

        with patch("orb.ui.pages.requests.api") as mock_api:
            mock_api.list_requests = AsyncMock(
                return_value={
                    "requests": page2,
                    "next_cursor": "",
                    "total_count": 2,
                }
            )
            await s.load_more()

        assert len(s.requests) == 2
        ids = [r["request_id"] for r in s.requests]
        assert "req-p1" in ids
        assert "req-p2" in ids
        # Sorted newest-first: req-p2 (2024-01-02) before req-p1 (2024-01-01)
        assert s.requests[0]["request_id"] == "req-p2"
        assert s.next_cursor == ""
        assert s.api_total_count == 2

    @pytest.mark.asyncio
    async def test_load_more_no_op_when_cursor_empty(self):
        """load_more must be a no-op when next_cursor is empty."""
        page1 = [{"request_id": "req-1", "status": "complete", "created_at": ""}]
        s = self._make_state(page1=page1, next_cursor="")

        with patch("orb.ui.pages.requests.api") as mock_api:
            mock_api.list_requests = AsyncMock(
                return_value={"requests": [], "next_cursor": "", "total_count": 0}
            )
            await s.load_more()

        mock_api.list_requests.assert_not_called()
        assert len(s.requests) == 1

    @pytest.mark.asyncio
    async def test_load_more_returns_tab_uses_return_requests_endpoint(self):
        """load_more with tab='returns' calls list_return_requests, not list_requests."""
        page1 = [{"request_id": "ret-1", "status": "complete", "created_at": "2024-03-01"}]
        page2 = [{"request_id": "ret-2", "status": "complete", "created_at": "2024-03-02"}]

        s = self._make_state(page1=page1, tab="returns")

        with patch("orb.ui.pages.requests.api") as mock_api:
            mock_api.list_return_requests = AsyncMock(
                return_value={
                    "requests": page2,
                    "next_cursor": "",
                    "total_count": 2,
                }
            )
            await s.load_more()

        mock_api.list_return_requests.assert_called_once()
        assert len(s.requests) == 2

    @pytest.mark.asyncio
    async def test_load_more_sets_error_on_api_failure(self):
        """load_more must set error and clear loading_more on exception."""
        page1 = [{"request_id": "req-1", "status": "complete", "created_at": ""}]
        s = self._make_state(page1=page1)

        with patch("orb.ui.pages.requests.api") as mock_api:
            mock_api.list_requests = AsyncMock(side_effect=RuntimeError("timeout"))
            await s.load_more()

        assert "Failed to load more requests" in s.error
        assert s.loading_more is False


# ---------------------------------------------------------------------------
# TemplatesState.load_more
# ---------------------------------------------------------------------------


class TestTemplatesStateLoadMore:
    """Calls the real load_more on a TemplatesState instance."""

    def _make_state(self, *, page1_raw=None, next_cursor="cursor-t"):
        from orb.ui.pages.templates import TemplatesState, _template_to_display

        TestableState = _make_testable_subclass(TemplatesState)
        s = TestableState.__new__(TestableState)
        raw = page1_raw or []
        s.templates = [_template_to_display(t) for t in raw]
        s.loading_more = False
        s.next_cursor = next_cursor
        s.page_size = 200
        s.error = ""
        s.api_total_count = len(s.templates)
        return s

    @pytest.mark.asyncio
    async def test_load_more_appends_rows(self):
        """load_more must concatenate new template rows, not replace."""
        page1_raw = [{"template_id": "t-p1", "provider_api": "aws", "is_active": True}]
        page2_raw = [{"template_id": "t-p2", "provider_api": "aws", "is_active": True}]

        s = self._make_state(page1_raw=page1_raw)

        with patch("orb.ui.pages.templates.api") as mock_api:
            mock_api.list_templates = AsyncMock(
                return_value={
                    "templates": page2_raw,
                    "next_cursor": "",
                    "total_count": 2,
                }
            )
            await s.load_more()

        assert len(s.templates) == 2
        ids = [t["template_id"] for t in s.templates]
        assert "t-p1" in ids
        assert "t-p2" in ids
        assert s.next_cursor == ""
        assert s.api_total_count == 2

    @pytest.mark.asyncio
    async def test_load_more_no_op_when_cursor_empty(self):
        """load_more must be a no-op when next_cursor is empty."""
        page1_raw = [{"template_id": "t-existing", "provider_api": "aws", "is_active": True}]
        s = self._make_state(page1_raw=page1_raw, next_cursor="")

        with patch("orb.ui.pages.templates.api") as mock_api:
            mock_api.list_templates = AsyncMock(
                return_value={"templates": [], "next_cursor": "", "total_count": 0}
            )
            await s.load_more()

        mock_api.list_templates.assert_not_called()
        assert len(s.templates) == 1

    @pytest.mark.asyncio
    async def test_load_more_updates_total_count(self):
        """load_more must update api_total_count from the API response."""
        page1_raw = [{"template_id": "t-p1", "provider_api": "aws", "is_active": True}]
        s = self._make_state(page1_raw=page1_raw)
        s.api_total_count = 1

        with patch("orb.ui.pages.templates.api") as mock_api:
            mock_api.list_templates = AsyncMock(
                return_value={
                    "templates": [
                        {"template_id": "t-p2", "provider_api": "aws", "is_active": True}
                    ],
                    "next_cursor": "",
                    "total_count": 999,
                }
            )
            await s.load_more()

        assert s.api_total_count == 999

    @pytest.mark.asyncio
    async def test_load_more_sets_error_on_api_failure(self):
        """load_more must set error and clear loading_more on exception."""
        page1_raw = [{"template_id": "t-p1", "provider_api": "aws", "is_active": True}]
        s = self._make_state(page1_raw=page1_raw)

        with patch("orb.ui.pages.templates.api") as mock_api:
            mock_api.list_templates = AsyncMock(side_effect=RuntimeError("server error"))
            await s.load_more()

        assert "Failed to load more templates" in s.error
        assert s.loading_more is False
