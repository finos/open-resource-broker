"""Unit tests for SLURM template generation from slurm.conf.

Covers open-resource-broker-2706.4: generate_scheduler_templates,
_parse_slurm_conf, and _match_instance_type had no dedicated tests.
"""

from typing import Any

import pytest

from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy

SLURM_CONF_SAMPLE = """\
# Sample slurm.conf
ClusterName=test-cluster

NodeName=compute-[001-010] CPUs=4 RealMemory=16000
NodeName=gpu-[001-004] CPUs=8 RealMemory=32000

PartitionName=compute Nodes=compute-[001-010] Default=YES MaxTime=INFINITE State=UP
PartitionName=gpu Nodes=gpu-[001-004] MaxTime=INFINITE State=UP
"""


@pytest.fixture
def strategy() -> SlurmSchedulerStrategy:
    return SlurmSchedulerStrategy()


@pytest.fixture
def slurm_conf_path(tmp_path) -> str:
    path = tmp_path / "slurm.conf"
    path.write_text(SLURM_CONF_SAMPLE)
    return str(path)


# ---------------------------------------------------------------------------
# _parse_slurm_conf
# ---------------------------------------------------------------------------


def test_parse_slurm_conf_extracts_both_partitions(strategy, slurm_conf_path):
    partitions = strategy._parse_slurm_conf(slurm_conf_path)

    names = {p["name"] for p in partitions}
    assert names == {"compute", "gpu"}


def test_parse_slurm_conf_resolves_cpus_and_memory_from_matching_nodename(
    strategy, slurm_conf_path
):
    partitions = strategy._parse_slurm_conf(slurm_conf_path)

    compute = next(p for p in partitions if p["name"] == "compute")
    gpu = next(p for p in partitions if p["name"] == "gpu")

    assert compute["cpus"] == 4
    assert compute["memory_mb"] == 16000
    assert gpu["cpus"] == 8
    assert gpu["memory_mb"] == 32000


def test_parse_slurm_conf_derives_max_nodes_from_bracket_range(strategy, slurm_conf_path):
    partitions = strategy._parse_slurm_conf(slurm_conf_path)

    compute = next(p for p in partitions if p["name"] == "compute")
    gpu = next(p for p in partitions if p["name"] == "gpu")

    assert compute["max_nodes"] == 10
    assert gpu["max_nodes"] == 4


def test_parse_slurm_conf_skips_comments_and_blank_lines(strategy, tmp_path):
    conf = tmp_path / "slurm.conf"
    conf.write_text("# comment\n\n   \nPartitionName=solo Nodes=solo-001\n")

    partitions = strategy._parse_slurm_conf(str(conf))

    assert len(partitions) == 1
    assert partitions[0]["name"] == "solo"


def test_parse_slurm_conf_defaults_when_node_spec_unresolvable(strategy, tmp_path):
    """Partition referencing an unknown node group falls back to defaults."""
    conf = tmp_path / "slurm.conf"
    conf.write_text("PartitionName=orphan Nodes=unknown-[01-05]\n")

    partitions = strategy._parse_slurm_conf(str(conf))

    assert partitions[0]["cpus"] == 1
    assert partitions[0]["memory_mb"] == 4096
    assert partitions[0]["max_nodes"] == 10


def test_parse_slurm_conf_prefix_fallback_matching(strategy, tmp_path):
    """Partition Nodes= value matches NodeName by prefix when not an exact key."""
    conf = tmp_path / "slurm.conf"
    conf.write_text(
        "NodeName=compute-[001-010] CPUs=2 RealMemory=8000\n"
        "PartitionName=compute Nodes=compute-[001-005]\n"
    )

    partitions = strategy._parse_slurm_conf(str(conf))

    assert partitions[0]["cpus"] == 2
    assert partitions[0]["memory_mb"] == 8000


def test_parse_slurm_conf_empty_file_returns_no_partitions(strategy, tmp_path):
    conf = tmp_path / "slurm.conf"
    conf.write_text("")

    assert strategy._parse_slurm_conf(str(conf)) == []


# ---------------------------------------------------------------------------
# _match_instance_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cpus", "memory_mb", "expected"),
    [
        (1, 512, "t3.nano"),
        (2, 2048, "t3.small"),
        (4, 16384, "t3.xlarge"),
        (8, 16384, "c5.2xlarge"),
    ],
)
def test_match_instance_type_picks_smallest_fit(strategy, cpus, memory_mb, expected):
    assert strategy._match_instance_type(cpus, memory_mb) == expected


def test_match_instance_type_falls_back_when_nothing_fits(strategy):
    """Requirement larger than every entry in the static map uses the default fallback."""
    assert strategy._match_instance_type(cpus=128, memory_mb=1_048_576) == "t3.medium"


def test_match_instance_type_exact_boundary_match(strategy):
    """16 CPU / 65536MB is only satisfied by m5.4xlarge (c5.4xlarge's 32768MB is too small)."""
    assert strategy._match_instance_type(cpus=16, memory_mb=65536) == "m5.4xlarge"


