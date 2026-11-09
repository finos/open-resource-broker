"""The release wheel itself: what it carries and how it is installed."""

from __future__ import annotations

import fnmatch
import json
import subprocess
import zipfile

import pytest

from tests.package_smoke.conftest import REPO_ROOT, REQUIRED, Smoke

STATIC = "orb/ui/_static"


def _names(wheel) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        return zf.namelist()


def test_wheel_carries_the_ui_bundle(smoke_wheel):
    names = _names(smoke_wheel)
    assert f"{STATIC}/index.html" in names, "the wheel has no UI index page"
    assert fnmatch.filter(names, f"{STATIC}/assets/*.js"), "the wheel has no UI scripts"
    assert f"{STATIC}/orb-build.json" in names, "the wheel has no UI build manifest"


def test_wheel_is_built_from_this_checkout(smoke_wheel):
    with zipfile.ZipFile(smoke_wheel) as zf:
        manifest = json.loads(zf.read(f"{STATIC}/orb-build.json"))
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        if REQUIRED:
            pytest.fail("the release smoke needs a git checkout to compare the wheel's commit")
        pytest.skip("not a git checkout")
        return
    assert manifest["git_sha"] == head, "the wheel was built from a different commit"


def test_orb_is_imported_from_the_installed_package(smoke: Smoke):
    import json as _json

    out = smoke.run_python(str(smoke.helpers / "introspect.py"), "version")
    info = _json.loads(out.stdout)
    site = str(smoke.venv.site_packages())
    assert info["file"].startswith(site), f"orb imported from {info['file']}, not from {site}"
    assert str(REPO_ROOT / "src") not in info["file"]
