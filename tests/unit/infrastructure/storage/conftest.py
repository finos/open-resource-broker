"""Storage unit tests run from a scratch working directory.

A relative SQLite path or a stray ``database.db`` in the repository root must
never influence (or be created by) a test.
"""

import pytest


@pytest.fixture(autouse=True)
def _storage_scratch_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield
