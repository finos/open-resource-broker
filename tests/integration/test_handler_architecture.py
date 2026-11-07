"""The four AWS handlers share one constructor, method and base-class shape."""

import inspect
from pathlib import Path
from unittest.mock import Mock

import pytest

from orb.domain.request.aggregate import Request
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

HANDLERS = [SpotFleetHandler, EC2FleetHandler, RunInstancesHandler, ASGHandler]
_HANDLER_IDS = [h.__name__ for h in HANDLERS]

# Every handler takes these first, in this order; handler-specific
# collaborators follow and must all be optional.
_SHARED_CONSTRUCTOR_PARAMS = [
    "aws_client",
    "logger",
    "aws_ops",
    "launch_template_manager",
    "request_adapter",
    "machine_adapter",
]


def _make_handler(handler_class, launch_template_manager=None):
    return handler_class(
        aws_client=Mock(),
        logger=Mock(),
        aws_ops=Mock(),
        launch_template_manager=launch_template_manager or Mock(),
    )


@pytest.mark.parametrize("handler_class", HANDLERS, ids=_HANDLER_IDS)
def test_constructor_consistency(handler_class):
    """Shared parameters come first in a fixed order; the rest are optional."""
    params = list(inspect.signature(handler_class.__init__).parameters.values())[1:]
    names = [p.name for p in params]

    assert names[: len(_SHARED_CONSTRUCTOR_PARAMS)] == _SHARED_CONSTRUCTOR_PARAMS
    for param in params[len(_SHARED_CONSTRUCTOR_PARAMS) :]:
        assert param.default is not inspect.Parameter.empty, (
            f"{handler_class.__name__}.__init__ parameter {param.name!r} has no default"
        )


@pytest.mark.parametrize("method_name", ["acquire_hosts", "check_hosts_status", "release_hosts"])
def test_method_signatures(method_name):
    """Each lifecycle method has the same parameters on every handler."""
    signatures = {
        h.__name__: list(inspect.signature(getattr(h, method_name)).parameters) for h in HANDLERS
    }

    assert len({tuple(s) for s in signatures.values()}) == 1, signatures


@pytest.mark.parametrize("handler_class", HANDLERS, ids=_HANDLER_IDS)
def test_acquire_and_status_take_request_and_aws_template(handler_class):
    acquire = inspect.signature(handler_class.acquire_hosts).parameters
    status = inspect.signature(handler_class.check_hosts_status).parameters

    assert [p.annotation for p in acquire.values()][1:] == [Request, AWSTemplate]
    assert [p.annotation for p in status.values()][1:] == [Request]


@pytest.mark.parametrize("handler_class", HANDLERS, ids=_HANDLER_IDS)
def test_base_class_inheritance(handler_class):
    """Handlers inherit AWSHandler directly and with it the shared helpers."""
    assert AWSHandler in handler_class.__bases__
    for method_name in ["_retry_with_backoff", "_convert_client_error", "_validate_prerequisites"]:
        assert hasattr(handler_class, method_name)


@pytest.mark.parametrize("handler_class", HANDLERS, ids=_HANDLER_IDS)
def test_launch_template_integration(handler_class):
    """The injected launch template manager is the one the handler uses."""
    manager = Mock()

    handler = _make_handler(handler_class, launch_template_manager=manager)

    assert handler.launch_template_manager is manager


def test_error_handling_consistency():
    """A constructed handler exposes the base-class error helpers."""
    handler = _make_handler(SpotFleetHandler)

    for method_name in ["_convert_client_error", "_retry_with_backoff"]:
        assert hasattr(AWSHandler, method_name)
        assert callable(getattr(handler, method_name))


def test_performance_metrics():
    """The removed per-handler metrics methods stay removed."""
    for method_name in ["get_metrics", "_record_success_metrics", "_record_failure_metrics"]:
        assert not hasattr(AWSHandler, method_name)


@pytest.mark.parametrize("handler_class", HANDLERS, ids=_HANDLER_IDS)
def test_import_consistency(handler_class):
    """Handler modules import AWSTemplate and AWSHandler from their canonical modules."""
    source = Path(inspect.getfile(handler_class)).read_text()

    assert (
        "from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate" in source
    )
    assert "from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler" in source
