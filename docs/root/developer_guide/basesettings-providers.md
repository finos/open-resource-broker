# BaseSettings Provider Configuration Guide

This guide covers creating provider configurations using Pydantic BaseSettings for automatic environment variable support, type validation, and extensible configuration schemas.

## Overview

The Open Resource Broker uses Pydantic BaseSettings to provide:

- **Automatic Environment Variable Mapping**: Fields automatically map to environment variables
- **Type Safety**: Automatic type conversion and validation
- **Provider Extensibility**: Each provider defines its own configuration schema
- **Environment Variable Precedence**: Environment variables override configuration file values
- **Complex Object Support**: Nested objects support JSON environment variables

## Creating a BaseSettings Provider Configuration

### Step 1: Define Configuration Schema

```python
# src/providers/provider1/configuration/config.py
from typing import Optional, List
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from orb.infrastructure.interfaces.provider import BaseProviderConfig

class Provider1ProviderConfig(BaseSettings, BaseProviderConfig):
    """Provider1 provider configuration with automatic environment variable support."""
    
    model_config = SettingsConfigDict(
        env_prefix='ORB_PROVIDER1_',           # Environment variable prefix
        case_sensitive=False,              # Case insensitive env vars
        populate_by_name=True,             # Support field aliases
        env_nested_delimiter='__',         # Nested object delimiter
        extra="allow"                      # Allow extra fields
    )
    
    # Provider identification
    provider_type: str = "provider1"
    
    # Authentication fields - automatically mapped to ORB_PROVIDER1_* env vars
    account_id: str = Field(..., description="Provider1 subscription ID")
    tenant_id: str = Field(..., description="Provider1 tenant ID")
    client_id: str = Field(..., description="Provider1 client ID")
    client_secret: str = Field(..., description="Provider1 client secret")
    
    # Service configuration
    resource_group: str = Field(..., description="Provider1 resource group")
    location: str = Field("East US", description="Provider1 location")
    
    # Optional settings with defaults
    endpoint_url: Optional[str] = Field(None, description="Provider1 endpoint URL")
    max_retries: int = Field(3, description="Maximum retries for Provider1 API calls")
    timeout: int = Field(30, description="Request timeout in seconds")
    
    # Complex nested configuration
    vm_sizes: List[str] = Field(
        default=["Standard_D2s_v3", "Standard_D4s_v3"], 
        description="Supported VM sizes"
    )
    
    @model_validator(mode="after")
    def validate_authentication(self) -> "Provider1ProviderConfig":
        """Validate that required authentication fields are provided."""
        if not all([self.account_id, self.tenant_id, self.client_id, self.client_secret]):
            raise ValueError("All authentication fields are required for Provider1 provider")
        return self
```

### Step 2: Register Provider Settings

```python
# src/providers/provider1/__init__.py
from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
from .configuration.config import Provider1ProviderConfig

# Register Provider1 provider settings for automatic environment variable support
ProviderSettingsRegistry.register_provider_settings("provider1", Provider1ProviderConfig)
```

### Step 3: Use in Provider Implementation

```python
# src/providers/provider1/provider1_provider.py
from typing import Dict, Any
from orb.infrastructure.interfaces.provider import ProviderInterface
from .configuration.config import Provider1ProviderConfig

class Provider1Provider(ProviderInterface):
    """Provider1 cloud provider implementation."""

    def __init__(self, config: Provider1ProviderConfig):
        """Initialize Provider1 provider with BaseSettings configuration."""
        self.config = config
        self.logger = get_logger(__name__)
        
        # Configuration is already validated and type-safe
        self.account_id = config.account_id
        self.resource_group = config.resource_group
        self.location = config.location

    def initialize(self) -> bool:
        """Initialize Provider1 provider using configuration."""
        try:
            # Use validated configuration
            self.provider1_client = Provider1Client(
                account_id=self.config.account_id,
                tenant_id=self.config.tenant_id,
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                timeout=self.config.timeout,
                max_retries=self.config.max_retries
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Provider1 provider: {e}")
            return False
```

## Environment Variable Mapping

### Automatic Field Mapping

With `env_prefix='ORB_PROVIDER1_'`, fields automatically map to environment variables:

```python
# Configuration field -> Environment variable
account_id     -> ORB_PROVIDER1_SUBSCRIPTION_ID
tenant_id          -> ORB_PROVIDER1_TENANT_ID
client_id          -> ORB_PROVIDER1_CLIENT_ID
client_secret      -> ORB_PROVIDER1_CLIENT_SECRET
resource_group     -> ORB_PROVIDER1_RESOURCE_GROUP
location           -> ORB_PROVIDER1_LOCATION
max_retries        -> ORB_PROVIDER1_MAX_RETRIES
timeout            -> ORB_PROVIDER1_TIMEOUT
```