# ---------------------------------------------------------------------------
# generate_scheduler_templates — end to end
# ---------------------------------------------------------------------------


def test_generate_scheduler_templates_auto_maps_instance_types(strategy, slurm_conf_path):
    templates = strategy.generate_scheduler_templates(slurm_conf=slurm_conf_path)

    assert templates is not None
    by_id = {t["template_id"]: t for t in templates}
    assert set(by_id) == {"compute", "gpu"}
    assert by_id["compute"]["max_instances"] == 10
    assert by_id["gpu"]["max_instances"] == 4


def test_generate_scheduler_templates_no_conf_found_returns_none(strategy, monkeypatch):
    monkeypatch.setattr(strategy, "_find_slurm_conf", lambda cli_path=None: None)

    assert strategy.generate_scheduler_templates() is None


def test_generate_scheduler_templates_no_partitions_returns_none(strategy, tmp_path):
    conf = tmp_path / "slurm.conf"
    conf.write_text("# nothing here\n")

    assert strategy.generate_scheduler_templates(slurm_conf=str(conf)) is None


def test_generate_scheduler_templates_honors_user_instance_type_preference(
    strategy, slurm_conf_path
):
    class _FakeConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            if key == "scheduler":
                return {"slurm": {"partitions": {"gpu": {"instance_types": ["p3.2xlarge"]}}}}
            return default

    strategy._config_manager = _FakeConfigManager()

    templates = strategy.generate_scheduler_templates(
        slurm_conf=slurm_conf_path, skip_validation=True
    )

    gpu_template = next(t for t in templates if t["template_id"] == "gpu")
    assert gpu_template["machine_types"] == {"p3.2xlarge": 1}


def test_generate_scheduler_templates_raises_on_undersized_user_instance_type(
    strategy, slurm_conf_path
):
    """An explicitly configured instance type that can't meet partition requirements errors."""

    class _FakeConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            if key == "scheduler":
                # gpu partition needs 8 CPU / 32000MB; t3.nano (1 CPU/512MB) can't satisfy it.
                return {"slurm": {"partitions": {"gpu": {"instance_types": ["t3.nano"]}}}}
            return default

    strategy._config_manager = _FakeConfigManager()

    with pytest.raises(ValueError, match="validation failed"):
        strategy.generate_scheduler_templates(slurm_conf=slurm_conf_path)


def test_generate_scheduler_templates_skip_validation_bypasses_undersized_check(
    strategy, slurm_conf_path
):
    class _FakeConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            if key == "scheduler":
                return {"slurm": {"partitions": {"gpu": {"instance_types": ["t3.nano"]}}}}
            return default

    strategy._config_manager = _FakeConfigManager()

    templates = strategy.generate_scheduler_templates(
        slurm_conf=slurm_conf_path, skip_validation=True
    )

    gpu_template = next(t for t in templates if t["template_id"] == "gpu")
    assert gpu_template["machine_types"] == {"t3.nano": 1}


def test_generate_scheduler_templates_malformed_conf_returns_none(
    strategy, monkeypatch, slurm_conf_path
):
    """Non-ValueError parsing failures are swallowed and reported as None, not raised."""

    def _raise(path):
        raise OSError("disk read error")

    monkeypatch.setattr(strategy, "_parse_slurm_conf", _raise)

    assert strategy.generate_scheduler_templates(slurm_conf=slurm_conf_path) is None


def test_generate_scheduler_templates_user_instance_types_as_weighted_dict(
    strategy, slurm_conf_path
):
    """A dict of {instance_type: weight} is used directly as machine_types."""

    class _FakeConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            if key == "scheduler":
                return {
                    "slurm": {
                        "partitions": {
                            "gpu": {"instance_types": {"p3.2xlarge": 2, "p3.8xlarge": 1}}
                        }
                    }
                }
            return default

    strategy._config_manager = _FakeConfigManager()

    templates = strategy.generate_scheduler_templates(
        slurm_conf=slurm_conf_path, skip_validation=True
    )

    gpu_template = next(t for t in templates if t["template_id"] == "gpu")
    assert gpu_template["machine_types"] == {"p3.2xlarge": 2, "p3.8xlarge": 1}


def test_generate_scheduler_templates_honors_allocation_strategy_preference(
    strategy, slurm_conf_path
):
    class _FakeConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            if key == "scheduler":
                return {
                    "slurm": {
                        "partitions": {
                            "gpu": {
                                "instance_types": ["p3.2xlarge"],
                                "allocation_strategy": "lowest-price",
                            }
                        }
                    }
                }
            return default

    strategy._config_manager = _FakeConfigManager()

    templates = strategy.generate_scheduler_templates(
        slurm_conf=slurm_conf_path, skip_validation=True
    )

    gpu_template = next(t for t in templates if t["template_id"] == "gpu")
    assert gpu_template["allocation_strategy"] == "lowest-price"


