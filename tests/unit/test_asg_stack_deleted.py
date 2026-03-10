"""Verify the dead ASG query stack has been fully removed."""

import pytest


def test_asg_query_port_not_importable():
    """ASGQueryPort should not exist in domain ports."""
    with pytest.raises(ImportError):
        from orb.domain.base.ports.asg_query_port import ASGQueryPort  # noqa: F401


def test_asg_query_adapter_not_importable():
    """ASGQueryAdapter should not exist in infrastructure adapters."""
    with pytest.raises(ImportError):
        from orb.infrastructure.adapters.asg_query_adapter import ASGQueryAdapter  # noqa: F401


def test_asg_metadata_service_not_importable():
    """ASGMetadataService should not exist in application services."""
    with pytest.raises(ImportError):
        from orb.application.services.asg_metadata_service import ASGMetadataService  # noqa: F401


def test_sync_request_handler_has_no_asg_query_port_param():
    """SyncRequestHandler should not require asg_query_port."""
    import inspect

    from orb.application.commands.request_sync_handlers import SyncRequestHandler

    sig = inspect.signature(SyncRequestHandler.__init__)
    assert "asg_query_port" not in sig.parameters, (
        "SyncRequestHandler still has asg_query_port parameter"
    )