### Usage Examples

**Configuration File:**
```json
{
  "providers": [{
    "name": "provider1-east-us",
    "type": "provider1",
    "config": {
      "account_id": "12345678-1234-1234-1234-123456789012",
      "tenant_id": "87654321-4321-4321-4321-210987654321",
      "client_id": "app-client-id",
      "resource_group": "hostfactory-rg",
      "location": "East US",
      "max_retries": 3
    }
  }]
}
```

**Environment Variable Overrides:**
```bash
# Override specific fields
export ORB_PROVIDER1_CLIENT_SECRET="secure-production-secret"
export ORB_PROVIDER1_LOCATION="West US 2"
export ORB_PROVIDER1_MAX_RETRIES=5
export ORB_PROVIDER1_TIMEOUT=60

# Override complex fields with JSON
export ORB_PROVIDER1_VM_SIZES='["Standard_D8s_v3", "Standard_D16s_v3"]'
```

## Advanced Configuration Patterns

### Nested Object Support

```python
class NetworkConfig(BaseModel):
    """Network configuration for Provider1 provider."""
    virtual_network: str
    subnet: str
    security_group: str

class Provider1ProviderConfig(BaseSettings, BaseProviderConfig):
    # ... other fields ...
    
    # Nested configuration object
    network: NetworkConfig = Field(default_factory=NetworkConfig)
```

**Environment Variable Usage:**
```bash
# JSON format for complex objects
export ORB_PROVIDER1_NETWORK='{"virtual_network": "prod-vnet", "subnet": "prod-subnet", "security_group": "prod-sg"}'

# Nested delimiter format (if supported)
export ORB_PROVIDER1_NETWORK__VIRTUAL_NETWORK="prod-vnet"
export ORB_PROVIDER1_NETWORK__SUBNET="prod-subnet"
export ORB_PROVIDER1_NETWORK__SECURITY_GROUP="prod-sg"
```

### Field Aliases and Validation

```python
class Provider1ProviderConfig(BaseSettings, BaseProviderConfig):
    # Field with alias for backward compatibility
    max_retries: int = Field(
        3, 
        alias="retries",  # Also accepts ORB_PROVIDER1_RETRIES
        description="Maximum retries for Provider1 API calls"
    )
    
    # Custom validation
    @field_validator('location')
    @classmethod
    def validate_location(cls, v: str) -> str:
        """Validate Provider1 location format."""
        valid_locations = ["East US", "West US", "West US 2", "Central US"]
        if v not in valid_locations:
            raise ValueError(f"Invalid location: {v}. Must be one of {valid_locations}")
        return v
    
    @model_validator(mode="after")
    def validate_resource_limits(self) -> "Provider1ProviderConfig":
        """Validate resource configuration limits."""
        if self.max_retries > 10:
            raise ValueError("max_retries cannot exceed 10")
        if self.timeout > 300:
            raise ValueError("timeout cannot exceed 300 seconds")
        return self
```

### Type Conversion

BaseSettings automatically handles type conversion:

```python
class Provider1ProviderConfig(BaseSettings, BaseProviderConfig):
    # Integer fields
    max_retries: int = 3           # ORB_PROVIDER1_MAX_RETRIES="5" -> int(5)
    timeout: int = 30              # ORB_PROVIDER1_TIMEOUT="60" -> int(60)
    
    # Boolean fields  
    enable_monitoring: bool = True  # ORB_PROVIDER1_ENABLE_MONITORING="false" -> False
    
    # List fields
    vm_sizes: List[str] = []       # ORB_PROVIDER1_VM_SIZES='["size1", "size2"]' -> ["size1", "size2"]
    
    # Optional fields
    endpoint_url: Optional[str] = None  # ORB_PROVIDER1_ENDPOINT_URL="" -> None
```

## Integration with Provider Factory

### Provider Factory Usage

The provider factory automatically uses BaseSettings for configuration:

```python
# src/providers/factory.py
from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry

class ProviderStrategyFactory:
    def create_provider_config(self, instance_config: ProviderInstanceConfig):
        """Create provider configuration with automatic env var loading."""
        
        # Use BaseSettings for automatic environment variable loading
        typed_config = ProviderSettingsRegistry.create_settings(
            instance_config.type, 
            instance_config.config
        )
        
        if instance_config.type == "provider1":
            # typed_config is already an Provider1ProviderConfig instance
            return typed_config
        
        # Fallback for providers without BaseSettings
        return instance_config.config
```

