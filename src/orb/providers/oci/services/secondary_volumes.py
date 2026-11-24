"""Manage blank OCI block volumes with receipts bound to a single worker and slot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from orb.providers.oci.cli_client import OCICommandError
from orb.providers.oci.volume_journal import OCIVolumeJournal, VolumeSlot


def _field(row: dict[str, Any], cli: str, api: str) -> Any:
    return row.get(cli, row.get(api))


def _state(row: dict[str, Any]) -> str:
    return str(_field(row, "lifecycle-state", "lifecycleState") or "").upper()


def _identity(row: dict[str, Any], slot: VolumeSlot, *, attachment: bool = False) -> str:
    resource_id = row.get("id")
    prefix = "ocid1.volumeattachment." if attachment else "ocid1.volume."
    if not isinstance(resource_id, str) or not resource_id.startswith(prefix):
        raise RuntimeError("OCI returned an invalid secondary-volume resource identity")
    if _field(row, "compartment-id", "compartmentId") != slot.compartment_id:
        raise RuntimeError("OCI returned a resource outside the secondary-volume compartment")
    if (
        not attachment
        and _field(row, "availability-domain", "availabilityDomain") != slot.availability_domain
    ):
        raise RuntimeError("OCI returned a volume outside the worker availability domain")
    return resource_id


class OCISecondaryVolumes:
    """One bounded reconciliation pass. Subsequent ORB status polls resume durable work."""

    def __init__(
        self,
        journal: OCIVolumeJournal,
        cli: Callable[..., dict[str, Any]],
        post: Callable[[str, dict[str, Any], str], dict[str, Any]],
    ) -> None:
        self.journal = journal
        self.cli = cli
        self.post = post

    def _issue(
        self,
        request: str,
        worker: str,
        slot: VolumeSlot,
        action: Literal["create", "attach"],
        resource: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        token = self.journal.retry_token(request, worker, slot.index, action)
        try:
            return self.post(resource, body, token)["data"]
        except OCICommandError as exc:
            # Reuse the original operation on an ambiguous transport response. A
            # definitive rejection needs operator intervention, never a fresh token.
            if exc.status and 400 <= exc.status < 500 and exc.status not in {408, 429}:
                self.journal.refuse(request, worker, slot.index, action)
            raise

    def _volume(self, volume_id: str, slot: VolumeSlot) -> tuple[dict[str, Any], str | None]:
        result = self.cli(["bv", "volume", "get", "--volume-id", volume_id])
        row = result["data"]
        if _identity(row, slot) != volume_id:
            raise RuntimeError("Volume read does not match its saved ID")
        return row, result.get("etag")

    @staticmethod
    def _check_attachment(
        row: dict[str, Any], slot: VolumeSlot, worker: str, volume_id: str
    ) -> str:
        attachment_id = _identity(row, slot, attachment=True)
        if (
            _field(row, "instance-id", "instanceId") != worker
            or _field(row, "volume-id", "volumeId") != volume_id
        ):
            raise RuntimeError("Attachment does not match the saved worker and volume")
        return attachment_id

    def _attach(self, request: str, worker: str, slot: VolumeSlot) -> str:
        volume_id = slot.volume_id
        if not volume_id:
            body = {
                "compartmentId": slot.compartment_id,
                "availabilityDomain": slot.availability_domain,
                "sizeInGBs": slot.spec["size_in_gbs"],
            }
            if "display_name" in slot.spec:
                body["displayName"] = slot.spec["display_name"]
            if "vpus_per_gb" in slot.spec:
                body["vpusPerGB"] = slot.spec["vpus_per_gb"]
            created = self._issue(request, worker, slot, "create", "volumes", body)
            volume_id = _identity(created, slot)
            self.journal.record_volume(request, worker, slot.index, volume_id)
        volume, _ = self._volume(volume_id, slot)
        if _state(volume) in {"PROVISIONING", "RESTORING"}:
            return "pending"
        if _state(volume) != "AVAILABLE":
            raise RuntimeError(f"Secondary volume is {_state(volume) or 'unreadable'}")
        attachment_id = slot.attachment_id
        if not attachment_id:
            body = {
                "instanceId": worker,
                "volumeId": volume_id,
                "type": slot.spec["attachment_type"],
                "isShareable": False,
            }
            attached = self._issue(request, worker, slot, "attach", "volumeAttachments", body)
            attachment_id = self._check_attachment(attached, slot, worker, volume_id)
            self.journal.record_attachment(request, worker, slot.index, attachment_id)
        attachment = self.cli(
            ["compute", "volume-attachment", "get", "--volume-attachment-id", attachment_id]
        )["data"]
        if self._check_attachment(attachment, slot, worker, volume_id) != attachment_id:
            raise RuntimeError("Attachment read does not match its saved ID")
        if _state(attachment) == "ATTACHED":
            if not slot.attached_confirmed:
                self.journal.confirm_attached(request, worker, slot.index)
            return "attached"
        if _state(attachment) == "ATTACHING":
            return "pending"
        raise RuntimeError(f"Secondary volume attachment is {_state(attachment) or 'unreadable'}")

    def _release(self, request: str, worker: str, slot: VolumeSlot) -> str:
        # Standalone Block Volumes survive instance termination. OCI detaches
        # them as it terminates the worker. Never detach an unrelated attachment.
        if not slot.returning:
            raise RuntimeError("Volume return has no durable acquisition fence")
        volume_id = slot.volume_id
        if not volume_id:
            if slot.create_started:
                raise RuntimeError(
                    "Create was issued without a durable volume ID; ownership unresolved"
                )
            self.journal.finish(request, worker, slot.index, "skipped")
            return "skipped"
        volume, etag = self._volume(volume_id, slot)
        state = _state(volume)
        if state == "TERMINATED" and slot.spec["delete_on_return"]:
            self.journal.finish(request, worker, slot.index, "deleted")
            return "deleted"
        if state in {"PROVISIONING", "RESTORING", "TERMINATING"}:
            return "pending"
        if state != "AVAILABLE":
            raise RuntimeError(f"Secondary volume is {state or 'unreadable'}")
        inventory = self.cli(
            [
                "compute",
                "volume-attachment",
                "list",
                "--compartment-id",
                slot.compartment_id,
                "--volume-id",
                volume_id,
                "--all",
            ]
        )
        # The OCI CLI prints nothing for an empty list with no displayable headers.
        # run_oci has already checked command success; a nonempty malformed or paged
        # response is never accepted as proof that the observed inventory is complete.
        attachments = inventory.get("data") if inventory else []
        if inventory.get("opc-next-page") or not isinstance(attachments, list):
            raise RuntimeError("Could not inventory secondary volume attachments")
        for binding in attachments:
            if (
                not isinstance(binding, dict)
                or _field(binding, "volume-id", "volumeId") != volume_id
            ):
                raise RuntimeError("Attachment inventory contains an unexpected volume")
            if _state(binding) == "DETACHED":
                continue
            if self._check_attachment(
                binding, slot, worker, volume_id
            ) != slot.attachment_id or _state(binding) not in {
                "ATTACHED",
                "ATTACHING",
                "DETACHING",
            }:
                raise RuntimeError("Volume has an unexpected active attachment; preserved")
            return "pending"
        if not slot.spec["delete_on_return"]:
            self.journal.finish(request, worker, slot.index, "retained")
            return "retained"
        if not etag:
            raise RuntimeError("Missing volume ETag; conditional deletion cannot proceed")
        self.cli(
            [
                "bv",
                "volume",
                "delete",
                "--volume-id",
                volume_id,
                "--if-match",
                etag,
                "--force",
                "--no-retry",
            ]
        )
        # GET must actually report TERMINATED before the return is complete.
        return "pending"

    def reconcile(self, request: str, worker: str, *, release: bool) -> list[dict[str, Any]]:
        slots = self.journal.slots(request, worker)
        if not slots:
            raise RuntimeError("Secondary-volume plan missing from originating request")
        outcomes: list[dict[str, Any]] = []
        for slot in slots:
            outcome = {"slot": slot.index, "delete_on_return": slot.spec["delete_on_return"]}
            try:
                if slot.outcome:
                    state = slot.outcome
                elif release:
                    state = self._release(request, worker, slot)
                elif slot.returning:
                    state = "returning"
                else:
                    state = self._attach(request, worker, slot)
                outcome["status"] = state
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                outcome.update(status="unresolved", error=str(exc))
            outcomes.append(outcome)
        # Refresh receipts, including a create that succeeded before attach failed.
        for outcome, slot in zip(outcomes, self.journal.slots(request, worker), strict=True):
            outcome.update(volume_id=slot.volume_id, attachment_id=slot.attachment_id)
        return outcomes
