# Multi-Provider API Reference

## Overview

This document describes the REST API endpoints and CLI commands for multi-provider functionality in the Open Host Factory Plugin.

## REST API Endpoints

### Provider Management

#### List Available Providers
```http
GET /api/v1/providers
```

**Response:**
```json
{
  "providers": [
    {
      "name": "aws-us-east-1",
      "type": "aws",
      "enabled": true,
      "priority": 1,
      "weight": 10,
      "capabilities": ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"],
      "status": "healthy"
    },
    {
      "name": "aws-us-west-2",
      "type": "aws",
      "enabled": true,
      "priority": 2,
      "weight": 5,
      "capabilities": ["EC2Fleet", "RunInstances"],
      "status": "healthy"
    }
  ],
  "total": 2,
  "enabled": 2
}
```

#### Get Provider Details
```http
GET /api/v1/providers/{provider-instance}
```

**Response:**
```json
{
  "name": "aws-us-east-1",
  "type": "aws",
  "enabled": true,
  "priority": 1,
  "weight": 10,
  "capabilities": ["EC2Fleet", "SpotFleet", "RunInstances", "ASG"],
  "status": "healthy",
  "configuration": {
    "region": "us-east-1",
    "profile": "default"
  },
  "limits": {
    "max_instances_per_request": 1000,
    "max_concurrent_requests": 50
  },
  "statistics": {
    "total_requests": 1250,
    "successful_requests": 1200,
    "failed_requests": 50,
    "average_response_time_ms": 2500
  }
}
```

#### Get Provider Capabilities
```http
GET /api/v1/providers/{provider-instance}/capabilities
```

**Response:**
```json
{
  "provider_instance": "aws-us-east-1",
  "supported_apis": [
    {
      "name": "EC2Fleet",
      "supports_spot": true,
      "supports_on_demand": true,
      "max_instances": 1000,
      "supported_fleet_types": ["instant", "request", "maintain"]
    },
    {
      "name": "SpotFleet",
      "supports_spot": true,
      "supports_on_demand": false,
      "max_instances": 1000,
      "supported_fleet_types": ["request", "maintain"]
    },
    {
      "name": "RunInstances",
      "supports_spot": false,
      "supports_on_demand": true,
      "max_instances": 20,
      "supported_fleet_types": []
    }
  ],
  "pricing_models": ["ondemand", "spot"],
  "regions": ["us-east-1"],
  "availability_zones": ["us-east-1a", "us-east-1b", "us-east-1c"]
}
```

#### Validate Provider Configuration
```http
POST /api/v1/providers/validate
```

**Request:**
```json
{
  "providers": [
    {
      "name": "aws-test",
      "type": "aws",
      "enabled": true,
      "priority": 1,
      "weight": 10,
      "capabilities": ["EC2Fleet"]
    }
  ]
}
```

**Response:**
```json
{
  "valid": true,
  "providers": [
    {
      "name": "aws-test",
      "valid": true,
      "errors": [],
      "warnings": []
    }
  ]
}
```

### Template Management

#### List Templates with Provider Filtering
```http
GET /api/v1/templates?provider_type=aws
GET /api/v1/templates?provider_name=aws-us-east-1
GET /api/v1/templates?provider_api=EC2Fleet
```

**Response:**
```json
{
  "templates": [
    {
      "template_id": "web-server-aws",
      "provider_type": "aws",
      "provider_name": "aws-us-east-1",
      "provider_api": "EC2Fleet",
      "image_id": "ami-12345",
      "subnet_ids": ["subnet-123"],
      "max_instances": 10,
      "source_info": {
        "source_file": "config/aws-us-east-1_templates.json",
        "file_type": "provider_instance",
        "priority": 0
      }
    }
  ],
  "total": 1,
  "filters": {
    "provider_type": "aws"
  }
}
```

#### Get Template with Source Information
```http
GET /api/v1/templates/{template-id}?include_source=true
```

**Response:**
```json
{
  "template_id": "web-server",
  "provider_type": "aws",
  "provider_name": "aws-us-east-1",
  "provider_api": "EC2Fleet",
  "image_id": "ami-optimized",
  "subnet_ids": ["subnet-123"],
  "max_instances": 10,
  "source_info": {
    "source_file": "config/aws-us-east-1_templates.json",
    "file_type": "provider_instance",
    "priority": 0,
    "override_chain": [
      {
        "file": "config/templates.json",
        "fields": ["template_id", "image_id", "subnet_ids"]
      },
      {
        "file": "config/awsprov_templates.json",
        "fields": ["provider_type", "provider_api", "max_instances"]
      },
      {
        "file": "config/aws-us-east-1_templates.json",
        "fields": ["provider_name", "image_id"]
      }
    ]
  }
}
```

#### Validate Template Against Provider
```http
POST /api/v1/templates/{template-id}/validate
```

**Request:**
```json
{
  "provider_instance": "aws-us-east-1",
  "validation_level": "strict"
}
```

