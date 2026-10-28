"""RDMA placement must survive persistence and be verified before any live launch."""

from importlib import import_module
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.mocking.dry_run_context import dry_run_context
from orb.infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.mapping.template_mapper import OCITemplateMapper
from orb.providers.oci.provider_plugin import OCIPlugin
from orb.providers.oci.scheduler.hostfactory_field_mapping import OCIFieldMapping


@pytest.mark.parametrize(
    ("provider", "class_name"),
    [
        ("aws", "AWSFieldMapping"),
        ("azure", "AzureFieldMapping"),
        ("gcp", "GCPFieldMapping"),
        ("k8s", "K8sFieldMapping"),
    ],
)
def test_other_providers_keep_existing_hostfactory_output(monkeypatch, provider, class_name):
    module = import_module(f"orb.providers.{provider}.scheduler.hostfactory_field_mapping")
    adapter = getattr(module, class_name)()
    monkeypatch.setitem(FieldMappingRegistry._store, provider, adapter)
    assert not hasattr(adapter, "prepare_output_fields")
    mapper = HostFactoryFieldMapper(provider)
    flat = {
        "template_id": "existing",
        "machine_image": "original-image",
        "machine_types": {"original-type": 1},
        "provider_type": provider,
    }
    before = mapper.map_output_fields(flat)
    after = mapper.map_output_fields(
        {
            **flat,
            "provider_config": {
                "fleet_type": "maintain",
                "vm_size": "new-size",
                "project_id": "new-project",
                "namespace": "new-namespace",
                "machine_image": "different-image",
            },
        }
    )
    assert after == before


def test_oci_dotted_mapping_roundtrips_provider_extension(monkeypatch):
    monkeypatch.setitem(FieldMappingRegistry._store, "oci", OCIFieldMapping())
    mapper = HostFactoryFieldMapper("oci")
    source = {
        "compartment_id": "explicit",
        "provider_config": {
            "compartment_id": "nested",
            "compute_cluster_id": "cluster-1",
            "availability_domain": "AD-1",
            "unmapped_option": "private",
        },
    }
    external = mapper.map_output_fields(source)
    assert external["computeClusterId"] == "cluster-1"
    assert external["compartmentId"] == "nested"
    assert mapper.map_input_fields(external)["provider_config"]["compute_cluster_id"] == "cluster-1"
    assert external["availabilityDomain"] == "AD-1"
    assert "provider_config" not in external
    assert "unmapped_option" not in external
    assert source["provider_config"]["compartment_id"] == "nested"


@pytest.fixture
def template():
    return {
        "template_id": "rdma",
        "provider_type": "oci",
        "provider_api": "OCICompute",
        "image_id": "image-hpc",
        "machine_types": {"BM.Optimized3.36": 1},
        "subnet_ids": ["subnet-1"],
        "compartment_id": "compartment-1",
        "availability_domain": "AD-1",
        "compute_cluster_id": "cluster-1",
        "agent_config": {
            "pluginsConfig": [
                {"name": "Compute HPC RDMA Authentication", "desiredState": "ENABLED"}
            ],
        },
    }


def make_handler():
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1")
    handler._oci_cli_available = True
    return handler


def test_compute_cluster_survives_nested_template_and_dto(template):
    OCIPlugin.reset_for_testing()
    plugin = OCIPlugin()
    plugin.initialize_provider()
    plugin.register_services_with_di(MagicMock())
    try:
        cluster_id = template.pop("compute_cluster_id")
        domain = OCITemplate(**template, provider_config={"compute_cluster_id": cluster_id})
        dto = TemplateDTOFactory().from_domain(domain)
        payload = OCITemplateMapper.build_launch_payload(dto.to_dict(), "rdma-1")
        assert payload["compute_cluster_id"] == cluster_id
        assert payload["agent_config"] == template["agent_config"]
        assert payload["shape"] == "BM.Optimized3.36"
        assert "shape_config" not in payload
    finally:
        OCIPlugin.reset_for_testing()


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        (
            {"machine_types": {"VM.Standard.E5.Flex": 1}},
            "compute_cluster_requires_bare_metal_shape",
        ),
        ({"availability_domain": None}, "availability_domain(required for compute_cluster_id)"),
        ({"capacity_type": "preemptible"}, "compute_cluster_requires_ondemand_capacity"),
        ({"agent_config": "invalid"}, "agent_config(must be an object)"),
    ],
)
def test_invalid_compute_cluster_templates_are_rejected(template, changes, error):
    assert error in OCITemplateMapper.validate_required_fields({**template, **changes})


