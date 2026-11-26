"""Tests for the file-size baseline ratchet in dev-tools/quality/dev_tools_runner.py."""

import importlib.util
import json
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "dev-tools" / "quality" / "dev_tools_runner.py"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("dev_tools_runner_under_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    return tmp_path


def _write(tree, name, lines):
    (tree / "src" / "pkg" / name).write_text("x = 1\n" * lines, encoding="utf-8")


def _baseline(tree, files):
    path = tree / "baseline.json"
    path.write_text(json.dumps({"files": files}), encoding="utf-8")
    return path


def _run(runner, tree, *args):
    try:
        runner.check_file_sizes(baseline_path=tree / "baseline.json", **(args[0] if args else {}))
    except SystemExit as exc:
        return exc.code
    return 0


KEY = "src/pkg/big.py"


def test_new_oversized_file_fails(runner, tree):
    _baseline(tree, {})
    _write(tree, "big.py", 601)
    assert _run(runner, tree) == 1


def test_baselined_file_that_grew_fails(runner, tree):
    _baseline(tree, {KEY: 650})
    _write(tree, "big.py", 651)
    assert _run(runner, tree) == 1


def test_baselined_file_that_shrank_fails_with_lower_the_baseline(runner, tree, caplog):
    _baseline(tree, {KEY: 650})
    _write(tree, "big.py", 649)
    assert _run(runner, tree) == 1
    assert "lower the baseline" in caplog.text
    assert "make file-sizes-baseline" in caplog.text


def test_stale_entry_fails(runner, tree, caplog):
    _baseline(tree, {KEY: 650})
    _write(tree, "big.py", 600)
    assert _run(runner, tree) == 1
    assert "remove the stale entry" in caplog.text


def test_deleted_file_entry_fails(runner, tree):
    _baseline(tree, {KEY: 650})
    assert _run(runner, tree) == 1


def test_unchanged_baselined_tree_passes(runner, tree):
    """Regression guard: already passes before the baseline logic."""
    _baseline(tree, {KEY: 650})
    _write(tree, "big.py", 650)
    assert _run(runner, tree) == 0


def test_warn_only_still_exits_zero(runner, tree):
    """Regression guard: --warn-only kept its meaning."""
    _baseline(tree, {})
    _write(tree, "big.py", 700)
    assert _run(runner, tree, {"warn_only": True}) == 0


def test_missing_or_malformed_baseline_fails(runner, tree):
    _write(tree, "big.py", 10)
    assert _run(runner, tree) == 1
    (tree / "baseline.json").write_text('{"files": {"a": "x"}}', encoding="utf-8")
    assert _run(runner, tree) == 1


def test_report_lists_and_exits_zero(runner, tree):
    _write(tree, "big.py", 700)
    assert _run(runner, tree, {"report": True}) == 0


def test_write_baseline_lowers_and_removes_but_never_raises_or_adds(runner, tree):
    _baseline(tree, {KEY: 900, "src/pkg/gone.py": 700, "src/pkg/grown.py": 650})
    _write(tree, "big.py", 700)
    _write(tree, "grown.py", 700)
    _write(tree, "new.py", 650)
    assert _run(runner, tree, {"write_baseline": True}) == 0
    files = json.loads((tree / "baseline.json").read_text())["files"]
    assert files == {KEY: 700, "src/pkg/grown.py": 650}


def test_write_baseline_allow_increase_records_growth_and_new_files(runner, tree):
    _baseline(tree, {"src/pkg/grown.py": 650})
    _write(tree, "grown.py", 700)
    _write(tree, "new.py", 650)
    assert _run(runner, tree, {"write_baseline": True, "allow_increase": True}) == 0
    files = json.loads((tree / "baseline.json").read_text())["files"]
    assert files == {"src/pkg/grown.py": 700, "src/pkg/new.py": 650}
    assert _run(runner, tree) == 0


def test_baseline_keys_are_posix_and_sorted(runner, tree):
    _write(tree, "b.py", 650)
    _write(tree, "a.py", 650)
    _run(runner, tree, {"write_baseline": True, "allow_increase": True})
    keys = list(json.loads((tree / "baseline.json").read_text())["files"])
    assert keys == ["src/pkg/a.py", "src/pkg/b.py"]
    assert all("\\" not in k for k in keys)


def test_committed_baseline_matches_the_tree(runner, monkeypatch):
    """The committed baseline must agree with the real sources."""
    monkeypatch.chdir(RUNNER.parents[2])
    try:
        runner.check_file_sizes()
    except SystemExit as exc:
        pytest.fail(f"committed baseline is out of date (exit {exc.code})")
