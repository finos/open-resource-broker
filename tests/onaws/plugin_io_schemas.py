expected_get_available_templates_schema = {
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

expected_request_machines_schema = {
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

expected_request_status_schema = {
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
