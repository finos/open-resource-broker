"""Tests for derive_cpu_ram_from_instance_type and helpers."""

from unittest.mock import MagicMock

import pytest

import orb.providers.aws.utilities.ec2.instances as mod


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(mod, "_instance_spec_cache", None)


# ---------------------------------------------------------------------------
# A. Heuristic tests (no ec2_client)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("instance_type,expected", [
    # t2 generation
    ("t2.nano",   ("1", "512")),
    ("t2.micro",  ("1", "1024")),
    ("t2.small",  ("1", "2048")),
    ("t2.medium", ("2", "4096")),
    # t3 generation
    ("t3.micro",  ("2", "1024")),
    ("t3.small",  ("2", "2048")),
    # t3a (same specs as t3)
    ("t3a.xlarge", ("4", "16384")),
    # t4g (same specs as t3)
    ("t4g.large",  ("2", "8192")),
])
def test_heuristic_t_family(instance_type, expected):
    assert mod.derive_cpu_ram_from_instance_type(instance_type) == expected


@pytest.mark.parametrize("instance_type,expected", [
    # c-family: 2 GiB/vCPU
    ("c5.large",  ("2", str(2 * 2 * 1024))),
    # m-family: 4 GiB/vCPU
    ("m5.xlarge", ("4", str(4 * 4 * 1024))),
    # r-family: 8 GiB/vCPU
    ("r5.large",  ("2", str(2 * 8 * 1024))),
])
def test_heuristic_standard_families(instance_type, expected):
    assert mod.derive_cpu_ram_from_instance_type(instance_type) == expected


def test_heuristic_unknown_family():
    # q-family not in _FAMILY_MEM_RATIO — defaults to 4 GiB/vCPU
    assert mod.derive_cpu_ram_from_instance_type("q5.large") == ("2", str(2 * 4 * 1024))


def test_heuristic_unknown_size():
    # unknown size defaults to 2 vCPUs, m-family 4 GiB/vCPU
    assert mod.derive_cpu_ram_from_instance_type("m5.unknown") == ("2", str(2 * 4 * 1024))


def test_heuristic_unparseable():
    assert mod.derive_cpu_ram_from_instance_type("invalid") == ("1", "1024")


# ---------------------------------------------------------------------------
# B. API cache tests
# ---------------------------------------------------------------------------

def _make_ec2_client(instance_types_page):
    """Build a mock ec2_client whose paginator yields one page."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"InstanceTypes": instance_types_page}]
    client = MagicMock()
    client.get_paginator.return_value = paginator
    return client


def test_api_cache_returns_api_values():
    ec2_client = _make_ec2_client([
        {"InstanceType": "m5.large", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 8192}},
    ])
    result = mod.derive_cpu_ram_from_instance_type("m5.large", ec2_client=ec2_client)
    assert result == ("2", "8192")


def test_api_cache_called_only_once():
    ec2_client = _make_ec2_client([
        {"InstanceType": "m5.large", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 8192}},
    ])
    mod.derive_cpu_ram_from_instance_type("m5.large", ec2_client=ec2_client)
    mod.derive_cpu_ram_from_instance_type("m5.large", ec2_client=ec2_client)
    # paginator should only have been created once
    assert ec2_client.get_paginator.call_count == 1


def test_api_failure_falls_back_to_heuristic(monkeypatch):
    # _load_instance_specs returns empty dict (simulates API failure)
    monkeypatch.setattr(mod, "_load_instance_specs", lambda _: {})
    ec2_client = MagicMock()
    result = mod.derive_cpu_ram_from_instance_type("m5.large", ec2_client=ec2_client)
    # heuristic: m-family large → 2 vCPUs, 4 GiB/vCPU
    assert result == ("2", str(2 * 4 * 1024))


def test_api_failure_no_second_api_call(monkeypatch):
    load_calls = []

    def fake_load(_):
        load_calls.append(1)
        return {}

    monkeypatch.setattr(mod, "_load_instance_specs", fake_load)
    ec2_client = MagicMock()
    mod.derive_cpu_ram_from_instance_type("m5.large", ec2_client=ec2_client)
    mod.derive_cpu_ram_from_instance_type("m5.large", ec2_client=ec2_client)
    # cache was populated (as empty dict) after first call — no second load
    assert len(load_calls) == 1