**Response:**
```json
{
  "valid": true,
  "provider_instance": "aws-us-east-1",
  "validation_level": "strict",
  "errors": [],
  "warnings": [],
  "supported_features": [
    "API: EC2Fleet",
    "Pricing: On-demand instances",
    "Instance count: 10 (within limit)",
    "Fleet type: instant"
  ],
  "unsupported_features": []
}
```

#### Validate Template Against Multiple Providers
```http
POST /api/v1/templates/{template-id}/validate-multi
```

**Request:**
```json
{
  "provider_instances": ["aws-us-east-1", "aws-us-west-2", "azure-east-us"],
  "validation_level": "lenient"
}
```

**Response:**
```json
{
  "template_id": "web-server",
  "validation_results": {
    "aws-us-east-1": {
      "valid": true,
      "errors": [],
      "warnings": [],
      "supported_features": ["API: EC2Fleet", "Pricing: On-demand"]
    },
    "aws-us-west-2": {
      "valid": false,
      "errors": ["Provider does not support API 'EC2Fleet'"],
      "warnings": [],
      "supported_features": ["Pricing: On-demand"]
    },
    "azure-east-us": {
      "valid": false,
      "errors": ["Provider type mismatch: expected 'azure', got 'aws'"],
      "warnings": [],
      "supported_features": []
    }
  },
  "compatible_providers": ["aws-us-east-1"],
  "incompatible_providers": ["aws-us-west-2", "azure-east-us"]
}
```

### Request Processing

#### Create Request with Provider Selection
```http
POST /api/v1/requests
```

**Request:**
```json
{
  "templateId": "web-server",
  "maxNumber": 5,
  "providerPreference": {
    "type": "explicit",
    "provider_instance": "aws-us-east-1"
  }
}
```

**Alternative Request (Load Balanced):**
```json
{
  "templateId": "web-server",
  "maxNumber": 5,
  "providerPreference": {
    "type": "load_balanced",
    "provider_type": "aws"
  }
}
```

**Response:**
```json
{
  "request_id": "req-12345",
  "template_id": "web-server",
  "requested_count": 5,
  "provider_selection": {
    "provider_type": "aws",
    "provider_instance": "aws-us-east-1",
    "selection_reason": "Explicitly specified in request",
    "confidence": 1.0,
    "alternatives": []
  },
  "status": "pending",
  "created_at": "2024-01-15T10:30:00Z"
}
```

#### Get Request with Provider Information
```http
GET /api/v1/requests/{request-id}
```

**Response:**
```json
{
  "request_id": "req-12345",
  "template_id": "web-server",
  "requested_count": 5,
  "provider_type": "aws",
  "provider_instance": "aws-us-east-1",
  "status": "completed",
  "machines": [
    {
      "machine_id": "i-1234567890abcdef0",
      "provider_instance": "aws-us-east-1",
      "status": "running",
      "created_at": "2024-01-15T10:32:00Z"
    }
  ],
  "provider_selection": {
    "selection_reason": "Explicitly specified in request",
    "confidence": 1.0,
    "selection_time_ms": 15
  },
  "created_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:35:00Z"
}
```

### Provider Selection Testing

#### Test Provider Selection
```http
POST /api/v1/providers/select
```

**Request:**
```json
{
  "template": {
    "template_id": "test-template",
    "provider_type": "aws",
    "provider_api": "EC2Fleet",
    "image_id": "ami-12345",
    "subnet_ids": ["subnet-123"],
    "max_instances": 5
  },
  "selection_strategy": "load_balanced"
}
```

**Response:**
```json
{
  "provider_type": "aws",
  "provider_instance": "aws-us-east-1",
  "selection_reason": "Load balanced across 2 AWS providers (weight: 10/15)",
  "confidence": 0.9,
  "alternatives": ["aws-us-west-2"],
  "selection_time_ms": 5,
  "validation_result": {
    "valid": true,
    "supported_features": ["API: EC2Fleet", "Instance count: 5"]
  }
}
```

## CLI Commands

### Provider Management

#### List Providers
```bash
# List all providers
ohfp providers list

# List providers with details
ohfp providers list --long

# List only enabled providers
ohfp providers list --enabled-only

# Filter by provider type
ohfp providers list --type aws
```

**Output:**
```
NAME            TYPE    ENABLED  PRIORITY  WEIGHT  STATUS
aws-us-east-1   aws     true     1         10      healthy
aws-us-west-2   aws     true     2         5       healthy
azure-east-us   azure   false    3         3       disabled
```

#### Show Provider Details
```bash
# Show provider details
ohfp providers show aws-us-east-1

# Show with capabilities
ohfp providers show aws-us-east-1 --capabilities

# Show with statistics
ohfp providers show aws-us-east-1 --stats
```

#### Validate Providers
```bash
# Validate all providers
ohfp providers validate

# Validate specific provider
ohfp providers validate aws-us-east-1

# Validate configuration file
ohfp providers validate --config config/providers.yml
```

### Template Management

#### List Templates by Provider
```bash
# List templates for provider type
ohfp templates list --provider-type aws

# List templates for provider instance
ohfp templates list --provider-name aws-us-east-1

# List templates for specific API
ohfp templates list --provider-api EC2Fleet
```

#### Show Template Source Information
```bash
# Show template with source info
ohfp templates show web-server --source-info

# Show template override chain
ohfp templates show web-server --override-chain
```