@pytest.mark.asyncio
async def test_two_nodes_use_one_verified_cluster(template):
    handler = make_handler()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["compute", "compute-cluster", "get"]:
            return {
                "data": {
                    "lifecycle-state": "ACTIVE",
                    "compartment-id": "compartment-1",
                    "availability-domain": "AD-1",
                }
            }
        assert args[0] == "raw-request"
        assert kwargs["payload"]["computeClusterId"] == "cluster-1"
        assert "--no-retry" in args
        assert kwargs["payload"]["agentConfig"] == template["agent_config"]
        assert args[args.index("--read-timeout") + 1] == "90"
        assert "shapeConfig" not in kwargs["payload"]
        return {"data": {"id": f"instance-{len(calls)}", "compute-cluster-id": "cluster-1"}}

    handler._run_oci = MagicMock(side_effect=run)
    result = await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template, "count": 2},
        )
    )
    assert result.success, result.error_message
    assert len(calls) == 3
    assert len(result.data["instance_ids"]) == 2
    assert all(
        i["provider_data"]["compute_cluster_id"] == "cluster-1" for i in result.data["instances"]
    )


@pytest.mark.parametrize(
    "cluster",
    [
        {
            "lifecycle-state": "DELETED",
            "compartment-id": "compartment-1",
            "availability-domain": "AD-1",
        },
        {"lifecycle-state": "ACTIVE", "compartment-id": "other", "availability-domain": "AD-1"},
        {
            "lifecycle-state": "ACTIVE",
            "compartment-id": "compartment-1",
            "availability-domain": "AD-2",
        },
        {},
    ],
)
@pytest.mark.asyncio
async def test_inactive_or_mismatched_cluster_never_launches_nodes(template, cluster):
    handler = make_handler()
    handler._run_oci = MagicMock(return_value={"data": cluster})
    result = await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template, "count": 2},
        )
    )
    assert not result.success
    assert result.error_code == "OCI_COMPUTE_CLUSTER_INVALID"
    handler._run_oci.assert_called_once()
    assert handler._run_oci.call_args.args[0][:3] == ["compute", "compute-cluster", "get"]


@pytest.mark.asyncio
async def test_cluster_permission_failure_is_not_replaced_with_standalone_nodes(template):
    handler = make_handler()
    handler._run_oci = MagicMock(side_effect=RuntimeError("NotAuthorizedOrNotFound"))
    result = await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template, "count": 2},
        )
    )
    assert not result.success
    assert result.error_code == "OCI_COMPUTE_CLUSTER_INVALID"
    handler._run_oci.assert_called_once()


@pytest.mark.asyncio
async def test_rdma_dry_run_does_not_contact_oci(template):
    handler = make_handler()
    handler._run_oci = MagicMock(side_effect=AssertionError("dry run contacted OCI"))
    result = await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template, "count": 2},
            context={"dry_run": True},
        )
    )
    assert result.success
    assert all(
        p["provider_data"]["compute_cluster_id"] == "cluster-1" for p in result.data["instances"]
    )
    handler._run_oci.assert_not_called()


@pytest.mark.asyncio
async def test_cli_global_dry_run_blocks_live_cli_without_pytest_or_operation_flags(template):
    handler = make_handler()
    handler._run_oci = MagicMock(side_effect=AssertionError("global dry run contacted OCI"))
    with dry_run_context():
        result = await handler.create_instances(
            ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={"template_config": template, "count": 2},
            )
        )
    assert result.success
    assert len(result.data["instance_ids"]) == 2
    handler._run_oci.assert_not_called()


