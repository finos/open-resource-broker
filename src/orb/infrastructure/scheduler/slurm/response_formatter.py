"""SLURM response formatting — converts domain objects to SLURM wire format."""

from typing import Any

from orb.infrastructure.scheduler.slurm.field_mapper import SlurmFieldMapper


class SlurmResponseFormatter:
    """Formats domain objects into SLURM scheduler response format (snake_case)."""

    def __init__(self) -> None:
        self._field_mapper = SlurmFieldMapper()

    @staticmethod
    def _to_dict(obj: Any) -> dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return {}

    def format_templates_response(self, templates: list[Any]) -> dict[str, Any]:
        """Format TemplateDTO list into SLURM response."""
        formatted = []
        for t in templates:
            d = self._to_dict(t)
            entry: dict[str, Any] = {
                "template_id": d.get("template_id"),
                "max_instances": d.get("max_instances"),
                "partition_name": d.get("template_id"),
                "node_list": d.get("node_list"),
                "is_active": d.get("is_active", True),
            }
            # Build attributes from machine_types if available
            machine_types = d.get("machine_types", {})
            instance_type = (
                next(iter(machine_types), None) if machine_types else d.get("instance_type")
            )
            if instance_type:
                entry["attributes"] = {
                    "type": instance_type,
                    "ncpus": d.get("vcpus"),
                    "nram": d.get("memory_mb"),
                }
            formatted.append(entry)

        return {
            "templates": formatted,
            "message": "Templates retrieved successfully",
            "count": len(formatted),
        }

    def format_request_status_response(self, requests: list[Any]) -> dict[str, Any]:
        """Format RequestDTO list into SLURM response."""
        formatted = []
        for r in requests:
            d = self._to_dict(r)
            formatted.append(
                {
                    "request_id": d.get("request_id"),
                    "status": d.get("status"),
                    "machines": d.get("machines", []),
                    "message": d.get("message", ""),
                }
            )

        return {
            "requests": formatted,
            "message": "Request status retrieved successfully",
            "count": len(formatted),
        }

    def format_machine_status_response(self, machines: list[Any]) -> dict[str, Any]:
        """Format MachineDTO list into SLURM response."""
        formatted = []
        for m in machines:
            d = self._to_dict(m)
            formatted.append(
                {
                    "machine_id": d.get("machine_id"),
                    "node_name": d.get("name") or d.get("node_name"),
                    "status": d.get("status"),
                    "instance_type": d.get("instance_type"),
                    "private_ip_address": d.get("private_ip_address"),
                    "result": d.get("result"),
                }
            )

        return {"machines": formatted, "count": len(formatted)}

    def format_request_response(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Format single request creation response."""
        return {
            "request_id": request_data.get("request_id"),
            "message": request_data.get("message", "Request submitted"),
            "status": request_data.get("status", "pending"),
        }

    def format_machine_details_response(self, machine_data: dict[str, Any]) -> dict[str, Any]:
        """Format single machine details for CLI display."""
        return {
            "machine_id": machine_data.get("machine_id"),
            "node_name": machine_data.get("name") or machine_data.get("node_name"),
            "status": machine_data.get("status"),
            "instance_type": machine_data.get("instance_type"),
            "private_ip_address": machine_data.get("private_ip_address"),
            "launch_time": machine_data.get("launch_time"),
            "partition": machine_data.get("template_id") or machine_data.get("partition"),
        }

    def format_return_requests_response(self, requests: list[Any]) -> dict[str, Any]:
        """Format return-request items for SLURM."""
        formatted = []
        for r in requests:
            d = self._to_dict(r)
            formatted.append(
                {
                    "request_id": d.get("request_id"),
                    "status": d.get("status"),
                    "message": d.get("message"),
                    "grace_period": d.get("grace_period"),
                    "machines": [
                        {
                            "machine_id": self._to_dict(m).get("machine_id"),
                            "node_name": self._to_dict(m).get("name"),
                        }
                        for m in (d.get("machines") or d.get("machine_references") or [])
                    ],
                }
            )

        return {"return_requests": formatted}
