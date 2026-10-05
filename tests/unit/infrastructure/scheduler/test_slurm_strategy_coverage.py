"""Coverage-focused unit tests for SlurmSchedulerStrategy.

Covers open-resource-broker-2706.4's coverage goal for the methods not
already exercised by the contract, template-resolution, template-generation,
or resume/suspend handler test modules: client selection, health checks,
scheduler metadata getters, template load/parse round-tripping, and request
parsing.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy


def _make_config_manager(**scheduler_overrides: Any) -> Any:
    scheduler_ns = SimpleNamespace(config_dir=None, work_dir=None, log_dir=None, log_level=None)
    for key, value in scheduler_overrides.items():
        setattr(scheduler_ns, key, value)
    return SimpleNamespace(
        app_config=SimpleNamespace(scheduler=scheduler_ns),
        get_logging_config=lambda: {},
        get_configuration_value=lambda key, default=None: default,
    )


@pytest.fixture
def strategy() -> SlurmSchedulerStrategy:
    return SlurmSchedulerStrategy()


# ---------------------------------------------------------------------------
# _get_slurm_client — REST vs CLI selection
# ---------------------------------------------------------------------------


def test_get_slurm_client_uses_cli_adapter_by_default(strategy, monkeypatch):
    monkeypatch.delenv("SLURM_ORB_RESTD_URL", raising=False)

    from orb.infrastructure.scheduler.slurm.cli_adapter import SlurmCliAdapter

    client = strategy._get_slurm_client()

    assert isinstance(client, SlurmCliAdapter)


def test_get_slurm_client_uses_rest_client_when_url_configured(strategy, monkeypatch):
    monkeypatch.setenv("SLURM_ORB_RESTD_URL", "https://slurmrestd.example.com")
    monkeypatch.setenv("SLURM_ORB_JWT_TOKEN", "tok-abc")

    from orb.infrastructure.scheduler.slurm.rest_client import SlurmRestClient

    client = strategy._get_slurm_client()

    assert isinstance(client, SlurmRestClient)
    assert client._token == "tok-abc"


def test_get_slurm_client_is_cached(strategy, monkeypatch):
    monkeypatch.delenv("SLURM_ORB_RESTD_URL", raising=False)

    first = strategy._get_slurm_client()
    second = strategy._get_slurm_client()

    assert first is second


def test_get_slurm_client_propagates_injected_logger_to_cli_adapter(monkeypatch):
    monkeypatch.delenv("SLURM_ORB_RESTD_URL", raising=False)
    fake_logger = object()
    strategy_with_logger = SlurmSchedulerStrategy(logger=fake_logger)

    client = strategy_with_logger._get_slurm_client()

    assert client._logger is fake_logger


def test_get_slurm_client_propagates_injected_logger_to_rest_client(monkeypatch):
    monkeypatch.setenv("SLURM_ORB_RESTD_URL", "https://slurmrestd.example.com")
    fake_logger = object()
    strategy_with_logger = SlurmSchedulerStrategy(logger=fake_logger)

    client = strategy_with_logger._get_slurm_client()

    assert client._logger is fake_logger


# ---------------------------------------------------------------------------
# expand_node_range — exposed via SchedulerPort instead of importing
# SlurmNodeMapper directly from infrastructure (open-resource-broker-2706.7)
# ---------------------------------------------------------------------------


def test_expand_node_range_bracket_notation(strategy):
    assert strategy.expand_node_range("compute-[001-003]") == [
        "compute-001",
        "compute-002",
        "compute-003",
    ]


def test_expand_node_range_space_separated_list(strategy):
    assert strategy.expand_node_range("compute-001 compute-002") == [
        "compute-001",
        "compute-002",
    ]


def test_expand_node_range_delegates_to_node_mapper(strategy, monkeypatch):
    calls = []
    monkeypatch.setattr(
        strategy.node_mapper, "expand_node_range", lambda spec: calls.append(spec) or ["x"]
    )

    result = strategy.expand_node_range("compute-[001-003]")

    assert calls == ["compute-[001-003]"]
    assert result == ["x"]


def test_scheduler_port_default_expand_node_range_splits_whitespace():
    """Non-SLURM schedulers get the generic whitespace-split default for free."""
    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    default_strategy = DefaultSchedulerStrategy()

    assert default_strategy.expand_node_range("node-1 node-2") == ["node-1", "node-2"]


# ---------------------------------------------------------------------------
# check_slurm_health
# ---------------------------------------------------------------------------


class _HealthyClient:
    def is_available(self) -> bool:
        return True

    def get_nodes(self) -> dict:
        return {"nodes": [{"state": "IDLE"}, {"state": "IDLE"}, {"state": "ALLOCATED"}]}

    def get_partitions(self) -> dict:
        return {"partitions": [{"name": "compute"}]}


def test_check_slurm_health_pass(strategy):
    strategy._slurm_client = _HealthyClient()

    result = strategy.check_slurm_health()

    assert result["status"] == "pass"
    assert result["details"]["total_nodes"] == 3
    assert result["details"]["node_states"] == {"IDLE": 2, "ALLOCATED": 1}


def test_check_slurm_health_fail_when_unreachable(strategy):
    class _UnreachableClient:
        def is_available(self) -> bool:
            return False

    strategy._slurm_client = _UnreachableClient()

    result = strategy.check_slurm_health()

    assert result["status"] == "fail"
    assert result["message"] == "SLURM cluster not reachable"


def test_check_slurm_health_fail_on_exception(strategy):
    class _ExplodingClient:
        def is_available(self) -> bool:
            raise RuntimeError("boom")

    strategy._slurm_client = _ExplodingClient()

    result = strategy.check_slurm_health()

    assert result["status"] == "fail"
    assert "boom" in result["message"]


# ---------------------------------------------------------------------------
# Scheduler metadata getters
# ---------------------------------------------------------------------------


def test_get_scheduler_type(strategy):
    assert strategy.get_scheduler_type() == "slurm"


def test_get_scripts_directory_points_at_scripts_folder(strategy):
    scripts_dir = strategy.get_scripts_directory()
    assert scripts_dir is not None
    assert scripts_dir.name == "scripts"
    assert (scripts_dir / "resumeProgram.sh").exists()


def test_should_log_to_console(strategy):
    assert strategy.should_log_to_console() is True


def test_get_config_file_path(strategy):
    strategy._config_manager = _make_config_manager(config_dir="/tmp/slurm-cfg")

    assert strategy.get_config_file_path() == "/tmp/slurm-cfg/slurm_config.json"


@pytest.mark.parametrize(
    ("suffix", "env_var", "value"),
    [
        ("CONFIG_DIR", "SLURM_ORB_CONFIG_DIR", "/etc/orb/slurm"),
        ("WORK_DIR", "SLURM_ORB_WORK_DIR", "/var/orb/slurm"),
        ("LOG_DIR", "SLURM_ORB_LOG_DIR", "/var/log/orb"),
        ("LOG_LEVEL", "SLURM_ORB_LOG_LEVEL", "DEBUG"),
    ],
)
def test_get_scheduler_env_var_known_suffixes(strategy, monkeypatch, suffix, env_var, value):
    monkeypatch.setenv(env_var, value)
    assert strategy._get_scheduler_env_var(suffix) == value


def test_get_scheduler_env_var_unknown_suffix_returns_none(strategy):
    assert strategy._get_scheduler_env_var("NOT_A_REAL_SUFFIX") is None


def test_get_directory_scripts(strategy):
    assert strategy.get_directory("scripts") == str(strategy.get_scripts_directory())


def test_get_directory_config_variants(strategy, monkeypatch):
    monkeypatch.setenv("SLURM_ORB_CONFIG_DIR", "/etc/orb/slurm")
    strategy._config_manager = _make_config_manager()

    for file_type in ("config", "template", "legacy"):
        assert strategy.get_directory(file_type) == "/etc/orb/slurm"


def test_get_directory_logs(strategy, monkeypatch):
    monkeypatch.setenv("SLURM_ORB_LOG_DIR", "/var/log/orb")
    strategy._config_manager = _make_config_manager()

    for file_type in ("log", "logs"):
        assert strategy.get_directory(file_type) == "/var/log/orb"


def test_get_directory_health_under_working_directory(strategy, monkeypatch):
    monkeypatch.setenv("SLURM_ORB_WORK_DIR", "/var/orb/slurm")
    strategy._config_manager = _make_config_manager()

    assert strategy.get_directory("health") == "/var/orb/slurm/health"


def test_get_directory_unknown_type_falls_back_to_working_directory(strategy, monkeypatch):
    monkeypatch.setenv("SLURM_ORB_WORK_DIR", "/var/orb/slurm")
    strategy._config_manager = _make_config_manager()

    assert strategy.get_directory("something-else") == "/var/orb/slurm"


# ---------------------------------------------------------------------------
# load_templates_from_path
# ---------------------------------------------------------------------------


def test_load_templates_from_path_missing_file_returns_empty(strategy, tmp_path):
    missing = tmp_path / "does-not-exist.json"

    assert strategy.load_templates_from_path(str(missing)) == []


def test_load_templates_from_path_happy_path(strategy, tmp_path):
    path = tmp_path / "slurm_templates.json"
    path.write_text(
        json.dumps(
            {
                "templates": [
                    {"partition_name": "gpu", "max_instances": 4},
                ]
            }
        )
    )

    templates = strategy.load_templates_from_path(str(path))

    assert len(templates) == 1
    assert templates[0]["template_id"] == "gpu"
    assert templates[0]["templateId"] == "gpu"


def test_load_templates_from_path_malformed_json_returns_empty(strategy, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json")

    assert strategy.load_templates_from_path(str(path)) == []


def test_load_templates_from_path_delegates_to_other_scheduler(strategy, tmp_path, monkeypatch):
    path = tmp_path / "hf_templates.json"
    path.write_text(json.dumps({"scheduler_type": "hostfactory", "templates": []}))

    monkeypatch.setattr(
        strategy, "_delegate_load_to_strategy", lambda *_a, **_k: [{"template_id": "delegated"}]
    )

    templates = strategy.load_templates_from_path(str(path))

    assert templates == [{"template_id": "delegated"}]


def test_load_templates_from_path_delegation_fails_falls_back_to_best_effort(
    strategy, tmp_path, monkeypatch
):
    path = tmp_path / "hf_templates.json"
    path.write_text(
        json.dumps({"scheduler_type": "hostfactory", "templates": [{"partition_name": "gpu"}]})
    )

    monkeypatch.setattr(strategy, "_delegate_load_to_strategy", lambda *_a, **_k: None)

    templates = strategy.load_templates_from_path(str(path))

    assert len(templates) == 1
    assert templates[0]["template_id"] == "gpu"


# ---------------------------------------------------------------------------
# parse_template_config
# ---------------------------------------------------------------------------


def test_parse_template_config_maps_partition_name_to_template_id(strategy):
    dto = strategy.parse_template_config({"partition_name": "gpu", "max_instances": 4})

    assert dto.template_id == "gpu"
    assert dto.max_instances == 4


def test_parse_template_config_applies_defaults_for_missing_fields(strategy):
    dto = strategy.parse_template_config({"partition_name": "gpu"})

    assert dto.max_instances == 1
    assert dto.price_type == "ondemand"
    assert dto.is_active is True


def test_parse_template_config_unknown_partition_falls_back_to_unknown(strategy):
    dto = strategy.parse_template_config({})

    assert dto.template_id == "unknown"


# ---------------------------------------------------------------------------
# parse_request_data
# ---------------------------------------------------------------------------


def test_parse_request_data_status_query_list():
    strategy = SlurmSchedulerStrategy()
    result = strategy.parse_request_data(
        {"requests": [{"request_id": "req-1"}, {"request_id": "req-2"}]}
    )

    assert result == [{"request_id": "req-1"}, {"request_id": "req-2"}]


def test_parse_request_data_nested_template_format(strategy):
    result = strategy.parse_request_data(
        {
            "template": {
                "template_id": "gpu",
                "node_names": ["gpu-001", "gpu-002"],
                "machine_count": 2,
            }
        }
    )

    assert result["template_id"] == "gpu"
    assert result["requested_count"] == 2
    assert result["node_names"] == ["gpu-001", "gpu-002"]


def test_parse_request_data_flat_format_uses_partition_name_fallback(strategy):
    result = strategy.parse_request_data({"partition_name": "compute", "count": 3})

    assert result["template_id"] == "compute"
    assert result["requested_count"] == 3
    assert result["request_type"] == "provision"


def test_parse_request_data_filters_invalid_node_names(strategy):
    result = strategy.parse_request_data(
        {"template_id": "compute", "node_names": ["compute-001", "bad; rm -rf /", 42]}
    )

    assert result["node_names"] == ["compute-001"]


def test_parse_request_data_requested_count_floor_is_one(strategy):
    result = strategy.parse_request_data({"template_id": "compute", "requested_count": 0})

    assert result["requested_count"] == 1


def test_parse_request_data_default_metadata_is_empty_dict(strategy):
    result = strategy.parse_request_data({"template_id": "compute"})

    assert result["metadata"] == {}


# ---------------------------------------------------------------------------
# format_templates_for_dispatch / format_machine_details_response
# ---------------------------------------------------------------------------


def test_format_templates_for_dispatch_round_trips_partition_name(strategy):
    dispatched = strategy.format_templates_for_dispatch(
        [{"template_id": "gpu", "max_instances": 4, "is_active": True}]
    )

    assert dispatched[0]["partition_name"] == "gpu"


def test_format_machine_details_response_prefers_template_id_as_partition(strategy):
    result = strategy.format_machine_details_response(
        {"machine_id": "i-abc", "name": "compute-001", "template_id": "compute"}
    )

    assert result["partition"] == "compute"
    assert result["node_name"] == "compute-001"


# ---------------------------------------------------------------------------
# register_provisioned_nodes
# ---------------------------------------------------------------------------


def test_register_provisioned_nodes_registers_mapping_and_address(strategy, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "orb.infrastructure.scheduler.slurm.node_bootstrap.SlurmNodeBootstrap.register_node_address",
        lambda self, node_name, ip_address, hostname=None: (
            calls.append((node_name, ip_address)) or True
        ),
    )

    strategy.register_provisioned_nodes(
        [{"node_name": "compute-001", "machine_id": "i-abc123", "ip_address": "10.0.0.5"}]
    )

    assert strategy.node_mapper.get_machine_id("compute-001") == "i-abc123"
    assert calls == [("compute-001", "10.0.0.5")]


def test_register_provisioned_nodes_handles_address_registration_failure(strategy, monkeypatch):
    monkeypatch.setattr(
        "orb.infrastructure.scheduler.slurm.node_bootstrap.SlurmNodeBootstrap.register_node_address",
        lambda self, node_name, ip_address, hostname=None: False,
    )

    # Should not raise even though address registration reports failure.
    strategy.register_provisioned_nodes(
        [{"node_name": "compute-002", "machine_id": "i-def456", "ip_address": "10.0.0.6"}]
    )

    assert strategy.node_mapper.get_machine_id("compute-002") == "i-def456"


def test_register_provisioned_nodes_skips_entries_missing_required_fields(strategy):
    # No node_name/machine_id → nothing registered, no exception.
    strategy.register_provisioned_nodes([{"ip_address": "10.0.0.7"}])

    assert strategy.node_mapper.get_all_mappings() == {}
