"""API examples for OpenAPI documentation."""

from typing import Any


def get_api_examples() -> dict[str, Any]:
    """
    Get comprehensive API examples for documentation.

    Returns:
        Dictionary of API examples organized by category
    """
    return {
        # Template Examples
        "template_list_response": {
            "summary": "Template List Response",
            "description": "Example response from GET /api/v1/templates",
            "value": {
                "success": True,
                "data": {
                    "templates": [
                        {
                            "templateId": "basic-template",
                            "templateName": "Basic Ubuntu Template",
                            "description": "Standard Ubuntu 22.04 template",
                            "provider_api": "aws",
                            "imageId": "ami-0c02fb55956c7d316",
                            "instanceType": "t3.medium",
                            "keyName": "my-key-pair",
                            "securityGroupIds": ["sg-12345678"],
                            "subnetId": "subnet-12345678",
                            "userData": "#!/bin/bash\necho 'Hello World'",
                            "tags": {
                                "Environment": "development",
                                "Project": "hostfactory",
                            },
                        }
                    ],
                    "total": 1,
                    "count": 1,
                },
                "message": "Templates retrieved successfully",
            },
        },
        "template_detail_response": {
            "summary": "Template Detail Response",
            "description": "Example response from GET /api/v1/templates/{templateId}",
            "value": {
                "success": True,
                "data": {
                    "template": {
                        "templateId": "basic-template",
                        "templateName": "Basic Ubuntu Template",
                        "description": "Standard Ubuntu 22.04 template with development tools",
                        "provider_api": "aws",
                        "imageId": "ami-0c02fb55956c7d316",
                        "instanceType": "t3.medium",
                        "keyName": "my-key-pair",
                        "securityGroupIds": ["sg-12345678"],
                        "subnetId": "subnet-12345678",
                        "userData": "#!/bin/bash\napt-get update\napt-get install -y docker.io",
                        "tags": {
                            "Environment": "development",
                            "Project": "hostfactory",
                            "Owner": "devops-team",
                        },
                        "created_at": "2025-01-07T10:00:00Z",
                        "updated_at": "2025-01-07T10:00:00Z",
                    }
                },
                "message": "Template retrieved successfully",
            },
        },
        # Machine Request Examples
        "machine_request": {
            "summary": "Machine Request",
            "description": "Example request body for POST /api/v1/machines",
            "value": {
                "templateId": "basic-template",
                "machineCount": 3,
                "additionalData": {
                    "subnetId": "subnet-87654321",
                    "tags": {
                        "RequestedBy": "user@company.com",
                        "Purpose": "load-testing",
                    },
                },
            },
        },
        "machine_request_response": {
            "summary": "Machine Request Response",
            "description": "Example response from POST /api/v1/machines",
            "value": {
                "success": True,
                "data": {
                    "requestId": "req-12345678-1234-1234-1234-123456789012",
                    "status": "pending",
                    "templateId": "basic-template",
                    "machineCount": 3,
                    "requestedAt": "2025-01-07T10:30:00Z",
                    "estimatedCompletionTime": "2025-01-07T10:35:00Z",
                },
                "message": "Machine request submitted successfully",
            },
        },
        # Request Status Examples
        "request_status_response": {
            "summary": "Request Status Response",
            "description": "Example response from GET /api/v1/requests/{requestId}/status",
            "value": {
                "success": True,
                "data": {
                    "requestId": "req-12345678-1234-1234-1234-123456789012",
                    "status": "completed",
                    "templateId": "basic-template",
                    "machineCount": 3,
                    "completedCount": 3,
                    "failedCount": 0,
                    "requestedAt": "2025-01-07T10:30:00Z",
                    "completedAt": "2025-01-07T10:34:23Z",
                    "machines": [
                        {
                            "machineId": "i-1234567890abcdef0",
                            "status": "running",
                            "instanceType": "t3.medium",
                            "privateIpAddress": "10.0.1.100",
                            "publicIpAddress": "54.123.45.67",
                            "launchedAt": "2025-01-07T10:32:15Z",
                        },
                        {
                            "machineId": "i-0987654321fedcba0",
                            "status": "running",
                            "instanceType": "t3.medium",
                            "privateIpAddress": "10.0.1.101",
                            "publicIpAddress": "54.123.45.68",
                            "launchedAt": "2025-01-07T10:32:18Z",
                        },
                        {
                            "machineId": "i-abcdef1234567890",
                            "status": "running",
                            "instanceType": "t3.medium",
                            "privateIpAddress": "10.0.1.102",
                            "publicIpAddress": "54.123.45.69",
                            "launchedAt": "2025-01-07T10:32:21Z",
                        },
                    ],
                },
                "message": "Request status retrieved successfully",
            },
        },
        # Machine Return Examples
        "machine_return_request": {
            "summary": "Machine Return Request",
            "description": "Example request body for POST /api/v1/machines/return",
            "value": {
                "machineIds": ["i-1234567890abcdef0", "i-0987654321fedcba0"],
                "reason": "Load testing completed",
            },
        },
        "machine_return_response": {
            "summary": "Machine Return Response",
            "description": "Example response from POST /api/v1/machines/return",
            "value": {
                "success": True,
                "data": {
                    "returnRequestId": "ret-87654321-4321-4321-4321-210987654321",
                    "machineIds": ["i-1234567890abcdef0", "i-0987654321fedcba0"],
                    "status": "pending",
                    "requestedAt": "2025-01-07T11:00:00Z",
                    "estimatedCompletionTime": "2025-01-07T11:02:00Z",
                },
                "message": "Machine return request submitted successfully",
            },
        },
        # Error Examples
        "error_400": {
            "summary": "Bad Request Error",
            "description": "Example 400 Bad Request error response",
            "value": {
                "success": False,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Invalid request parameters",
                    "details": {
                        "field": "machineCount",
                        "issue": "Machine count must be between 1 and 100",
                    },
                },
                "timestamp": "2025-01-07T10:30:00Z",
                "requestId": "req-error-12345",
            },
        },
        "error_401": {
            "summary": "Unauthorized Error",
            "description": "Example 401 Unauthorized error response",
            "value": {
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Authentication required",
                    "details": {"reason": "Missing or invalid authorization header"},
                },
                "timestamp": "2025-01-07T10:30:00Z",
                "requestId": "req-error-12346",
            },
        },
        "error_403": {
            "summary": "Forbidden Error",
            "description": "Example 403 Forbidden error response",
            "value": {
                "success": False,
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Insufficient permissions",
                    "details": {
                        "required_permission": "hostfactory:request_machines",
                        "user_permissions": ["hostfactory:list_templates"],
                    },
                },
                "timestamp": "2025-01-07T10:30:00Z",
                "requestId": "req-error-12347",
            },
        },
        "error_404": {
            "summary": "Not Found Error",
            "description": "Example 404 Not Found error response",
            "value": {
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Resource not found",
                    "details": {
                        "resource": "template",
                        "identifier": "non-existent-template",
                    },
                },
                "timestamp": "2025-01-07T10:30:00Z",
                "requestId": "req-error-12348",
            },
        },
        "error_500": {
            "summary": "Internal Server Error",
            "description": "Example 500 Internal Server Error response",
            "value": {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal server error occurred",
                    "details": {"error_id": "err-12345678-90ab-cdef-1234-567890abcdef"},
                },
                "timestamp": "2025-01-07T10:30:00Z",
                "requestId": "req-error-12349",
            },
        },
    }
