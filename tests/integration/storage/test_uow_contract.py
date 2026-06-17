"""UnitOfWork contract tests across all backends.

Each UoW must:
- Expose machines / requests / templates repositories.
- begin/commit/rollback transactions without raising.
"""

import pytest


@pytest.mark.integration
class TestUnitOfWorkRepositories:
    def test_repositories_exposed(self, unit_of_work):
        assert unit_of_work.machines is not None
        assert unit_of_work.requests is not None
        assert unit_of_work.templates is not None


@pytest.mark.integration
class TestUnitOfWorkTransactions:
    def test_begin_then_commit(self, unit_of_work):
        unit_of_work.begin()
        assert unit_of_work.in_transaction is True
        unit_of_work.commit()
        assert unit_of_work.in_transaction is False

    def test_begin_then_rollback(self, unit_of_work):
        unit_of_work.begin()
        unit_of_work.rollback()
        assert unit_of_work.in_transaction is False

    def test_context_manager_commits_on_success(self, unit_of_work):
        with unit_of_work:
            assert unit_of_work.in_transaction is True
        assert unit_of_work.in_transaction is False

    def test_context_manager_rolls_back_on_exception(self, unit_of_work):
        with pytest.raises(RuntimeError):
            with unit_of_work:
                raise RuntimeError("boom")
        assert unit_of_work.in_transaction is False