### Configuration Loading Process

1. **Configuration File**: Base configuration loaded from JSON/YAML
2. **Environment Variables**: Override config file values automatically
3. **Type Validation**: Pydantic validates and converts types
4. **Custom Validation**: Model validators ensure configuration consistency
5. **Provider Creation**: Type-safe configuration passed to provider

## Best Practices

### Configuration Design

1. **Use Descriptive Field Names**: Clear, unambiguous field names
2. **Provide Sensible Defaults**: Default values for optional fields
3. **Add Field Descriptions**: Help users understand configuration options
4. **Group Related Fields**: Use nested models for related configuration
5. **Validate Early**: Use model validators to catch configuration errors

### Environment Variable Naming

1. **Consistent Prefixes**: Use `ORB_{PROVIDER}_` pattern
2. **Clear Hierarchy**: Reflect configuration structure in variable names
3. **Avoid Conflicts**: Ensure variables don't conflict with system variables
4. **Document Variables**: Provide clear documentation for all supported variables

### Security Considerations

1. **Sensitive Fields**: Mark sensitive fields appropriately
2. **Environment Variables**: Use environment variables for secrets
3. **Validation**: Validate authentication configuration
4. **Logging**: Avoid logging sensitive configuration values

```python
class Provider1ProviderConfig(BaseSettings, BaseProviderConfig):
    # Sensitive field - should come from environment variable
    client_secret: str = Field(..., description="Provider1 client secret (use ORB_PROVIDER1_CLIENT_SECRET)")
    
    @model_validator(mode="after")
    def mask_sensitive_fields(self) -> "Provider1ProviderConfig":
        """Ensure sensitive fields are not logged."""
        # Implementation to mask sensitive fields in logs
        return self
```

## Testing BaseSettings Configurations

### Unit Testing

```python
import pytest
from orb.providers.provider1.configuration.config import Provider1ProviderConfig

def test_provider1_config_validation():
    """Test Provider1 configuration validation."""
    config = Provider1ProviderConfig(
        account_id="test-subscription",
        tenant_id="test-tenant",
        client_id="test-client",
        client_secret="test-secret",
        resource_group="test-rg"
    )
    
    assert config.account_id == "test-subscription"
    assert config.location == "East US"  # Default value
    assert config.max_retries == 3       # Default value

def test_environment_variable_override(monkeypatch):
    """Test environment variable override."""
    monkeypatch.setenv("ORB_PROVIDER1_LOCATION", "West US 2")
    monkeypatch.setenv("ORB_PROVIDER1_MAX_RETRIES", "5")
    
    config = Provider1ProviderConfig(
        account_id="test-subscription",
        tenant_id="test-tenant", 
        client_id="test-client",
        client_secret="test-secret",
        resource_group="test-rg"
    )
    
    assert config.location == "West US 2"  # From environment
    assert config.max_retries == 5         # From environment

def test_validation_errors():
    """Test configuration validation errors."""
    with pytest.raises(ValueError, match="All authentication fields are required"):
        Provider1ProviderConfig(
            account_id="test-subscription"
            # Missing required fields
        )
```

## Migration from Legacy Configuration

### Backward Compatibility

When migrating from legacy configuration patterns:

```python
class Provider1ProviderConfig(BaseSettings, BaseProviderConfig):
    # New field with legacy alias
    max_retries: int = Field(3, alias="retries")
    
    # Support both new and legacy field names
    @field_validator('max_retries', mode='before')
    @classmethod
    def handle_legacy_retries(cls, v, info):
        """Handle legacy 'retries' field name."""
        if info.field_name == 'retries':
            return v
        return v
```

### Migration Steps

1. **Create BaseSettings Class**: Define new configuration schema
2. **Register Provider Settings**: Add to provider settings registry
3. **Update Provider Implementation**: Use new configuration class
4. **Test Environment Variables**: Verify all fields support env vars
5. **Update Documentation**: Document new environment variables
6. **Deprecate Legacy**: Plan deprecation of old configuration patterns

## Next Steps

- **[Provider Architecture](providers.md)**: Learn about the complete provider architecture
- **[Configuration Guide](../configuration/examples.md)**: See configuration examples
- **[Environment Variables](../user_guide/configuration.md)**: Complete environment variable reference