"""The lifecycle fake must filter pod lists by selector for return requests too.

A carve-out that served a return request's list unfiltered once hid a status read
that selected pods by the return request's own id. This guard only stops that
carve-out from coming back; the behaviour itself is covered by the return-status
tests that run the real handlers against a selector-honouring fake.
"""

from __future__ import annotations

from pathlib import Path

LIFECYCLE = Path(__file__).resolve().parents[2] / "lifecycle"


def test_lifecycle_fake_has_no_return_request_carve_out() -> None:
    for name in ("k8s_cloud.py", "driver.py"):
        text = (LIFECYCLE / name).read_text()
        assert "note_return_requests" not in text, name
        assert "_return_requests" not in text, name
