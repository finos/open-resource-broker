"""uv_lock_version.py resolves exactly one version of a package from uv.lock."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "dev-tools" / "ci" / "uv_lock_version.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_extractor_reads_reflex_from_the_real_lock():
    result = _run("reflex", str(ROOT / "uv.lock"))
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"\d+\.\d+\.\d+\S*", result.stdout.strip())


def test_extractor_fails_for_an_absent_package():
    result = _run("no-such-package-xyz", str(ROOT / "uv.lock"))
    assert result.returncode == 1


def test_extractor_fails_for_two_distinct_versions(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "reflex"\nversion = "0.9.13"\n\n'
        '[[package]]\nname = "reflex"\nversion = "0.9.14"\n',
        encoding="utf-8",
    )
    assert _run("reflex", str(lock)).returncode == 1


def test_extractor_matches_names_exactly(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "reflex-hosting-cli"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    assert _run("reflex", str(lock)).returncode == 1
