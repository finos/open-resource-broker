expected_get_available_templates_schema_hostfactory = {
    "type": "object",
    "required": ["message", "templates"],
    "properties": {
        "message": {"type": "string"},
        "success": {"type": "boolean"},
        "total_count": {"type": "integer"},
        "templates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "templateId",
                    "maxNumber",
                    "attributes",
                ],
                "properties": {
                    "templateId": {"type": "string"},
                    "maxNumber": {"type": "integer", "minimum": 1},
                    "pgrpName": {"type": ["string", "null"]},
                    "onDemandCapacity": {"type": "integer"},
                    "vmTypes": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "instanceTags": {"type": "string"},
                    "attributes": {
                        "type": "object",
                        "required": ["type", "ncores", "ncpus", "nram"],
                        "properties": {
                            "type": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "ncores": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "ncpus": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "nram": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

expected_request_machines_schema_hostfactory = {
    "type": "object",
    "required": ["requestId", "message"],
    "properties": {
        "requestId": {
            "type": "string",
            "pattern": "^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        },
        "message": {"type": "string", "pattern": "^.*(succeeded|success).*$"},
    },
    "additionalProperties": False,
}

expected_request_status_schema_hostfactory = {
    "type": "object",
    "required": ["requests"],
    "properties": {
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["request_id", "message", "status", "machines"],
                "properties": {
                    "request_id": {
                        "type": "string",
                        "pattern": "^(req-|ret-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                    },
                    "message": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["running", "complete", "complete_with_error", "in_progress"],
                    },
                    "machines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "machineId",
                                "name",
                                "result",
                                "status",
                                "privateIpAddress",
                                "launchtime",
                                "message",
                            ],
                            "properties": {
                                "machineId": {"type": "string", "pattern": "^i-[0-9a-f]+$"},
                                "name": {"type": "string"},
                                "priceType": {"type": "string", "enum": ["ondemand", "spot"]},
                                "instanceType": {"type": "string"},
                                "result": {
                                    "type": "string",
                                    "enum": ["executing", "succeed", "fail"],
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "running", "terminated", "failed", "error"],
                                },
                                "privateIpAddress": {
                                    "type": ["string", "null"],
                                    "pattern": "^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}$",
                                },
                                "instanceTags": {"type": "string"},
                                "cloudHostId": {"type": "null"},
                                "launchtime": {"type": ["integer", "string"]},
                                "message": {"type": "string"},
                            },
                            "additionalProperties": True,
                        },
                    },
                },
                "additionalProperties": True,
            },
        },
        "message": {"type": "string"},
        "count": {"type": "integer"},
    },
    "additionalProperties": True,
}


# Default scheduler schemas - machine-focused format
expected_get_available_templates_schema_default = {
    "type": "object",
    "required": ["templates", "total_count"],
    "properties": {
        "templates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["template_id", "max_capacity", "instance_type"],
                "properties": {
                    "template_id": {"type": "string"},
                    "max_capacity": {"type": "integer", "minimum": 1},
                    "instance_type": {"type": "string"},
                    "vcpus": {"type": "integer"},
                    "memory_mb": {"type": "integer"},
                    "provider_api": {"type": "string"},
                    "price_type": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "total_count": {"type": "integer"},
        "provider": {"type": "string"},
    },
    "additionalProperties": True,
}

expected_request_machines_schema_default = {
    "type": "object",
    "required": ["request_id", "message"],
    "properties": {
        "request_id": {
            "type": "string",
            "pattern": "^req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        },
        "message": {"type": "string", "pattern": "^.*(succeeded|success).*$"},
        "status": {"type": "string"},
    },
    "additionalProperties": True,
}

expected_request_status_schema_default = {
    "type": "object",
    "required": ["requests"],
    "properties": {
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["request_id", "message", "status", "machines"],
                "properties": {
                    "request_id": {
                        "type": "string",
                        "pattern": "^(req-|ret-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                    },
                    "message": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["running", "complete", "complete_with_error", "in_progress"],
                    },
                    "machines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "machine_id",
                                "name",
                                "result",
                                "status",
                                "private_ip_address",
                                "launch_time",
                                "message",
                            ],
                            "properties": {
                                "machine_id": {"type": "string", "pattern": "^i-[0-9a-f]+$"},
                                "name": {"type": "string"},
                                "price_type": {"type": "string", "enum": ["ondemand", "spot"]},
                                "instance_type": {"type": "string"},
                                "result": {
                                    "type": "string",
                                    "enum": ["executing", "succeed", "fail"],
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "running", "terminated", "failed", "error"],
                                },
                                "private_ip_address": {
                                    "type": ["string", "null"],
                                    "pattern": "^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}$",
                                },
                                "instance_tags": {"type": "string"},
                                "cloud_host_id": {"type": "null"},
                                "launch_time": {"type": ["integer", "string"]},
                                "message": {"type": "string"},
                            },
                            "additionalProperties": True,
                        },
                    },
                },
                "additionalProperties": True,
            },
        },
        "message": {"type": "string"},
        "count": {"type": "integer"},
    },
    "additionalProperties": True,
}


# Backward compatibility aliases (default to hostfactory for existing code)
expected_get_available_templates_schema = expected_get_available_templates_schema_hostfactory
expected_request_machines_schema = expected_request_machines_schema_hostfactory
expected_request_status_schema = expected_request_status_schema_hostfactory


def get_schema_for_scheduler(operation: str, scheduler_type: str) -> dict:
    """
    Get appropriate schema based on scheduler type.

    Args:
        operation: API operation name (e.g., "get_available_templates", "request_machines", "request_status")
        scheduler_type: "default" or "hostfactory"

    Returns:
        JSON schema dictionary for validation

    Raises:
        ValueError: If operation or scheduler_type is invalid
    """
    # Validate scheduler type
    if scheduler_type not in ["default", "hostfactory"]:
        raise ValueError(
            f"Invalid scheduler_type: {scheduler_type}. Must be 'default' or 'hostfactory'"
        )

    # Schema mapping
    schema_map = {
        "get_available_templates": {
            "default": expected_get_available_templates_schema_default,
            "hostfactory": expected_get_available_templates_schema_hostfactory,
        },
        "request_machines": {
            "default": expected_request_machines_schema_default,
            "hostfactory": expected_request_machines_schema_hostfactory,
        },
        "request_status": {
            "default": expected_request_status_schema_default,
            "hostfactory": expected_request_status_schema_hostfactory,
        },
    }

    # Get schema for operation
    if operation not in schema_map:
        raise ValueError(
            f"Invalid operation: {operation}. Must be one of {list(schema_map.keys())}"
        )

    return schema_map[operation][scheduler_type]