**Output:**
```
Template: web-server
Provider Type: aws
Provider Name: aws-us-east-1
Provider API: EC2Fleet

Source Information:
  Primary Source: config/aws-us-east-1_templates.json (provider_instance)
  Override Chain:
    1. config/templates.json (main) - base template
    2. config/awsprov_templates.json (provider_type) - AWS overrides
    3. config/aws-us-east-1_templates.json (provider_instance) - instance overrides

Fields by Source:
  templates.json: template_id, image_id, subnet_ids, max_instances
  awsprov_templates.json: provider_type, provider_api, instance_type
  aws-us-east-1_templates.json: provider_name, image_id (overridden)
```

#### Validate Templates
```bash
# Validate template against provider
ohfp templates validate web-server --provider aws-us-east-1

# Validate with strict mode
ohfp templates validate web-server --provider aws-us-east-1 --strict

# Validate against multiple providers
ohfp templates validate web-server --providers aws-us-east-1,aws-us-west-2

# Validate all templates
ohfp templates validate --all --provider aws-us-east-1
```

### Request Management

#### Create Requests with Provider Selection
```bash
# Create request with explicit provider
ohfp requests create --template web-server --count 5 --provider aws-us-east-1

# Create request with provider type (load balanced)
ohfp requests create --template web-server --count 5 --provider-type aws

# Create request with API-based selection
ohfp requests create --template web-server --count 5 --provider-api EC2Fleet

# Create request with default selection
ohfp requests create --template web-server --count 5
```

#### Test Provider Selection
```bash
# Test provider selection for template
ohfp providers select --template web-server

# Test with specific strategy
ohfp providers select --template web-server --strategy load_balanced

# Test with provider type filter
ohfp providers select --template web-server --provider-type aws
```

**Output:**
```
Provider Selection Result:
  Selected Provider: aws-us-east-1
  Provider Type: aws
  Selection Reason: Load balanced across 2 AWS providers (weight: 10/15)
  Confidence: 0.9
  Selection Time: 5ms

  Alternatives: aws-us-west-2

  Validation Result: Valid
    - API: EC2Fleet supported
    - Instance count: 5 (within limit of 1000)
    - Pricing: On-demand supported
    - Fleet type: instant supported
```

### Configuration Management

#### Refresh Template Cache
```bash
# Refresh template cache
ohfp templates refresh

# Refresh with verbose output
ohfp templates refresh --verbose
```

#### Migrate Templates
```bash
# Migrate templates to provider-specific files
ohfp templates migrate --from templates.json --to-provider aws-us-east-1

# Migrate with backup
ohfp templates migrate --from templates.json --to-provider aws-us-east-1 --backup

# Dry run migration
ohfp templates migrate --from templates.json --to-provider aws-us-east-1 --dry-run
```

## Error Responses

### Common Error Codes

#### Provider Errors
- `PROVIDER_NOT_FOUND`: Specified provider instance not found
- `PROVIDER_DISABLED`: Selected provider is disabled
- `NO_ENABLED_PROVIDERS`: No enabled providers available
- `PROVIDER_TYPE_MISMATCH`: Provider type doesn't match template

#### Template Errors
- `TEMPLATE_NOT_FOUND`: Template ID not found
- `TEMPLATE_INVALID`: Template validation failed
- `API_NOT_SUPPORTED`: Provider doesn't support required API
- `INSTANCE_LIMIT_EXCEEDED`: Request exceeds provider limits

#### Configuration Errors
- `INVALID_CONFIGURATION`: Provider configuration is invalid
- `FILE_NOT_FOUND`: Template file not found
- `PARSE_ERROR`: JSON parsing error in template file

### Error Response Format
```json
{
  "error": {
    "code": "PROVIDER_NOT_FOUND",
    "message": "Provider instance 'aws-invalid' not found in configuration",
    "details": {
      "requested_provider": "aws-invalid",
      "available_providers": ["aws-us-east-1", "aws-us-west-2"],
      "suggestion": "Use 'GET /api/v1/providers' to see available providers"
    },
    "timestamp": "2024-01-15T10:30:00Z",
    "request_id": "req-error-12345"
  }
}
```

## Rate Limiting

### API Rate Limits
- **Provider operations**: 100 requests/minute per client
- **Template operations**: 200 requests/minute per client
- **Request creation**: 50 requests/minute per client
- **Validation operations**: 500 requests/minute per client

### Rate Limit Headers
```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1642248600
X-RateLimit-Window: 60
```

## Authentication

### API Key Authentication
```http
Authorization: Bearer <api-key>
```

### CLI Authentication
```bash
# Set API key
export OHFP_API_KEY=<api-key>

# Or use config file
ohfp config set api-key <api-key>
```

## Pagination

### Request Parameters
- `page`: Page number (default: 1)
- `page_size`: Items per page (default: 50, max: 200)
- `sort`: Sort field and direction (e.g., `name:asc`, `priority:desc`)

### Response Format
```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total_items": 150,
    "total_pages": 3,
    "has_next": true,
    "has_previous": false
  }
}
```
