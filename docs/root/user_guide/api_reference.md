# API Reference

The Open Resource Broker exposes a REST API when running in server mode. Start the server with:

```bash
orb server start --foreground --host 0.0.0.0 --port 8000
```

Requires the `api` extra: `pip install "orb-py[api]"`.

Interactive API docs are available at `http://localhost:8000/docs` (Swagger UI) and `http://localhost:8000/redoc` once the server is running.

## Base URL

```
http://<host>:<port>/api/v1
```

## Authentication

Authentication is not enforced by default in development mode. For production deployments, place ORB behind a reverse proxy or API gateway that handles authentication.

## Response Format

The configured scheduler decides the shape of every response. A call can ask for
another registered format with the `X-ORB-Scheduler` header or the `scheduler`
query parameter, with any role; an unknown name returns `400`. The response names
the format it used in the `X-ORB-Scheduler` header and varies on it. The OpenAPI
document describes the default shape of each operation and, under
`x-orb-formats`, the schema of every built-in format.

## Templates

### List Templates

Returns all available templates.

**Endpoint:** `GET /api/v1/templates/`

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `provider_api` | string | Filter by provider API type (e.g. `EC2Fleet`, `RunInstances`) |
| `force_refresh` | boolean | Force reload from configuration files (default: `false`) |

**Response (200):**
```json
{
  "templates": [
    {
      "template_id": "aws-basic",
      "provider_api": "RunInstances",
      "machine_type": "t3.medium",
      "image_id": "ami-0abcdef1234567890",
      "subnet_ids": ["subnet-aaa111"],
      "security_group_ids": ["sg-11111111"],
      "tags": {}
    }
  ],
  "total_count": 1,
  "count": 1,
  "message": "Retrieved 1 templates successfully",
  "success": true,
  "timestamp": "2026-02-23T22:00:00.000000"
}
```

**Example:**
```bash
curl http://localhost:8000/api/v1/templates/
curl "http://localhost:8000/api/v1/templates/?provider_api=EC2Fleet"
```

---

### Get Template

Returns a single template by ID.

**Endpoint:** `GET /api/v1/templates/{template_id}`

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `template_id` | string | Template identifier |

**Response (200):**
```json
{
  "template": {
    "template_id": "aws-basic",
    "provider_api": "RunInstances",
    "machine_type": "t3.medium",
    "image_id": "ami-0abcdef1234567890",
    "subnet_ids": ["subnet-aaa111"],
    "security_group_ids": ["sg-11111111"],
    "tags": {}
  },
  "timestamp": "2026-02-23T22:00:00.000000"
}
```

**Error Responses:**
- `404 Not Found` — template ID does not exist

**Example:**
```bash
curl http://localhost:8000/api/v1/templates/aws-basic
```

---

### Create Template

Creates a new template.

**Endpoint:** `POST /api/v1/templates/`

**Request Body** (JSON, accepts both `camelCase` and `snake_case`):
```json
{
  "template_id": "aws-spot",
  "provider_api": "SpotFleet",
  "machine_type": "c5.xlarge",
  "image_id": "ami-0abcdef1234567890",
  "subnet_ids": ["subnet-aaa111"],
  "security_group_ids": ["sg-11111111"],
  "key_name": "my-keypair",
  "tags": {
    "Environment": "production"
  }
}
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `template_id` | string | Yes | Unique template identifier |
| `provider_api` | string | No | Provider API type (default: `aws`) |
| `image_id` | string | No | AMI ID |
| `machine_type` | string | No | Machine / instance type (e.g. `t3.medium`). The alias `instance_type` is also accepted for backward compatibility but is deprecated. |
| `key_name` | string | No | EC2 key pair name |
| `security_group_ids` | list[string] | No | Security group IDs |
| `subnet_ids` | list[string] | No | Subnet IDs |
| `user_data` | string | No | Instance user data (base64) |
| `tags` | object | No | Key-value tags |

**Response (201):**
```json
{
  "message": "Template aws-spot created successfully",
  "templateId": "aws-spot",
  "timestamp": "2026-02-23T22:00:00.000000"
}
```

**Error Responses:**
- `400 Bad Request` — validation failed

**Example:**
```bash
curl -X POST http://localhost:8000/api/v1/templates/ \
  -H "Content-Type: application/json" \
  -d '{"template_id": "aws-spot", "provider_api": "SpotFleet", "machine_type": "c5.xlarge"}'
