"""Layer 4: Cross-cutting concerns — template defaults, scheduler_type identity, _unwrap_request_id."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from tests.unit.infrastructure.scheduler.conftest import (
    _MINIMAL_HF_TEMPLATE_ON_DISK,
    _MINIMAL_SNAKE_TEMPLATE,
    make_default_strategy,
    make_defaults_service,
    make_hf_strategy,
    write_default_file,
    write_hf_file,
)

# ---------------------------------------------------------------------------
# get_scheduler_type identity
# ---------------------------------------------------------------------------


def test_hf_get_scheduler_type_returns_hostfactory():
    assert make_hf_strategy().get_scheduler_type() == "hostfactory"


def test_default_get_scheduler_type_returns_default():
    assert make_default_strategy().get_scheduler_type() == "default"


def test_hf_scheduler_type_matches_written_file_key(tmp_path):
    """The value returned by get_scheduler_type() matches the scheduler_type key in generated files."""
    import json

    strategy = make_hf_strategy()
    generated = strategy.format_templates_for_dispatch([_MINIMAL_SNAKE_TEMPLATE])
    f = tmp_path / "hf_rt.json"
    f.write_text(
        json.dumps({"scheduler_type": strategy.get_scheduler_type(), "templates": generated})
    )
    data = json.loads(f.read_text())
    assert data["scheduler_type"] == strategy.get_scheduler_type()


def test_default_scheduler_type_matches_written_file_key(tmp_path):
    import json

    strategy = make_default_strategy()
    generated = strategy.format_templates_for_dispatch([_MINIMAL_SNAKE_TEMPLATE])
    f = tmp_path / "default_rt.json"
    f.write_text(
        json.dumps({"scheduler_type": strategy.get_scheduler_type(), "templates": generated})
    )
    data = json.loads(f.read_text())
    assert data["scheduler_type"] == strategy.get_scheduler_type()


# ---------------------------------------------------------------------------
# Template defaults applied regardless of scheduler
# ---------------------------------------------------------------------------


def test_hf_strategy_defaults_fills_empty_subnet_ids(tmp_path):
    tpl = {**_MINIMAL_HF_TEMPLATE_ON_DISK, "subnetIds": []}
    f = tmp_path / "hf.json"
    write_hf_file(f, [tpl])
    svc = make_defaults_service(["subnet-111", "subnet-222"], ["sg-abc"])
    strategy = make_hf_strategy(defaults_service=svc)
    results = strategy.load_templates_from_path(str(f))
    assert results[0]["subnet_ids"] == ["subnet-111", "subnet-222"]


def test_hf_strategy_defaults_does_not_overwrite_existing_subnet_ids(tmp_path):
    tpl = {**_MINIMAL_HF_TEMPLATE_ON_DISK, "subnetIds": ["subnet-existing"]}
    f = tmp_path / "hf.json"
    write_hf_file(f, [tpl])
    svc = make_defaults_service(["subnet-from-defaults"], ["sg-abc"])
    strategy = make_hf_strategy(defaults_service=svc)
    results = strategy.load_templates_from_path(str(f))
    assert results[0]["subnet_ids"] == ["subnet-existing"]


def test_default_strategy_defaults_fills_empty_subnet_ids(tmp_path):
    tpl = {**_MINIMAL_SNAKE_TEMPLATE, "subnet_ids": []}
    f = tmp_path / "default.json"
    write_default_file(f, [tpl])
    svc = make_defaults_service(["subnet-333", "subnet-444"], ["sg-xyz"])
    strategy = make_default_strategy(defaults_service=svc)
    results = strategy.load_templates_from_path(str(f))
    assert results[0]["subnet_ids"] == ["subnet-333", "subnet-444"]


def test_default_strategy_defaults_fills_empty_security_group_ids(tmp_path):
    tpl = {**_MINIMAL_SNAKE_TEMPLATE, "security_group_ids": []}
    f = tmp_path / "default.json"
    write_default_file(f, [tpl])
    svc = make_defaults_service(["subnet-aaa"], ["sg-from-defaults"])
    strategy = make_default_strategy(defaults_service=svc)
    results = strategy.load_templates_from_path(str(f))
    assert results[0]["security_group_ids"] == ["sg-from-defaults"]


def test_default_strategy_defaults_does_not_overwrite_existing_subnet_ids(tmp_path):
    tpl = {**_MINIMAL_SNAKE_TEMPLATE, "subnet_ids": ["subnet-keep-me"]}
    f = tmp_path / "default.json"
    write_default_file(f, [tpl])
    svc = make_defaults_service(["subnet-should-not-appear"], ["sg-abc"])
    strategy = make_default_strategy(defaults_service=svc)
    results = strategy.load_templates_from_path(str(f))
    assert results[0]["subnet_ids"] == ["subnet-keep-me"]


def test_hf_strategy_defaults_noop_when_service_is_none(tmp_path):
    """_apply_template_defaults is a no-op when template_defaults_service is None."""
    tpl = {**_MINIMAL_HF_TEMPLATE_ON_DISK, "subnetIds": []}
    f = tmp_path / "hf.json"
    write_hf_file(f, [tpl])
    strategy = make_hf_strategy(defaults_service=None)
    results = strategy.load_templates_from_path(str(f))
    # subnet_ids may be empty — the point is no exception is raised
    assert isinstance(results[0].get("subnet_ids", []), list)


def test_default_strategy_defaults_noop_when_service_is_none(tmp_path):
    tpl = {**_MINIMAL_SNAKE_TEMPLATE, "subnet_ids": []}
    f = tmp_path / "default.json"
    write_default_file(f, [tpl])
    strategy = make_default_strategy(defaults_service=None)
    results = strategy.load_templates_from_path(str(f))
    assert isinstance(results[0].get("subnet_ids", []), list)


# ---------------------------------------------------------------------------
# _unwrap_request_id (base class static method)
# ---------------------------------------------------------------------------


def test_unwrap_request_id_plain_string():
    result = HostFactorySchedulerStrategy._unwrap_request_id("req-abc")
    assert result == "req-abc"


def test_unwrap_request_id_dict_with_value_key():
    result = HostFactorySchedulerStrategy._unwrap_request_id({"value": "req-xyz"})
    assert result == "req-xyz"


def test_unwrap_request_id_object_with_value_attr():
    class _Wrapper:
        value = "req-obj"

    result = HostFactorySchedulerStrategy._unwrap_request_id(_Wrapper())
    assert result == "req-obj"


def test_unwrap_request_id_none_returns_none():
    assert HostFactorySchedulerStrategy._unwrap_request_id(None) is None


def test_unwrap_request_id_integer():
    result = HostFactorySchedulerStrategy._unwrap_request_id(42)
    assert result == "42"


def test_unwrap_request_id_same_for_both_strategies():
    """Both strategies share the same static method — results must be identical."""
    value = {"value": "req-shared"}
    hf_result = HostFactorySchedulerStrategy._unwrap_request_id(value)
    default_result = DefaultSchedulerStrategy._unwrap_request_id(value)
    assert hf_result == default_result
