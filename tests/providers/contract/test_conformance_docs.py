"""The provider conformance requirements in the developer guide match the suites.

A new test file under ``tests/providers/contract`` must be classified here, so
it is either documented as a conformance suite or recorded as not one.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "tests" / "providers" / "contract"
GUIDE = ROOT / "docs" / "root" / "developer_guide" / "adding_a_provider.md"

CONFORMANCE_SUITES = (
    "tests/providers/contract/test_provider_conformance.py",
    "tests/providers/contract/test_capability_router_parity.py",
    "tests/providers/contract/test_config_init_parity.py",
    "tests/providers/lifecycle/test_meta.py",
)

NOT_A_SUITE = {
    "test_conformance_docs.py": "checks this guide, not a provider",
    "test_fresh_registry.py": "checks the registry fixture, not a provider",
    "test_oci_template_validation.py": "OCI template adapter behaviour, not a cross-provider rule",
}


def _guide_section() -> str:
    text = GUIDE.read_text()
    start = text.index("## Conformance requirements")
    return text[start : text.index("\n## ", start + 1)]


def test_every_suite_exists_and_is_documented() -> None:
    section = _guide_section()
    for suite in CONFORMANCE_SUITES:
        assert (ROOT / suite).is_file(), suite
        assert suite in section, f"{suite} is not named in the conformance section"


def test_every_contract_test_file_is_classified_once() -> None:
    suites = {Path(s).name for s in CONFORMANCE_SUITES if s.startswith("tests/providers/contract/")}
    files = {p.name for p in CONTRACT.glob("test_*.py")}
    assert not suites & set(NOT_A_SUITE)
    assert files == suites | set(NOT_A_SUITE), (
        "classify new contract test files as a conformance suite or in NOT_A_SUITE: "
        f"{sorted(files ^ (suites | set(NOT_A_SUITE)))}"
    )


def test_guide_names_no_missing_test_file() -> None:
    import re

    for path in re.findall(r"tests/providers/[\w/]+\.py", _guide_section()):
        assert (ROOT / path).is_file(), path