```

---

### Update Template

Updates an existing template.

**Endpoint:** `PUT /api/v1/templates/{template_id}`

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `template_id` | string | Template identifier |

**Request Body** (JSON, all fields optional, accepts `camelCase` or `snake_case`):
```json
{
  "machine_type": "m5.large",
  "tags": {
    "Environment": "staging"
  }
}
```

**Response (200):**
```json
{
  "message": "Template aws-basic updated successfully",
  "templateId": "aws-basic",
  "timestamp": "2026-02-23T22:00:00.000000"
}
```

**Error Responses:**
- `400 Bad Request` — validation failed

**Example:**
```bash
curl -X PUT http://localhost:8000/api/v1/templates/aws-basic \
  -H "Content-Type: application/json" \
  -d '{"machine_type": "m5.large"}'
```

---

### Delete Template

Deletes a template.

**Endpoint:** `DELETE /api/v1/templates/{template_id}`

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `template_id` | string | Template identifier |

**Response (200):**
```json
{
  "message": "Template aws-basic deleted successfully",
  "templateId": "aws-basic",
  "timestamp": "2026-02-23T22:00:00.000000"
}
```

**Example:**
```bash
curl -X DELETE http://localhost:8000/api/v1/templates/aws-basic
```

---

### Validate Template

Validates a template configuration without creating it.

**Endpoint:** `POST /api/v1/templates/validate`

**Request Body:** Any template configuration object (JSON).

**Response (200):**
```json
{
  "valid": true,
  "templateId": "aws-spot",
  "validationErrors": [],
  "validationWarnings": [],
  "timestamp": "2026-02-23T22:00:00.000000"
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/api/v1/templates/validate \
  -H "Content-Type: application/json" \
  -d '{"template_id": "aws-spot", "provider_api": "SpotFleet"}'
```

---

### Refresh Templates

Reloads templates from configuration files and clears the cache.

**Endpoint:** `POST /api/v1/templates/refresh`

**Response (200):**
```json
{
  "message": "Templates refreshed successfully. Found 20 templates.",
  "templateCount": 20,
  "cacheStats": {"refreshed": true},
  "timestamp": "2026-02-23T22:00:00.000000"
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/api/v1/templates/refresh
```

---

## Machines

### Request Machines

Provisions new machines from a template.

**Endpoint:** `POST /api/v1/machines/request`

**Request Body** (accepts `camelCase` or `snake_case`):
```json
{
  "template_id": "aws-basic",
  "machine_count": 3,
  "additional_data": {}
}
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `template_id` | string | Yes | Template to use |
| `machine_count` | integer | Yes | Number of machines to provision |
| `additional_data` | object | No | Optional extra configuration |

**Response (200):** Format depends on the configured scheduler strategy.

Default scheduler:
```json
{
  "request_id": "req-abc123",
  "template_id": "aws-basic",
  "machine_count": 3,
  "status": "pending"
}
```

HostFactory scheduler:
```json
{
  "requestId": "req-abc123",
  "templateId": "aws-basic",
  "machineCount": 3,
  "status": "running"
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/api/v1/machines/request \
  -H "Content-Type: application/json" \
  -d '{"template_id": "aws-basic", "machine_count": 2}'
```

---

### Return Machines

Returns (terminates) machines.

**Endpoint:** `POST /api/v1/machines/return`

**Request Body** (accepts `camelCase` or `snake_case`):
```json
{
  "machine_ids": [
    "machine-i-1234567890abcdef0",
    "machine-i-0987654321fedcba0"
  ]
}
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `machine_ids` | list[string] | Yes | Machine IDs to return |

**Response (200):** Scheduler-dependent format.

**Example:**
```bash
curl -X POST http://localhost:8000/api/v1/machines/return \
  -H "Content-Type: application/json" \
  -d '{"machine_ids": ["machine-i-1234567890abcdef0"]}'
```

---

### List Machines

**Endpoint:** `GET /api/v1/machines/`

**Status:** Not yet implemented. Returns `501 Not Implemented`.

Use `GET /api/v1/requests/{request_id}/status` to check provisioning status instead.

---

### Get Machine

**Endpoint:** `GET /api/v1/machines/{machine_id}`

**Status:** Not yet implemented. Returns `501 Not Implemented`.

---

## Requests

### List Requests

Returns provisioning requests with optional filtering.

**Endpoint:** `GET /api/v1/requests/`

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status (`pending`, `in_progress`, `completed`, `failed`, `cancelled`) |
| `limit` | integer | Limit number of results |

**Response (200):** Scheduler-dependent format.

**Example:**
```bash
curl http://localhost:8000/api/v1/requests/
curl "http://localhost:8000/api/v1/requests/?status=pending&limit=10"
```

---

### Get Request Status

Returns the status of a specific request.

**Endpoint:** `GET /api/v1/requests/{request_id}/status`

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `request_id` | string | Request identifier |

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `long` | boolean | Include detailed info and refresh provider state (default: `true`) |

**Response (200):** Scheduler-dependent format. A request that has a deadline carries
`deadline_at`, and one that was ever partially fulfilled carries `partial_since`
(ISO 8601); both are absent otherwise and are not part of the HostFactory format.

Default scheduler example:
```json
{
  "request_id": "req-abc123",
  "template_id": "aws-basic",
  "machine_count": 3,
  "status": "completed",
  "machines": [
    {
      "machine_id": "machine-i-1234567890abcdef0",
      "status": "running",
      "private_ip": "10.0.1.100"
    }
  ]
}
```

**Example:**
```bash
curl http://localhost:8000/api/v1/requests/req-abc123/status
curl "http://localhost:8000/api/v1/requests/req-abc123/status?long=false"
```

---

### List Return Requests

Returns the return requests ORB created.

**Endpoint:** `GET /api/v1/requests/return`

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by return request status (`pending`, `in_progress`, `complete`, `failed`, `cancelled`, `partial`, `timeout`); an unknown value returns `400` |
| `limit`, `offset`, `cursor`, `q`, `sort` | | Paging, search and ordering, as for List Requests |

**Response (200):** Scheduler-dependent format.

---

### Get Request Details

Returns full details for a request.

**Endpoint:** `GET /api/v1/requests/{request_id}`

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `request_id` | string | Request identifier |

**Response (200):** Same format as Get Request Status with `long=true`.

**Example:**
```bash
curl http://localhost:8000/api/v1/requests/req-abc123
```

---

## Providers

### List Providers

**Endpoint:** `GET /api/v1/providers/`

**Response (200):** `providers` (each with `name`, `type`, `enabled`, `status`,
`capabilities`, `weight`, `priority` and `config`), `count`, `total_count` (the
same value as `count`), `selection_policy` and `message`. An unreadable provider
configuration returns a configuration error, not an empty list.

---

## Administration

### Initialise ORB

**Endpoint:** `POST /api/v1/admin/init`

Requires the `admin` role, `allow_destructive_admin`, a non-production
environment and `"confirm": "INIT"`. It writes a configuration with one provider
built the same way as `orb init --non-interactive`, after checking the provider
credentials; nothing is written when the check fails.

| Body field | Type | Description |
|------------|------|-------------|
| `confirm` | string | Must be `INIT` |
| `provider_type` | string | Provider to configure; the first registered provider if omitted |
| `scheduler_type` | string | Scheduler type, `default` if omitted |
| `options` | object | Provider options by their `orb init` argument names, for example `{"aws_region": "eu-west-2", "aws_profile": "ops"}`. Credential sources are named, never sent as secrets |
| `force` | boolean | Replace an existing configuration |
| `generate_templates` | boolean | Refresh templates afterwards (default `true`) |

Returns `400` for a missing confirmation, rejected provider input or credentials
that could not be verified.

---

## Error Responses

All endpoints return a consistent error format on failure:

```json
{
  "detail": "Template aws-missing not found"
}
```

| Status Code | Meaning |
|-------------|---------|
| `400` | Bad request — invalid input or validation failure |
| `404` | Not found — resource does not exist |
| `500` | Internal server error |
| `501` | Not implemented — endpoint is planned but not yet available |

---

## Starting the Server

```bash
# Development (with auto-reload)
orb server start --foreground --reload --port 8000

# Production
orb server start --foreground --host 0.0.0.0 --port 8000 --workers 4

# Custom log level
orb server start --foreground --server-log-level warning
```

## Related

- [CLI Reference](../cli/cli-reference.md) — CLI commands including `orb server start --foreground`
- [Templates](templates.md) — template configuration
- [Requests](requests.md) — request management via CLI