def test_status_exposes_observed_cluster_membership():
    handler = make_handler()
    handler._run_oci = MagicMock(
        return_value={"data": {"lifecycle-state": "RUNNING", "compute-cluster-id": "cluster-1"}}
    )
    handler._get_instance_network = MagicMock(return_value={"private_ip": "10.0.0.2"})
    result = handler.get_instance_status(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["instance-1"]},
        )
    )
    assert result.success
    assert result.data["instances"][0]["provider_data"]["compute_cluster_id"] == "cluster-1"


@pytest.mark.parametrize("is_member", [True, False])
def test_status_verifies_membership_when_get_instance_omits_cluster_id(is_member):
    handler = make_handler()
    handler._run_oci = MagicMock(
        side_effect=[
            {
                "data": {
                    "lifecycle-state": "RUNNING",
                    "compartment-id": "compartment-1",
                    "freeform-tags": {"orb_compute_cluster_id": "cluster-1"},
                }
            },
            {"data": [{"id": "instance-1" if is_member else "other"}]},
        ]
    )
    handler._get_instance_network = MagicMock(return_value={"private_ip": "10.0.0.2"})
    result = handler.get_instance_status(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={"instance_ids": ["instance-1"]},
        )
    )
    assert result.success is is_member
    args = handler._run_oci.call_args.args[0]
    assert args[args.index("--compute-cluster-id") + 1] == "cluster-1"
    if is_member:
        assert result.data["instances"][0]["provider_data"]["compute_cluster_id"] == "cluster-1"
    else:
        assert result.error_code == "OCI_STATUS_FAILED"


def test_return_does_not_delete_shared_compute_cluster():
    handler = make_handler()
    handler._run_oci = MagicMock(return_value={})
    result = handler.terminate_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"instance_ids": ["instance-1", "instance-2"]},
        )
    )
    assert result.success
    assert all(
        c.args[0][:3] == ["compute", "instance", "terminate"]
        for c in handler._run_oci.call_args_list
    )


@pytest.mark.parametrize("cluster_mode", [True, False])
@pytest.mark.asyncio
async def test_partial_launch_rolls_back_only_its_accepted_nodes(template, cluster_mode):
    if not cluster_mode:
        template.pop("compute_cluster_id")
    handler = make_handler()
    launched = 0
    rollback = []

    def run(args, **kwargs):
        nonlocal launched
        if args[:3] == ["compute", "compute-cluster", "get"]:
            return {
                "data": {
                    "lifecycle-state": "ACTIVE",
                    "compartment-id": "compartment-1",
                    "availability-domain": "AD-1",
                }
            }
        if args[:3] == ["compute", "instance", "terminate"]:
            rollback.append(args[args.index("--instance-id") + 1])
            assert args[args.index("--preserve-boot-volume") + 1] == "false"
            return {}
        launched += 1
        if cluster_mode:
            assert kwargs["payload"]["freeformTags"]["orb_request_id"] == "request-1"
        if launched == 2:
            raise RuntimeError("Out of host capacity")
        return {"data": {"id": "accepted-node"}}

    handler._run_oci = MagicMock(side_effect=run)
    result = await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template, "count": 2, "request_id": "request-1"},
        )
    )
    assert not result.success
    assert result.metadata["launched_instance_ids"] == ["accepted-node"]
    assert rollback == ["accepted-node"]
    assert result.metadata["rollback_requested_instance_ids"] == rollback
    assert result.metadata["rollback_errors"] == []


@pytest.mark.asyncio
async def test_failed_cluster_rollback_retains_ids_for_recovery(template):
    handler = make_handler()
    handler._run_oci = MagicMock(
        side_effect=[
            {
                "data": {
                    "lifecycle-state": "ACTIVE",
                    "compartment-id": "compartment-1",
                    "availability-domain": "AD-1",
                }
            },
            {"data": {"id": "accepted-node"}},
            RuntimeError("Out of host capacity"),
            RuntimeError("Termination denied"),
        ]
    )
    result = await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={"template_config": template, "count": 2},
        )
    )
    assert not result.success
    assert result.metadata["launched_instance_ids"] == ["accepted-node"]
    assert result.metadata["rollback_requested_instance_ids"] == []
    assert result.metadata["rollback_errors"] == [
        {"instance_id": "accepted-node", "error": "Termination denied"}
    ]