def test_generate_scheduler_templates_unknown_user_instance_type_skips_validation_silently(
    strategy, slurm_conf_path
):
    """An instance type absent from the static map can't be validated and is passed through."""

    class _FakeConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            if key == "scheduler":
                return {"slurm": {"partitions": {"gpu": {"instance_types": ["g5.48xlarge"]}}}}
            return default

    strategy._config_manager = _FakeConfigManager()

    templates = strategy.generate_scheduler_templates(slurm_conf=slurm_conf_path)

    gpu_template = next(t for t in templates if t["template_id"] == "gpu")
    assert gpu_template["machine_types"] == {"g5.48xlarge": 1}


# ---------------------------------------------------------------------------
# _get_partition_preferences
# ---------------------------------------------------------------------------


def test_get_partition_preferences_no_config_manager_returns_empty(strategy):
    assert strategy._get_partition_preferences() == {}


def test_get_partition_preferences_swallows_config_errors(strategy):
    class _ExplodingConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            raise RuntimeError("config backend down")

    strategy._config_manager = _ExplodingConfigManager()

    assert strategy._get_partition_preferences() == {}


# ---------------------------------------------------------------------------
# _find_slurm_conf
# ---------------------------------------------------------------------------


def test_find_slurm_conf_prefers_cli_path(strategy, slurm_conf_path):
    assert strategy._find_slurm_conf(cli_path=slurm_conf_path) == slurm_conf_path


def test_find_slurm_conf_falls_back_to_config_path(strategy, slurm_conf_path):
    class _FakeConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            if key == "scheduler":
                return {"slurm": {"config_path": slurm_conf_path}}
            return default

    strategy._config_manager = _FakeConfigManager()

    assert strategy._find_slurm_conf() == slurm_conf_path


def test_find_slurm_conf_config_manager_error_is_swallowed(strategy, monkeypatch, tmp_path):
    class _ExplodingConfigManager:
        def get_configuration_value(self, key: str, default: Any = None) -> Any:
            raise RuntimeError("config backend down")

    strategy._config_manager = _ExplodingConfigManager()
    monkeypatch.delenv("SLURM_CONF", raising=False)
    monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))

    assert strategy._find_slurm_conf() is None


def test_find_slurm_conf_falls_back_to_env_var(strategy, slurm_conf_path, monkeypatch):
    monkeypatch.setenv("SLURM_CONF", slurm_conf_path)

    assert strategy._find_slurm_conf() == slurm_conf_path


def test_find_slurm_conf_falls_back_to_orb_root_default_path(strategy, tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_CONF", raising=False)
    monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))
    default_conf = tmp_path / "slurm.conf"
    default_conf.write_text("PartitionName=solo Nodes=solo-001\n")

    assert strategy._find_slurm_conf() == str(default_conf)


def test_find_slurm_conf_returns_none_when_nothing_found(strategy, tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_CONF", raising=False)
    monkeypatch.setenv("ORB_ROOT_DIR", str(tmp_path))

    assert strategy._find_slurm_conf() is None


# ---------------------------------------------------------------------------
# _parse_slurm_conf — malformed lines and prefix-mismatch fallthrough
# ---------------------------------------------------------------------------


def test_parse_slurm_conf_malformed_nodename_line_is_skipped(strategy, tmp_path):
    conf = tmp_path / "slurm.conf"
    conf.write_text("NodeName=\nPartitionName=solo Nodes=solo-001\n")

    partitions = strategy._parse_slurm_conf(str(conf))

    assert len(partitions) == 1
    assert partitions[0]["cpus"] == 1  # no NodeName spec resolved → defaults


def test_parse_slurm_conf_malformed_partitionname_line_is_skipped(strategy, tmp_path):
    conf = tmp_path / "slurm.conf"
    conf.write_text("PartitionName=\nPartitionName=solo Nodes=solo-001\n")

    partitions = strategy._parse_slurm_conf(str(conf))

    assert len(partitions) == 1
    assert partitions[0]["name"] == "solo"


def test_parse_slurm_conf_nodename_without_bracket_has_no_max_nodes_override(strategy, tmp_path):
    conf = tmp_path / "slurm.conf"
    conf.write_text("NodeName=solo-001 CPUs=2 RealMemory=4096\nPartitionName=solo Nodes=solo-001\n")

    partitions = strategy._parse_slurm_conf(str(conf))

    assert partitions[0]["max_nodes"] == 10  # default, since no bracket range to derive from


def test_parse_slurm_conf_prefix_fallback_exhausts_without_match(strategy, tmp_path):
    """Partition's Nodes= doesn't match any NodeName group, even by prefix — uses defaults."""
    conf = tmp_path / "slurm.conf"
    conf.write_text(
        "NodeName=compute-[001-010] CPUs=4 RealMemory=16000\n"
        "PartitionName=orphan Nodes=storage-[001-002]\n"
    )

    partitions = strategy._parse_slurm_conf(str(conf))

    assert partitions[0]["cpus"] == 1
    assert partitions[0]["memory_mb"] == 4096
