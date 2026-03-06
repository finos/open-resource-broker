"""HostFactory response formatting — converts domain objects to HF wire format."""

import json
from typing import Any

from orb.application.dto.responses import MachineDTO
from orb.application.request.dto import RequestDTO
from orb.infrastructure.template.dtos import TemplateDTO


class HostFactoryResponseFormatter:
    """
    Formats domain objects and DTOs into HostFactory JSON wire format.

    Encapsulates all status mapping, machine formatting, and response
    construction logic so the strategy class stays focused on template
    loading and field mapping.
    """

    def format_request_response(self, request_data: Any, unwrap_id_fn, coerce_fn) -> dict[str, Any]:
        """Format request creation response to HostFactory format."""
        request_dict = coerce_fn(request_data)

        if "requests" in request_dict:
            return {
                "requests": request_dict.get("requests", []),
                "status": request_dict.get("status", "complete"),
                "message": request_dict.get("message", "Status retrieved successfully"),
            }

        raw_id = request_dict.get("request_id", request_dict.get("requestId"))
        request_id = unwrap_id_fn(raw_id)
        status = request_dict.get("status", "pending")
        error_message = request_dict.get("status_message")

        if status == "failed":
            return {
                "requestId": request_id,
                "message": f"Request failed: {error_message or 'Unknown error'}",
            }
        elif status == "cancelled":
            return {"requestId": request_id, "message": "Request cancelled"}
        elif status == "timeout":
            return {"requestId": request_id, "message": "Request timed out"}
        elif status == "partial":
            return {
                "requestId": request_id,
                "message": f"Request partially completed: {error_message or 'Some resources failed'}",
            }
        elif status == "complete":
            return {"requestId": request_id, "message": "Request completed successfully"}
        elif status == "in_progress":
            return {"requestId": request_id, "message": "Request in progress"}
        elif status == "pending":
            return {"requestId": request_id, "message": "Request submitted for processing"}
        else:
            return {
                "requestId": request_id,
                "message": request_dict.get("message", "Request status unknown"),
            }

    def format_get_request_status(
        self,
        data: Any,
        format_machines_fn,
        map_status_fn,
        generate_message_fn,
    ) -> dict[str, Any]:
        """Format getRequestStatus response."""
        if hasattr(data, "request_id"):
            dto_dict = data.to_dict()
            machines_data = dto_dict.get("machines", [])
            machines = format_machines_fn(machines_data, request_type=dto_dict.get("request_type"))
            status = map_status_fn(data.status)
            message = generate_message_fn(data.status, len(machines))
            return {
                "requests": [
                    {
                        "requestId": data.request_id,
                        "status": status,
                        "message": message,
                        "machines": machines,
                    }
                ]
            }
        elif isinstance(data, dict):
            machines = format_machines_fn(
                data.get("machines", []), request_type=data.get("request_type")
            )
            status = map_status_fn(data.get("status", "unknown"))
            message = generate_message_fn(data.get("status", "unknown"), len(machines))
            return {
                "requests": [
                    {
                        "requestId": data.get("request_id", data.get("requestId", "")),
                        "status": status,
                        "message": message,
                        "machines": machines,
                    }
                ]
            }
        return {"requests": [], "message": "Request not found."}

    def format_templates_response(
        self, templates: list[TemplateDTO], format_template_fn, build_attributes_fn
    ) -> dict[str, Any]:
        """Format TemplateDTOs to HostFactory getAvailableTemplates response."""
        formatted_templates = []
        for template in templates:
            formatted_template = format_template_fn(template)

            if "templateId" not in formatted_template and "template_id" in formatted_template:
                formatted_template["templateId"] = formatted_template["template_id"]
            if "maxNumber" not in formatted_template and "max_instances" in formatted_template:
                formatted_template["maxNumber"] = formatted_template["max_instances"]

            if "instanceTags" in formatted_template:
                tags = formatted_template["instanceTags"]
                if isinstance(tags, dict):
                    formatted_template["instanceTags"] = json.dumps(tags)
                elif tags is None:
                    del formatted_template["instanceTags"]

            if "attributes" not in formatted_template:
                instance_type = (
                    formatted_template.get("vmType")
                    or formatted_template.get("instance_type")
                    or "t2.micro"
                )
                formatted_template["attributes"] = build_attributes_fn(instance_type)

            formatted_templates.append(formatted_template)

        return {
            "templates": formatted_templates,
            "message": f"Retrieved {len(formatted_templates)} templates successfully",
            "success": True,
            "total_count": len(formatted_templates),
        }

    def format_request_status_response(
        self, requests: list[RequestDTO], format_machines_fn, map_status_fn
    ) -> dict[str, Any]:
        """Format RequestDTOs to HostFactory request status response."""
        formatted_requests = []
        for request_dto in requests:
            req_dict = request_dto.to_dict()

            if "machine_references" in req_dict:
                req_dict["machines"] = req_dict.pop("machine_references")

            machines = []
            if "machines" in req_dict:
                machines = format_machines_fn(
                    req_dict["machines"], request_type=req_dict.get("request_type")
                )

            hf_request: dict[str, Any] = {
                "requestId": req_dict.get("request_id"),
                "status": map_status_fn(req_dict.get("status") or "pending"),
                "message": req_dict.get("message", ""),
                "machines": machines,
            }

            if req_dict.get("provider_name"):
                hf_request["providerName"] = req_dict["provider_name"]
            if req_dict.get("provider_type"):
                hf_request["providerType"] = req_dict["provider_type"]
            if req_dict.get("provider_api"):
                hf_request["providerApi"] = req_dict["provider_api"]

            formatted_requests.append(hf_request)

        return {"requests": formatted_requests}

    def format_machine_status_response(self, machines: list[MachineDTO]) -> dict[str, Any]:
        """Format MachineDTOs to HostFactory machine response."""
        return {
            "machines": [
                {
                    "instanceId": str(machine.machine_id),
                    "templateId": str(machine.template_id),
                    "requestId": str(machine.request_id),
                    "vmType": str(machine.instance_type),
                    "imageId": str(machine.image_id),
                    "privateIp": machine.private_ip,
                    "publicIp": machine.public_ip,
                    "subnetId": machine.subnet_id,
                    "securityGroupIds": machine.security_group_ids,
                    "status": str(machine.status),
                    "statusReason": machine.status_reason,
                    "launchTime": machine.launch_time,
                    "terminationTime": machine.termination_time,
                    "tags": machine.tags,
                }
                for machine in machines
            ]
        }

    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details with HostFactory-specific fields."""
        return {
            "id": machine_data.get("id"),
            "name": machine_data.get("name"),
            "status": machine_data.get("status"),
            "provider": "hostfactory",
            "instance_type": machine_data.get("instance_type"),
            "region": machine_data.get("region"),
            "instanceId": machine_data.get("id"),
            "vmType": machine_data.get("instance_type"),
            "imageId": machine_data.get("image_id"),
            "privateIp": machine_data.get("private_ip"),
            "publicIp": machine_data.get("public_ip"),
            "subnetId": machine_data.get("subnet_id"),
            "securityGroupIds": machine_data.get("security_group_ids"),
            "statusReason": machine_data.get("status_reason"),
            "launchTime": machine_data.get("launch_time"),
            "terminationTime": machine_data.get("termination_time"),
            "tags": machine_data.get("tags"),
        }

    def format_machines_for_hostfactory(
        self, machines: list[dict[str, Any]], request_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Format machine data to exact HostFactory format per hf_docs/input-output.md."""
        formatted_machines = []

        for machine in machines:
            result = self.map_machine_status_to_result(
                machine.get("status"), request_type=request_type
            )

            if result == "fail":
                message = (
                    machine.get("status_reason")
                    or machine.get("error")
                    or machine.get("message")
                    or ""
                )
            else:
                message = machine.get("message", "")

            launchtime = int(machine.get("launch_time_timestamp", 0))
            raw_ip = machine.get("private_ip_address", machine.get("private_ip"))
            private_ip = raw_ip if raw_ip else None

            formatted_machine: dict[str, Any] = {
                "machineId": machine.get("machine_id", machine.get("instance_id")),
                "name": machine.get(
                    "name", machine.get("instance_id", machine.get("private_ip", ""))
                ),
                "result": result,
                "status": machine.get("status", "unknown"),
                "privateIpAddress": private_ip,
                "launchtime": launchtime,
                "message": message,
                "cloudHostId": machine.get("cloud_host_id") or None,
            }

            formatted_machine["publicIpAddress"] = (
                machine.get("public_ip_address") or machine.get("public_ip") or None
            )
            if machine.get("instance_type"):
                formatted_machine["instanceType"] = machine["instance_type"]
            if machine.get("price_type"):
                formatted_machine["priceType"] = machine["price_type"]
            if machine.get("instance_tags"):
                tags = machine["instance_tags"]
                formatted_machine["instanceTags"] = (
                    json.dumps(tags) if isinstance(tags, dict) else str(tags)
                )

            formatted_machines.append(formatted_machine)

        return formatted_machines

    def map_machine_status_to_result(
        self, status: str | None, request_type: str | None = None
    ) -> str:
        """Map machine status to HostFactory result field per hf_docs/input-output.md."""
        if request_type == "return":
            if status in ["terminated", "stopped"]:
                return "succeed"
            elif status in ["shutting-down", "stopping", "pending", "terminating"]:
                return "executing"
            else:
                return "fail"
        elif status == "running":
            return "succeed"
        elif status in ["pending", "launching"]:
            return "executing"
        elif status in ["terminated", "failed", "error"]:
            return "fail"
        else:
            return "executing"

    def map_domain_status_to_hostfactory(self, domain_status: str) -> str:
        """Map domain status to HostFactory status per hf_docs/input-output.md."""
        status_mapping = {
            "pending": "running",
            "in_progress": "running",
            "provisioning": "running",
            "complete": "complete",
            "completed": "complete",
            "partial": "complete_with_error",
            "failed": "complete_with_error",
            "cancelled": "complete_with_error",
            "timeout": "complete_with_error",
            "error": "complete_with_error",
        }
        return status_mapping.get(domain_status.lower(), "running")

    def generate_status_message(self, status: str, machine_count: int) -> str:
        """Generate appropriate status message for a request status."""
        if status == "completed":
            return ""
        elif status == "partial":
            return f"Partially fulfilled: {machine_count} instances created"
        elif status == "failed":
            return "Failed to create instances"
        elif status in ["pending", "in_progress", "provisioning"]:
            return ""
        else:
            return ""
