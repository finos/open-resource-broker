# API Reference

This section contains comprehensive API documentation for the Open Host Factory Plugin.

## REST API Documentation

The plugin provides a REST API interface for all operations. The API follows OpenAPI 3.0 specification.

### Base URL
```
http://localhost:8000/api/v1
```

### Authentication
The API supports multiple authentication methods:
- JWT tokens
- AWS IAM authentication
- Amazon Cognito integration

### Endpoints

#### Templates
- `GET /templates` - List available templates
- `GET /templates/{id}` - Get template details
- `POST /templates/validate` - Validate template configuration

#### Machines
- `POST /machines` - Request machine provisioning
- `GET /machines/{id}` - Get machine details
- `DELETE /machines/{id}` - Terminate machine

#### Requests
- `GET /requests` - List provisioning requests
- `GET /requests/{id}` - Get request status
- `POST /requests/{id}/cancel` - Cancel request

### Response Format
All API responses follow a consistent format:

```json
{
  "success": true,
  "data": {},
  "error": null,
  "timestamp": "2025-07-09T07:00:00Z"
}
```

## Related Documentation
- [Developer Guide](../developer_guide/architecture.md) - Implementation guidance
- [User Guide](../user_guide/configuration.md) - Configuration options
