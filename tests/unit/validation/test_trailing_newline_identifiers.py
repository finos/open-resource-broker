"""Identifier validators must reject a value that only differs by a trailing newline.

``$`` matches before a final ``\\n`` under ``re.match``, so ``"abc\\n"`` used to
satisfy ``^[a-z]+$``. Each case builds an ``accepts(value)`` probe around a
validator and checks that the clean value passes while the newline-terminated
value does not.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from orb.domain.machine.machine_identifiers import MachineType
from orb.domain.machine.machine_metadata import IPAddressRange
from orb.domain.request.request_identifiers import RequestId, ResourceIdentifier
from orb.infrastructure.scheduler.slurm.cli_adapter import SlurmCliAdapter
from orb.infrastructure.scheduler.slurm.node_bootstrap import SlurmNodeBootstrap
from orb.infrastructure.scheduler.slurm.node_mapper import SlurmNodeMapper
from orb.infrastructure.scheduler.slurm.rest_client import SlurmRestClient
from orb.infrastructure.storage.components.sql_query_builder import SQLQueryBuilder
from orb.infrastructure.validation.input_validator import InputValidator
from orb.providers.aws.infrastructure.handlers.spot_fleet.validator import SpotFleetValidator
from orb.providers.aws.utilities.ssm_utils import extract_ssm_parameter_path, is_ssm_parameter_path
from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.infrastructure.handlers.vmss_handler import _validate_odata_filter_name
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.k8s.configuration.config import K8sNamingConfig, K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.strategy.k8s_provider_adapter import K8sResourceValidator
from orb.providers.k8s.utilities import labels as k8s_labels
from orb.providers.k8s.utilities.dns_names import (
    validate_dns_1123_label,
    validate_dns_1123_subdomain,
)
from orb.providers.k8s.validation.template_validator import K8sTemplateValidator
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler


def _raises(call: Callable[[], object]) -> bool:
    """Return True when ``call`` raises, i.e. the validator rejected its input."""
    try:
        call()
    except Exception:
        return True
    return False


def _spot_validator() -> SpotFleetValidator:
    return SpotFleetValidator.__new__(SpotFleetValidator)


def _spot_logger_validator() -> SpotFleetValidator:
    validator = _spot_validator()
    validator._logger = type("L", (), {"debug": staticmethod(lambda *a, **k: None)})()  # type: ignore[attr-defined]
    return validator


_UUID = "123e4567-e89b-12d3-a456-426614174000"

# (id, accepts(value) -> bool, valid value)
_CASES: list[tuple[str, Callable[[str], bool], str]] = [
    ("request_id", RequestId._is_valid_format, f"req-{_UUID}"),
    (
        "resource_identifier",
        lambda v: not _raises(lambda: ResourceIdentifier(resource_id="i-1", resource_type=v)),
        "instance",
    ),
    (
        "machine_type",
        lambda v: not _raises(lambda: MachineType(value=v)),
        "t2.micro",
    ),
    ("cidr", IPAddressRange._is_valid_cidr, "10.0.0.0/24"),
    (
        "input_alphanumeric",
        lambda v: not _raises(lambda: InputValidator.validate_alphanumeric(v)),
        "abc123",
    ),
    (
        "input_alphanumeric_dash",
        lambda v: not _raises(lambda: InputValidator.validate_alphanumeric(v, allow_dash=True)),
        "abc-1_2",
    ),
    (
        "input_integer",
        lambda v: not _raises(lambda: InputValidator.validate_integer(v)),
        "42",
    ),
    (
        "sql_identifier",
        lambda v: not _raises(lambda: SQLQueryBuilder("t", {})._validate_identifier(v)),
        "table_1",
    ),
    ("ssm_is_parameter", is_ssm_parameter_path, "ssm:/app/param"),
    (
        "ssm_extract_path",
        lambda v: extract_ssm_parameter_path(v) is not None,
        "ssm:/app/param",
    ),
    (
        "spot_service_role",
        _spot_logger_validator()._is_valid_service_role,
        "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet",
    ),
    (
        "spot_tagging_role",
        _spot_logger_validator()._is_valid_tagging_role,
        "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role",
    ),
    (
        "slurm_mapper_node",
        lambda v: not _raises(lambda: SlurmNodeMapper._validate_node_name(v)),
        "node[1-2],node3",
    ),
    (
        "slurm_mapper_machine",
        lambda v: not _raises(lambda: SlurmNodeMapper._validate_machine_id(v)),
        "i-0abc",
    ),
    (
        "slurm_bootstrap_node",
        lambda v: not _raises(lambda: SlurmNodeBootstrap._validate_node_name(v)),
        "node_1",
    ),
    (
        "slurm_bootstrap_ip",
        lambda v: not _raises(lambda: SlurmNodeBootstrap._validate_ip(v)),
        "10.0.0.1",
    ),
    (
        "slurm_bootstrap_conf_path",
        lambda v: not _raises(lambda: SlurmNodeBootstrap._validate_slurm_conf_path(v)),
        "/etc/slurm/slurm.conf",
    ),
    (
        "slurm_cli_name",
        lambda v: not _raises(lambda: SlurmCliAdapter._validate_name(v, "node")),
        "node_1",
    ),
    (
        "slurm_rest_name",
        lambda v: not _raises(lambda: SlurmRestClient._validate_name(v, "node")),
        "node_1",
    ),
    ("k8s_dns_label", lambda v: not _raises(lambda: validate_dns_1123_label(v)), "my-ns"),
    (
        "k8s_dns_subdomain",
        lambda v: not _raises(lambda: validate_dns_1123_subdomain(v)),
        "my-ns.example",
    ),
    ("k8s_label_prefix", lambda v: not _raises(lambda: k8s_labels._validate_prefix(v)), "orb"),
    ("k8s_label_key", lambda v: not _raises(lambda: k8s_labels._validate_key(v)), "orb"),
    ("k8s_label_value", lambda v: not _raises(lambda: k8s_labels._validate_value(v)), "orb"),
    ("k8s_namespace", lambda v: not _raises(lambda: k8s_labels.validate_namespace(v)), "orb"),
    (
        "k8s_resource_identifier",
        lambda v: K8sResourceValidator().validate_resource_identifier(v, "pod"),
        "my-pod",
    ),
    (
        "k8s_naming_prefix",
        lambda v: not _raises(lambda: K8sNamingConfig(prefix=v)),
        "orb",
    ),
    (
        "k8s_image",
        lambda v: not _raises(lambda: K8sTemplate._validate_image_id(v)),
        "nginx:1.25",
    ),
    (
        "gcp_project_id",
        lambda v: not _raises(lambda: GCPProviderConfig.validate_project_id(v)),
        "my-project-123",
    ),
    (
        "gcp_region",
        lambda v: not _raises(lambda: GCPProviderConfig.validate_region(v)),
        "us-central1",
    ),
    (
        "gcp_zones",
        lambda v: not _raises(lambda: GCPProviderConfig.validate_zones([v])),
        "us-central1-a",
    ),
    (
        "azure_resource_group",
        lambda v: not _raises(lambda: AzureProviderConfig.validate_resource_group(v)),
        "my-rg",
    ),
    (
        "azure_subscription_id",
        lambda v: not _raises(lambda: AzureProviderConfig.validate_subscription_id(v)),
        _UUID,
    ),
    (
        "azure_odata_filter_name",
        lambda v: not _raises(lambda: _validate_odata_filter_name(v, field_name="name")),
        "my-vmss",
    ),
    (
        "oci_region_from_ocid",
        lambda v: OCIComputeHandler._infer_region_from_ocid(v) is not None,
        "ocid1.subnet.oc1.eu-frankfurt-1.abcdef",
    ),
    (
        "k8s_validator_namespace",
        lambda v: not K8sTemplateValidator()._check_namespace(SimpleNamespace(namespace=v)),
        "my-ns",
    ),
    (
        "k8s_validator_service_account",
        lambda v: (
            not K8sTemplateValidator()._check_service_account(SimpleNamespace(service_account=v))
        ),
        "my.sa",
    ),
    (
        "k8s_config_namespace",
        lambda v: not _raises(lambda: K8sProviderConfig._validate_namespace_format(v)),
        "my-ns",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("accepts", "valid"), [c[1:] for c in _CASES], ids=[c[0] for c in _CASES])
def test_trailing_newline_rejected(accepts: Callable[[str], bool], valid: str) -> None:
    assert accepts(valid), "baseline value must be accepted"
    assert not accepts(valid + "\n"), "value with trailing newline must be rejected"
