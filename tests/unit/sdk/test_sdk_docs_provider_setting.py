"""SDK docs and examples must not show the deprecated ``provider`` setting.

Copying an example that sets ``provider`` emits a ``DeprecationWarning`` and
has no effect, so the SDK-facing docs should show ``provider_type`` (or
nothing) instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Files that show SDK construction or SDK config dictionaries.
_SDK_DOCS = [
    *sorted((_REPO_ROOT / "docs" / "root" / "sdk").glob("*.md")),
    _REPO_ROOT / "docs" / "root" / "examples" / "sdk_usage.py",
    _REPO_ROOT / "docs" / "root" / "workflows" / "end-to-end.md",
    _REPO_ROOT / "README.md",
]

# A string-valued "provider" key (the deprecated SDK setting; the dict-valued
# application config section is fine) or a provider= keyword on SDK entry points.
_DEPRECATED = re.compile(
    r"""["']provider["']\s*:\s*["']"""
    r"""|\b(?:orb|ORBClient|OpenResourceBroker|SDKConfig)\(\s*provider\s*="""
)


@pytest.mark.unit
def test_sdk_doc_files_exist():
    missing = [str(p) for p in _SDK_DOCS if not p.is_file()]
    assert not missing, f"SDK doc files moved or removed: {missing}"


@pytest.mark.unit
@pytest.mark.parametrize("doc", _SDK_DOCS, ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_sdk_docs_do_not_use_the_deprecated_provider_setting(doc: Path):
    hits = [
        f"{doc.relative_to(_REPO_ROOT)}:{n}: {line.strip()}"
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1)
        if _DEPRECATED.search(line)
    ]
    assert not hits, "Deprecated SDK 'provider' setting in docs:\n" + "\n".join(hits)
