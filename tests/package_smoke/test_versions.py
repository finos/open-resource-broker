"""Three independent sources must name the same release.

The UI bundle's build manifest (written when the bundle was built), the wheel's
own METADATA (read from the archive, nothing imported) and the version the
running backend reports. A bundle left over from an older build, or a backend
that does not match its wheel, makes them disagree.
"""

from __future__ import annotations

import json
import urllib.request
import zipfile

from tests.package_smoke.conftest import Server, Smoke
from tests.package_smoke.test_cli_flow import _wheel_version

FALLBACK = "0.1.0.dev0"


def test_bundle_wheel_and_backend_report_one_version(smoke: Smoke, server: Server):
    with zipfile.ZipFile(smoke.wheel) as zf:
        bundle = json.loads(zf.read("orb/ui/_static/orb-build.json"))["version"]
    wheel = _wheel_version(smoke.wheel)
    with urllib.request.urlopen(f"{server.api}/info", timeout=30) as response:  # noqa: S310
        backend = json.loads(response.read())["version"]
    assert FALLBACK not in (bundle, wheel, backend), (bundle, wheel, backend)
    assert bundle == wheel == backend, f"bundle {bundle}, wheel {wheel}, backend {backend}"
