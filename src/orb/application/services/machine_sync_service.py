"""Machine sync service for provider integration."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from orb.application.services.provider_registry_service import ProviderRegistryService

from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.services.machine_coordinates import load_machine_coordinates
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.exceptions import ConcurrencyError
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request

# Metadata key set by ``fetch_provider_machines`` when the provider could not be
# queried, so callers keep the stored state instead of treating the missing
# provider verdict as a contract violation.
PROVIDER_FETCH_FAILED = "provider_fetch_failed"


class FailedProviderFetch(list):  # type: ignore[type-arg]
    """Stored machines handed back because the provider fetch failed.

    Behaves as the plain list of stored machines for callers that only want to
    carry on with the stored state, but lets the sync step tell "the provider
    was asked and answered" from "the provider could not be reached".
    """


class MachineSyncService:
    """Provider synchronization service."""

    def __init__(
        self,
        command_bus: CommandBusPort,
        uow_factory: UnitOfWorkFactory,
        config_port: ConfigurationPort,
        logger: LoggingPort,
        provider_registry_service: Optional["ProviderRegistryService"] = None,
    ) -> None:
        self.command_bus = command_bus
        self.uow_factory = uow_factory
        self._config_port = config_port
        self.logger = logger
        self._provider_registry_service: Optional[ProviderRegistryService] = (
            provider_registry_service
        )

    @property
    def provider_registry_service(self) -> Optional["ProviderRegistryService"]:
        """Return the injected provider registry service."""
        return self._provider_registry_service

    async def populate_missing_machine_ids(self, request: Request) -> None:
        """Populate missing machine IDs via command."""
        if request.needs_machine_id_population():
            try:
                from orb.application.dto.commands import PopulateMachineIdsCommand

                populate_command = PopulateMachineIdsCommand(
                    request_id=str(request.request_id.value)
                )
                await self.command_bus.execute(populate_command)
                self.logger.debug(
                    f"Triggered machine ID population for request {request.request_id.value}"
                )
            except Exception as e:
                self.logger.error(f"Failed to populate machine IDs: {e}")

    async def fetch_provider_machines(
        self, request: Request, db_machines: list[Machine]
    ) -> Tuple[list[Machine], dict]:
        """Fetch machines from provider."""
        try:
            from orb.domain.base.operations import (
                Operation as ProviderOperation,
                OperationType as ProviderOperationType,
            )

            # For return requests, always use instance-level status for the specific
            # machines being returned — not resource-level discovery which returns all
            # ASG/fleet instances including unrelated ones.
            if request.request_type.value == "return" and request.machine_ids:
                operation_type = ProviderOperationType.GET_INSTANCE_STATUS
                parameters = {
                    "instance_ids": request.machine_ids,
                    "template_id": request.template_id,
                    "machine_coordinates": load_machine_coordinates(
                        self.uow_factory, list(request.machine_ids)
                    ),
                }
            # Use resource-level discovery for acquire requests (handles scaling/replacement)
            elif request.resource_ids:
                operation_type = ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES
                parameters = {
                    "resource_ids": request.resource_ids,
                    "provider_api": request.provider_api,
                    "template_id": request.template_id,
                    "requested_count": request.requested_count,
                }
            # Fallback to instance-level discovery for requests without resource tracking
            elif db_machines:
                operation_type = ProviderOperationType.GET_INSTANCE_STATUS
                instance_ids = [m.machine_id.value for m in db_machines]
                parameters = {
                    "instance_ids": instance_ids,
                    "template_id": request.template_id,
                    "machine_coordinates": load_machine_coordinates(self.uow_factory, instance_ids),
                }
            else:
                return [], {}

            parameters = {**parameters, "request": request}
            operation = ProviderOperation(
                operation_type=operation_type,
                parameters=parameters,
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )

            # Get provider configuration
            self._config_port.get_provider_instance_config(request.provider_name or "")

            # Execute operation using Provider Registry Service
            if self.provider_registry_service is None:
                raise RuntimeError("ProviderRegistryService is required for this operation")
            result = await self.provider_registry_service.execute_operation(
                request.provider_name or "", operation
            )

            if result.success and result.data:
                instances = result.data.get("instances", [])
                self.logger.debug(f"Provider returned {len(instances)} instances")

                # instances are already snake_case domain dicts from check_hosts_status
                # via _get_instance_details → machine_adapter (PascalCase→snake_case conversion
                # happens once in the infrastructure layer). No re-conversion needed here.
                domain_machines = []
                returned_ids = set()
                db_machines_by_id = {str(m.machine_id.value): m for m in db_machines}
                for instance_data in instances:
                    try:
                        processed_data = {
                            **instance_data,
                            "request_id": str(request.request_id),
                            "resource_id": request.resource_ids[0] if request.resource_ids else "",
                        }
                        terminal_states = {"shutting-down", "terminated", "stopping", "stopped"}
                        instance_status = processed_data.get("status", "")
                        existing = db_machines_by_id.get(processed_data.get("instance_id", ""))
                        if instance_status in terminal_states and existing:
                            machine = self._create_machine_with_status(
                                existing, MachineStatus(instance_status)
                            )
                        else:
                            machine = self._create_machine_from_processed_data(
                                processed_data, request
                            )
                        domain_machines.append(machine)
                        returned_ids.add(processed_data["instance_id"])
                    except Exception as e:
                        self.logger.warning(f"Failed to create machine from instance data: {e}")

                # For return requests: instances missing from AWS response have been
                # terminated and purged (~1hr window). Treat them as terminated.
                if request.request_type.value == "return":
                    queried_ids = set(parameters.get("instance_ids", []))
                    missing_ids = queried_ids - returned_ids
                    for missing_id in missing_ids:
                        self.logger.info(
                            f"Instance {missing_id} not found in AWS — treating as terminated"
                        )
                        existing = next(
                            (m for m in db_machines if m.machine_id.value == missing_id), None
                        )
                        if existing:
                            domain_machines.append(self._create_terminated_machine(existing))

                return domain_machines, result.metadata or {}
            else:
                self.logger.warning(f"Provider operation failed: {result.error_message}")
                return FailedProviderFetch(db_machines), {PROVIDER_FETCH_FAILED: True}

        except Exception as e:
            self.logger.error(f"Failed to fetch provider machines: {e}")
            return FailedProviderFetch(db_machines), {PROVIDER_FETCH_FAILED: True}

    def _stamp_status_check(self, request: Request, now: datetime) -> None:
        """Stamp first/last_status_check on the stored request.

        The request in hand was loaded before the provider call, which can take
        a long time, so the stamp is applied to a fresh read taken inside the
        write: a request that was cancelled or finished meanwhile is left alone,
        and any other change made meanwhile is kept. A concurrent write is met
        with one re-read and re-apply; the stale copy is never saved.
        """
        for attempt in range(2):
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    current = uow.requests.get_by_id(request.request_id)
                    if (
                        current is None
                        or current.status.is_terminal()
                        or current.awaiting_resources
                    ):
                        # Gone, finished, or still owned by the in-flight
                        # create with nothing polled: nothing fresher is known.
                        self.logger.debug(
                            "Not stamping status_check on request %s: gone, finished "
                            "or not yet provisioned",
                            request.request_id,
                        )
                        return
                    stamped = current.record_status_check(now=now)
                    uow.requests.save(stamped)
                self.logger.debug(
                    "Stamped status_check timestamps on request %s (first=%s)",
                    request.request_id,
                    stamped.first_status_check,
                )
                return
            except ConcurrencyError:
                if attempt == 1:
                    raise

    @staticmethod
    def _apply_provider_state(existing: Machine, provider_machine: Machine) -> Machine:
        """Overlay what the provider owns onto ``existing``.

        Only provider-owned fields (status, addresses, DNS, tags, launch and
        provider data and the like) are taken from the provider. Everything else
        on ``existing`` is kept, notably ``return_request_id`` and ``request_id``,
        which the request and return flows set.
        """
        # Create updated machine with all provider data
        machine_data = existing.model_dump()
        machine_data["status"] = provider_machine.status
        machine_data["private_ip"] = provider_machine.private_ip
        machine_data["public_ip"] = provider_machine.public_ip
        # Preserve explicitly-set name (e.g. SLURM node name);
        # only adopt provider name if existing has no name
        if not existing.name:
            machine_data["name"] = provider_machine.name
        machine_data["private_dns_name"] = provider_machine.private_dns_name
        machine_data["public_dns_name"] = provider_machine.public_dns_name
        machine_data["price_type"] = provider_machine.price_type
        machine_data["instance_type"] = provider_machine.instance_type
        # status_reason normalisation:
        #   - On reaching a terminal status (TERMINATED), in-flight
        #     reasons like "Termination in progress" are stale.
        #     Stamp a terminal "Terminated" if provider didn't
        #     supply something more specific (EC2's StateReason).
        #   - Otherwise pass through whatever provider returned.
        from orb.domain.machine.machine_status import MachineStatus as _MS

        _new_reason = provider_machine.status_reason
        if provider_machine.status == _MS.TERMINATED:
            if not _new_reason or "in progress" in (_new_reason or "").lower():
                _new_reason = "Terminated"
        machine_data["status_reason"] = _new_reason
        machine_data["provider_data"] = provider_machine.provider_data
        machine_data["launch_time"] = provider_machine.launch_time or existing.launch_time
        machine_data["subnet_id"] = provider_machine.subnet_id
        machine_data["security_group_ids"] = provider_machine.security_group_ids
        machine_data["vpc_id"] = provider_machine.vpc_id
        machine_data["version"] = existing.version + 1
        machine_data["tags"] = provider_machine.tags
        if provider_machine.resource_id and not existing.resource_id:
            machine_data["resource_id"] = provider_machine.resource_id
        # A machine recorded from a launch response may not carry its image
        # yet; adopt it once the provider reports it.
        if MachineSyncService._should_adopt_image_id(existing, provider_machine):
            machine_data["image_id"] = provider_machine.image_id

        return Machine.model_validate(machine_data)

    def _write_machine(self, provider_machine: Machine, expected: Optional[Machine]) -> Machine:
        """Persist the provider's view of one machine on top of its stored state.

        The stored machine is re-read inside the write: the copy the sync started
        from predates a provider call that can take a long time, and the return
        flow may have changed the machine since. A concurrent write is met with
        one re-read and re-apply; the stale copy is never saved.
        """
        machine_id = provider_machine.machine_id.value
        for attempt in range(2):
            try:
                with self.uow_factory.create_unit_of_work() as uow:
                    current = uow.machines.get_by_id(machine_id)
                    if current is None:
                        if expected is not None:
                            # Removed meanwhile: do not bring it back.
                            return expected
                        saved = provider_machine
                    else:
                        saved = self._apply_provider_state(current, provider_machine)
                    uow.machines.save(saved)
                return saved
            except ConcurrencyError:
                if attempt == 1:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _create_machine_from_processed_data(
        self, processed_data: dict, request: Request
    ) -> Machine:
        from datetime import datetime

        from orb.domain.base.value_objects import InstanceType, Tags
        from orb.domain.machine.machine_identifiers import MachineId
        from orb.domain.machine.machine_status import MachineStatus

        launch_time = processed_data.get("launch_time")
        if isinstance(launch_time, str):
            try:
                launch_time = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            except ValueError:
                launch_time = None

        instance_type_str = processed_data.get("instance_type")
        if not instance_type_str:
            self.logger.warning(
                "Missing instance_type in processed data for request %s, defaulting to 'unknown'",
                request.request_id,
            )
            instance_type_str = "unknown"

        return Machine(
            machine_id=MachineId(value=processed_data["instance_id"]),
            name=processed_data.get("name"),
            template_id=request.template_id,
            request_id=str(request.request_id),
            provider_type=request.provider_type,
            provider_name=request.provider_name,
            provider_api=request.provider_api,
            resource_id=processed_data.get("resource_id"),
            instance_type=InstanceType(value=instance_type_str),
            image_id=processed_data.get("image_id", "unknown"),
            price_type=processed_data.get("price_type"),
            status=MachineStatus(processed_data.get("status", "pending")),
            status_reason=processed_data.get("status_reason"),
            private_ip=processed_data.get("private_ip"),
            public_ip=processed_data.get("public_ip"),
            private_dns_name=processed_data.get("private_dns_name"),
            public_dns_name=processed_data.get("public_dns_name"),
            launch_time=launch_time,
            subnet_id=processed_data.get("subnet_id"),
            vpc_id=processed_data.get("vpc_id"),
            security_group_ids=processed_data.get("security_group_ids", []),
            tags=Tags(tags=processed_data.get("tags") or {}),
            metadata=processed_data.get("metadata", {}),
            provider_data=processed_data.get("provider_data") or {},
        )

    def _create_terminated_machine(self, existing: Machine) -> Machine:
        """Return a copy of an existing DB machine with status set to TERMINATED.

        Clears any in-flight ``status_reason`` (e.g. ``"Termination in
        progress"``) and stamps a terminal reason so the UI doesn't show
        stale progress text on a terminal machine.
        """
        from orb.domain.machine.machine_status import MachineStatus

        machine_data = existing.model_dump()
        machine_data["status"] = MachineStatus.TERMINATED
        machine_data["status_reason"] = "Terminated"
        machine_data["version"] = existing.version + 1
        return Machine.model_validate(machine_data)

    def _create_machine_with_status(self, existing: Machine, status: "MachineStatus") -> Machine:
        """Return a copy of an existing DB machine with only the status updated."""
        machine_data = existing.model_dump()
        machine_data["status"] = status
        machine_data["version"] = existing.version + 1
        return Machine.model_validate(machine_data)

    @staticmethod
    def _should_adopt_image_id(existing: Machine, provider_machine: Machine) -> bool:
        """True when the provider reports an image the stored machine does not have yet."""
        unset = (None, "", "unknown")
        return provider_machine.image_id not in unset and existing.image_id in unset

    async def sync_machines_with_provider(
        self,
        request: Request,
        db_machines: list[Machine],
        provider_machines: list[Machine],
        now: Optional[datetime] = None,
    ) -> Tuple[list[Machine], dict]:
        """Sync machines with provider state and stamp first/last_status_check on the request.

        Stamping is a deliberate write triggered by this sync cycle, not by the
        caller (which may be a query handler).  Moving the write here keeps
        query handlers read-only while ensuring every sync cycle records the
        poll timestamp regardless of which caller initiated the sync.

        Args:
            request: The request whose machines are being synced.
            db_machines: Current machines in the database.
            provider_machines: Current machines as reported by the provider.
            now: Timestamp to use for status-check stamps (defaults to UTC now).
        """
        try:
            existing_by_id = {str(m.machine_id.value): m for m in db_machines}
            updated_machines = []
            to_upsert: list[tuple[int, Machine, Optional[Machine]]] = []

            # Update existing machines and add new ones discovered from provider
            for provider_machine in provider_machines:
                machine_id = str(provider_machine.machine_id.value)
                existing = existing_by_id.get(machine_id)

                if existing:
                    # Check if machine needs update (including DNS, name, price_type,
                    # and instance_type — k8s pods synced while pending get
                    # "k8s/<api>" which must be refreshed once the pod is scheduled
                    # and the real node instance type is known).
                    needs_update = (
                        existing.status != provider_machine.status
                        or existing.private_ip != provider_machine.private_ip
                        or existing.public_ip != provider_machine.public_ip
                        or existing.private_dns_name != provider_machine.private_dns_name
                        or existing.public_dns_name != provider_machine.public_dns_name
                        or existing.price_type != provider_machine.price_type
                        or str(existing.instance_type) != str(provider_machine.instance_type)
                        or existing.tags != provider_machine.tags
                        or existing.subnet_id != provider_machine.subnet_id
                        or existing.security_group_ids != provider_machine.security_group_ids
                        or existing.vpc_id != provider_machine.vpc_id
                        or existing.status_reason != provider_machine.status_reason
                        or existing.provider_data != provider_machine.provider_data
                        or (provider_machine.resource_id and not existing.resource_id)
                        or self._should_adopt_image_id(existing, provider_machine)
                    )

                    # Debug logging
                    self.logger.info(
                        f"Machine {machine_id} sync check: existing.name='{existing.name}' vs provider.name='{provider_machine.name}', needs_update={needs_update}"
                    )

                    if needs_update:
                        to_upsert.append((len(updated_machines), provider_machine, existing))
                        updated_machines.append(existing)

                        self.logger.debug(
                            f"Updated machine {machine_id} status: {existing.status} -> {provider_machine.status}"
                        )
                    else:
                        updated_machines.append(existing)
                else:
                    # New machine discovered from provider
                    to_upsert.append((len(updated_machines), provider_machine, None))
                    updated_machines.append(provider_machine)
                    self.logger.debug(f"Added new machine {machine_id} from provider")

            # Persist changes
            if to_upsert:
                for index, provider_machine, existing in to_upsert:
                    updated_machines[index] = self._write_machine(provider_machine, existing)

                self.logger.info(f"Updated {len(to_upsert)} machines from provider sync")

            # Stamp first_status_check (once, immutable) and last_status_check
            # on every sync cycle. Mutation lives here (not in the query
            # handler) to keep queries read-only.
            if isinstance(provider_machines, FailedProviderFetch):
                # The provider was not reached: the stored state is no fresher
                # than before, so freshness must not be refreshed.
                self.logger.debug(
                    "Not stamping status_check on request %s: provider fetch failed",
                    request.request_id,
                )
                return updated_machines, {}
            try:
                self._stamp_status_check(request, now or datetime.now(timezone.utc))
            except ConcurrencyError:
                # Persistent write conflict: do not hide it as a stale-looking
                # success; the caller decides (the loop counts it as failed).
                raise
            except Exception as stamp_err:
                # Non-fatal — the sync result is still valid even if stamping fails.
                self.logger.warning(
                    "Failed to stamp status_check on request %s: %s",
                    request.request_id,
                    stamp_err,
                )

            return updated_machines, {}

        except ConcurrencyError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to sync machines with provider: {e}")
            return db_machines, {}
