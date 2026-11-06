"""Template snapshot on the request survives restart and is used by return."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.commands import CreateRequestCommand
from orb.application.dto.template import TemplateDTO
from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator
from orb.application.services.template_overrides import apply_template_overrides
from orb.application.services.template_snapshot import (
    SNAPSHOT_FIELDS,
    SNAPSHOT_METADATA_KEY,
    capture_template_snapshot,
    restore_template_snapshot,
)

_ORIGIN_ID = "req-11111111-1111-4111-8111-111111111111"


def _template() -> TemplateDTO:
    return TemplateDTO(
        template_id="tmpl-1",
        subnet_ids=["subnet-a"],
        user_data="secret-boot-code",
        instance_profile="role-secret",
        key_name="key-secret",
        encryption_key="kms-secret",
        provider_data={"aws": {"k": "pd-secret"}},
        provider_type="aws",
        provider_api="EC2Fleet",
    )


@pytest.mark.unit
class TestSnapshotRoundTrip:
    def test_roundtrip_preserves_fields_and_excludes_bootstrap(self):
        snap = capture_template_snapshot(_template())
        assert "user_data" not in snap
        restored = restore_template_snapshot(json.loads(json.dumps(snap)), "tmpl-1")
        assert restored is not None
        assert restored.subnet_ids == ["subnet-a"]
        assert restored.provider_api == "EC2Fleet"

    def test_only_teardown_fields_are_stored(self):
        snap = capture_template_snapshot(_template())
        assert set(snap) <= set(SNAPSHOT_FIELDS)
        blob = json.dumps(snap)
        for secret in ("secret-boot-code", "role-secret", "key-secret", "kms-secret", "pd-secret"):
            assert secret not in blob

    def test_request_status_output_has_no_sensitive_template_fields(self):
        from orb.application.request.dto import RequestDTO
        from orb.application.services.request_creation_service import RequestCreationService

        tmpl = apply_template_overrides(_template(), {"subnet_ids": ["subnet-o"]})
        selection = MagicMock(provider_type="aws", provider_name="aws-1")
        selection.selection_reason = "r"
        selection.confidence = 1.0
        cmd = CreateRequestCommand(
            template_id="tmpl-1", requested_count=1, template_overrides={"subnet_ids": ["subnet-o"]}
        )
        request = RequestCreationService(MagicMock()).create_machine_request(cmd, tmpl, selection)

        out = json.dumps(RequestDTO.from_domain(request).to_dict(verbose=True), default=str)
        assert "subnet-o" in out
        for secret in ("secret-boot-code", "role-secret", "key-secret", "kms-secret", "pd-secret"):
            assert secret not in out

    @pytest.mark.parametrize("key", ["template_snapshot", "template_overrides"])
    def test_additional_data_cannot_plant_reserved_keys(self, key):
        from orb.application.services.request_creation_service import RequestCreationService
        from orb.domain.request.exceptions import RequestValidationError

        selection = MagicMock(provider_type="aws", provider_name="aws-1")
        cmd = CreateRequestCommand(
            template_id="tmpl-1", requested_count=1, additional_data={key: {"template_id": "x"}}
        )
        with pytest.raises(RequestValidationError, match="reserved"):
            RequestCreationService(MagicMock()).create_machine_request(cmd, _template(), selection)

    def test_non_dto_is_not_captured(self):
        assert capture_template_snapshot(MagicMock()) is None

    def test_mismatched_template_id_or_garbage_is_ignored(self):
        snap = capture_template_snapshot(_template())
        assert restore_template_snapshot(snap, "other") is None
        assert restore_template_snapshot({"nope": 1}, "tmpl-1") is None
        assert restore_template_snapshot(None, "tmpl-1") is None


def _orchestrator(origin_metadata: dict, stored_template):
    origin = MagicMock()
    origin.metadata = origin_metadata
    uow = MagicMock()
    uow.requests.get_by_id.return_value = origin
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    uow_factory = MagicMock()
    uow_factory.create_unit_of_work.return_value = uow
    scheduler = MagicMock()
    scheduler.format_template_for_provider.side_effect = lambda t: t.to_dict()
    query_bus = MagicMock()
    query_bus.execute = AsyncMock(return_value=stored_template)
    provider = MagicMock()
    provider.execute_operation = AsyncMock(return_value=MagicMock(success=True, error_message=None))
    orch = DeprovisioningOrchestrator(
        uow_factory=uow_factory,
        logger=MagicMock(),
        query_bus=query_bus,
        provider_selection_port=provider,
        scheduler=scheduler,
        config_manager=MagicMock(),
    )
    return orch, provider, query_bus, scheduler


def _machine():
    m = MagicMock()
    m.machine_id.value = "i-1"
    m.template_id = "tmpl-1"
    m.request_id = _ORIGIN_ID
    return m


@pytest.mark.unit
class TestReturnUsesSnapshot:
    @pytest.mark.asyncio
    async def test_return_after_restart_with_override_and_template_gone(self):
        overridden = apply_template_overrides(_template(), {"subnet_ids": ["subnet-override"]})
        # What storage hands back after a process restart: plain JSON.
        metadata = json.loads(
            json.dumps({SNAPSHOT_METADATA_KEY: capture_template_snapshot(overridden)})
        )
        orch, provider, query_bus, scheduler = _orchestrator(metadata, stored_template=None)

        result = await orch._process_resource_group(
            "aws", "EC2Fleet", "res-1", [_machine()], MagicMock(request_id="ret-1")
        )

        assert result["success"] is True, result
        query_bus.execute.assert_not_called()
        sent = scheduler.format_template_for_provider.call_args.args[0]
        assert sent.subnet_ids == ["subnet-override"]
        operation = provider.execute_operation.call_args.args[1]
        assert operation.parameters["template_config"]["subnet_ids"] == ["subnet-override"]

    @pytest.mark.asyncio
    async def test_falls_back_to_stored_template_without_snapshot(self):
        stored = _template()
        orch, _, query_bus, scheduler = _orchestrator({}, stored_template=stored)

        result = await orch._process_resource_group(
            "aws", "EC2Fleet", "res-1", [_machine()], MagicMock(request_id="ret-1")
        )

        assert result["success"] is True
        query_bus.execute.assert_awaited_once()
        assert scheduler.format_template_for_provider.call_args.args[0] is stored
