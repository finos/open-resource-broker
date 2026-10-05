"""Regression test for the TAG_INSTANCES enum consolidation.

Covers open-resource-broker-2706.9: OperationType (domain) and
ProviderOperationType (providers.base.strategy) used to be two
independently declared `str, Enum` classes with identical members, which
could drift apart. ProviderOperationType is now a re-export of the single
canonical domain OperationType rather than a duplicate declaration.

Placed under tests/unit/infrastructure/scheduler/ because the SLURM
resume-request tagging path (open-resource-broker-2706.8) is one of the
call sites that depends on these two names being the same enum.
"""

from orb.domain.base.operations import OperationType
from orb.providers.base.strategy.provider_strategy import ProviderOperationType


def test_provider_operation_type_is_the_domain_operation_type():
    """ProviderOperationType must be an alias, not a second declaration."""
    assert ProviderOperationType is OperationType


def test_tag_instances_member_is_shared():
    assert ProviderOperationType.TAG_INSTANCES is OperationType.TAG_INSTANCES
    assert ProviderOperationType.TAG_INSTANCES.value == "tag_instances"


def test_all_members_are_identical_single_enum():
    """Every member of the (formerly duplicated) enum is the same object
    whichever import path is used — there is exactly one enum now."""
    for member in list(OperationType):
        assert getattr(ProviderOperationType, member.name) is member
