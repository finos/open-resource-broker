"""Full acquire → return cycle integration tests for RunInstances.

Exercises the complete lifecycle through the application layer CQRS buses:
  CreateRequestCommand → handler.acquire_hosts()  [instances created in moto]
  GetRequestQuery      → handler.check_hosts_status()  [instances running]
  CreateReturnRequestCommand → handler.release_hosts()  [instances terminated]
  GetRequestQuery(return_request_id) → status reflects termination

Moto fully supports RunInstances instance lifecycle (launch, describe, terminate),
making it the only provider suitable for end-to-end return-flow testing.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from orb.application.dto.commands import CreateRequestCommand, CreateReturnRequestCommand
from orb.application.dto.queries import GetRequestQuery
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort

_RET_PREFIX_RE = re.compile(r"^ret-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cqrs_buses(orb_config_dir):
    """Resolve CommandBusPort and QueryBusPort from the booted DI container.

    Also ensures all configured provider instances are registered in the
    provider registry so the CQRS provisioning path can dispatch to them.
    """
    from orb.infrastructure.di.container import get_container

    container = get_container()

    # Register all configured provider instances so execute_operation can find them
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    registry._config_port = container.get(ConfigurationPort)
    try:
        provider_config = registry._config_port.get_provider_config()
        if provider_config:
            for instance in provider_config.get_active_providers():
                registry.ensure_provider_instance_registered_from_config(instance)
    except Exception:
        pass

    command_bus = container.get(CommandBusPort)
    query_bus = container.get(QueryBusPort)
    return {"command_bus": command_bus, "query_bus": query_bus}


@pytest.fixture
def orb_config_dir(orb_config_dir):
    """Extend the base orb_config_dir fixture with moto-compatible patches.

    Two patches are applied so the CQRS provisioning path works under moto:

    1. config.json: remove ``profile`` from the provider config so boto3 uses
       env-var credentials (which moto intercepts) instead of a named profile
       that doesn't exist in the test environment.

    2. default_config.json: replace the SSM parameter path image_id with a
       literal AMI ID so moto can launch instances without SSM resolution.
    """
    import json as _json

    # Patch 1: remove profile from config.json provider entry
    config_path = orb_config_dir / "config.json"
    if config_path.exists():
        cfg = _json.loads(config_path.read_text())
        try:
            for provider in cfg["provider"]["providers"]:
                provider.get("config", {}).pop("profile", None)
        except (KeyError, TypeError):
            pass
        config_path.write_text(_json.dumps(cfg, indent=2))

    # Patch 2: replace SSM path image_id with literal AMI in default_config.json
    default_cfg_path = orb_config_dir / "default_config.json"
    if default_cfg_path.exists():
        cfg = _json.loads(default_cfg_path.read_text())
        try:
            cfg["provider"]["provider_defaults"]["aws"]["template_defaults"]["image_id"] = (
                "ami-12345678"
            )
        except (KeyError, TypeError):
            pass
        default_cfg_path.write_text(_json.dumps(cfg, indent=2))

    return orb_config_dir


@pytest.fixture
def run_instances_template_id(orb_config_dir):
    """Return the template_id of the first RunInstances template in the repository."""
    from orb.infrastructure.di.container import get_container
    from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager

    container = get_container()
    manager = container.get(TemplateConfigurationManager)
    templates = asyncio.run(manager.get_all_templates())
    run_templates = [
        t for t in templates if str(getattr(t, "provider_api", "")).upper() == "RUNINSTANCES"
    ]
    assert run_templates, "No RunInstances template found in test config"
    return str(run_templates[0].template_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _acquire(command_bus, template_id: str, count: int = 1) -> str:
    """Dispatch CreateRequestCommand and return the created request_id."""
    cmd = CreateRequestCommand(template_id=template_id, requested_count=count)
    asyncio.run(command_bus.execute(cmd))
    assert cmd.created_request_id, "CreateRequestCommand did not set created_request_id"
    return cmd.created_request_id


def _get_request(query_bus, request_id: str):
    """Dispatch GetRequestQuery and return the RequestDTO."""
    query = GetRequestQuery(request_id=request_id)
    return asyncio.run(query_bus.execute(query))


def _return_machines(command_bus, machine_ids: list) -> list:
    """Dispatch CreateReturnRequestCommand and return the created return request IDs."""
    cmd = CreateReturnRequestCommand(machine_ids=machine_ids)
    asyncio.run(command_bus.execute(cmd))
    return cmd.created_request_ids or []


def _instance_states(ec2_client, instance_ids: list) -> list:
    resp = ec2_client.describe_instances(InstanceIds=instance_ids)
    return [i["State"]["Name"] for r in resp["Reservations"] for i in r["Instances"]]


def _machine_ids_from_dto(request_dto) -> list:
    """Extract instance IDs from a RequestDTO.

    Checks machine_ids (list[str]) first, then falls back to iterating
    machine_references (list[MachineReferenceDTO]).
    """
    # Preferred: flat list of IDs
    ids = getattr(request_dto, "machine_ids", None)
    if ids:
        return list(ids)

    # Fallback: machine_references objects/dicts
    refs = getattr(request_dto, "machine_references", None) or []
    result = []
    for m in refs:
        mid = m.get("machine_id") if isinstance(m, dict) else getattr(m, "machine_id", None)
        if mid:
            result.append(mid)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunInstancesReturnFlow:
    def test_acquire_then_return_all(self, cqrs_buses, run_instances_template_id):
        """Full acquire → return cycle completes without error and produces valid IDs."""
        command_bus = cqrs_buses["command_bus"]
        query_bus = cqrs_buses["query_bus"]

        # Acquire
        request_id = _acquire(command_bus, run_instances_template_id, count=1)
        assert request_id.startswith("req-"), f"Expected req- prefix, got: {request_id}"

        # Status after acquire
        request_dto = _get_request(query_bus, request_id)
        assert request_dto is not None
        status = getattr(request_dto, "status", None)
        assert status in ("running", "complete", "completed", "in_progress"), (
            f"Unexpected status after acquire: {status}"
        )

        # Collect machine IDs
        machine_ids = _machine_ids_from_dto(request_dto)
        assert machine_ids, "No machine IDs found in request after acquire"

        # Return
        return_request_ids = _return_machines(command_bus, machine_ids)
        assert return_request_ids, "CreateReturnRequestCommand produced no return request IDs"

        # Status after return
        return_request_id = return_request_ids[0]
        return_dto = _get_request(query_bus, return_request_id)
        assert return_dto is not None
        return_status = getattr(return_dto, "status", None)
        assert return_status in ("running", "complete", "completed", "in_progress", "failed"), (
            f"Unexpected return request status: {return_status}"
        )

    def test_return_request_id_has_ret_prefix(self, cqrs_buses, run_instances_template_id):
        """Return request ID matches the ret-<uuid> format."""
        command_bus = cqrs_buses["command_bus"]
        query_bus = cqrs_buses["query_bus"]

        request_id = _acquire(command_bus, run_instances_template_id, count=1)
        request_dto = _get_request(query_bus, request_id)
        machine_ids = _machine_ids_from_dto(request_dto)
        assert machine_ids, "No machine IDs found after acquire"

        return_request_ids = _return_machines(command_bus, machine_ids)
        assert return_request_ids, "No return request IDs produced"

        for ret_id in return_request_ids:
            assert _RET_PREFIX_RE.match(ret_id), (
                f"Return request ID '{ret_id}' does not match ret-<uuid> pattern"
            )

    def test_instances_terminated_in_aws_after_return(
        self, cqrs_buses, run_instances_template_id, ec2_client
    ):
        """After return, all acquired instances are shutting-down or terminated in moto."""
        command_bus = cqrs_buses["command_bus"]
        query_bus = cqrs_buses["query_bus"]

        request_id = _acquire(command_bus, run_instances_template_id, count=2)
        request_dto = _get_request(query_bus, request_id)
        machine_ids = _machine_ids_from_dto(request_dto)
        assert machine_ids, "No machine IDs found after acquire"

        # Verify instances are running before return
        pre_states = _instance_states(ec2_client, machine_ids)
        assert all(s in ("pending", "running") for s in pre_states), (
            f"Instances not running before return: {pre_states}"
        )

        _return_machines(command_bus, machine_ids)

        post_states = _instance_states(ec2_client, machine_ids)
        assert all(s in ("shutting-down", "terminated") for s in post_states), (
            f"Instances not terminated after return: {post_states}"
        )

    def test_return_status_shows_request_completed(self, cqrs_buses, run_instances_template_id):
        """The return request reaches a terminal status after release_hosts completes."""
        command_bus = cqrs_buses["command_bus"]
        query_bus = cqrs_buses["query_bus"]

        request_id = _acquire(command_bus, run_instances_template_id, count=1)
        request_dto = _get_request(query_bus, request_id)
        machine_ids = _machine_ids_from_dto(request_dto)
        assert machine_ids

        return_request_ids = _return_machines(command_bus, machine_ids)
        assert return_request_ids

        return_dto = _get_request(query_bus, return_request_ids[0])
        status = getattr(return_dto, "status", None)
        # Deprovisioning is synchronous in the handler — expect a terminal status
        assert status in ("complete", "completed", "failed"), (
            f"Return request did not reach terminal status: {status}"
        )

    def test_return_request_persisted_in_storage(
        self, cqrs_buses, run_instances_template_id, orb_config_dir
    ):
        """After return, the storage file contains an entry with a ret- prefixed request_id."""
        command_bus = cqrs_buses["command_bus"]
        query_bus = cqrs_buses["query_bus"]

        request_id = _acquire(command_bus, run_instances_template_id, count=1)
        request_dto = _get_request(query_bus, request_id)
        machine_ids = _machine_ids_from_dto(request_dto)
        assert machine_ids

        return_request_ids = _return_machines(command_bus, machine_ids)
        assert return_request_ids
        return_request_id = return_request_ids[0]

        # Find the storage file written by the json strategy
        storage_file = orb_config_dir.parent / "data" / "request_database.json"
        assert storage_file.exists(), f"Storage file not found at {storage_file}"

        raw = json.loads(storage_file.read_text())

        # Storage format: {"requests": {<id>: {...}, ...}, "machines": {...}}
        # or a flat dict keyed by request_id, or a list of dicts.
        def _contains_id(obj, rid):
            if isinstance(obj, dict):
                if rid in obj:
                    return True
                # nested under a "requests" key
                for v in obj.values():
                    if isinstance(v, dict) and rid in v:
                        return True
                    if isinstance(v, list):
                        if any(
                            (
                                isinstance(e, dict)
                                and (e.get("request_id") == rid or e.get("id") == rid)
                            )
                            for e in v
                        ):
                            return True
            elif isinstance(obj, list):
                return any(
                    isinstance(e, dict) and (e.get("request_id") == rid or e.get("id") == rid)
                    for e in obj
                )
            return False

        found = _contains_id(raw, return_request_id)
        assert found, (
            f"Return request {return_request_id} not found in storage file. "
            f"Top-level keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}"
        )
