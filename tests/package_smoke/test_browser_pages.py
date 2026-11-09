"""Each UI page loads in a real browser against the installed package.

Runs only in the nightly browser job (``ORB_SMOKE_BROWSER=1``), where Playwright
is installed at a pinned version outside the project's dependencies.
"""

from __future__ import annotations

import os

import pytest

from tests.package_smoke.conftest import Server

pytestmark = pytest.mark.skipif(
    os.environ.get("ORB_SMOKE_BROWSER") != "1", reason="browser smoke runs in the nightly job only"
)

ROUTES = ["/", "/machines", "/requests", "/templates", "/config"]


def test_pages_render_in_a_browser(server: Server):
    sync_api = pytest.importorskip("playwright.sync_api")
    errors: list[str] = []
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            for route in ROUTES:
                response = page.goto(f"{server.base}{route}", wait_until="networkidle")
                assert response is not None and response.status == 200, route
                assert page.title().startswith("ORB"), (route, page.title())
        finally:
            browser.close()
    assert not errors, errors
