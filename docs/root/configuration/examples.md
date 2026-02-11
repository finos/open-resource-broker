# Provider Configuration Examples

## Provider Naming Conventions

### AWS Provider Naming Pattern

AWS providers use the naming pattern: `aws_{profile}_{region}`

**Standard Examples:**
- `aws_default_us-east-1` - Default profile in US East 1
- `aws_prod_us-west-2` - Production profile in US West 2  
- `aws_dev_eu-west-1` - Development profile in EU West 1
- `aws_staging_ap-southeast-1` - Staging profile in Asia Pacific Southeast 1

**Multi-Account Examples:**
- `aws_account-prod_us-east-1` - Production account in US East 1
- `aws_account-dev_us-east-1` - Development account in US East 1

### Template File Naming

Template files are automatically named based on provider names:
- `aws_default_us-east-1_templates.json`
- `aws_prod_us-west-2_templates.json`
- `aws_dev_eu-west-1_templates.json`

## AWS Context Field Support

The AWS provider supports an optional `context` field for EC2 Fleet, Auto Scaling Group, and Spot Fleet operations. Context is a reserved field in EC2 APIs that should be used by customers when advised by the EC2 team at AWS. This field maps directly to the AWS Context parameter in the respective APIs and should follow AWS Context format (e.g., "c-abc1234567890123").

### Template with Context Field
```json
{
  "template_defaults": {
    "context": "c-abc1234567890123",
    "provider_api": "EC2Fleet",
    "machine_types": {"t3.medium": 1}
  }
}
```

**Supported Handlers:**
- EC2 Fleet: Maps to `Context` parameter in `create_fleet()`
- Auto Scaling Group: Maps to `Context` parameter in `create_auto_scaling_group()`
- Spot Fleet: Maps to `Context` parameter in `request_spot_fleet()`
- RunInstances: Not supported (AWS API limitation)

## Basic Single Provider

### Minimal Configuration
```json
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      }
    ]
  }
}
```

### Single Provider with Full Configuration
```json
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "health_check_interval": 300,
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 5,
      "recovery_timeout": 60,
      "half_open_max_calls": 3
    },
    "providers": [
      {
        "name": "aws_production_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 100,
        "config": {
          "region": "us-east-1",
          "profile": "production",
          "max_instances": 100,
          "timeout": 30
        },
        "capabilities": [
          "instances",
          "spot_instances",
          "fleet_management"
        ],
        "health_check": {
          "enabled": true,
          "interval": 60,
          "timeout": 30,
          "retry_count": 3
        }
      }
    ]
  }
}
```

## Multi-Region Setup

### Active-Active Multi-Region
```json
{
  "provider": {
    "selection_policy": "WEIGHTED_ROUND_ROBIN",
    "health_check_interval": 180,
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 3,
      "recovery_timeout": 120
    },
    "providers": [
      {
        "name": "aws_production_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 60,
        "config": {
          "region": "us-east-1",
          "profile": "production",
          "availability_zones": ["us-east-1a", "us-east-1b", "us-east-1c"]
        },
        "capabilities": ["instances", "spot_instances", "fleet_management"]
      },
      {
        "name": "aws_production_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 40,
        "config": {
          "region": "us-west-2",
          "profile": "production",
          "availability_zones": ["us-west-2a", "us-west-2b", "us-west-2c"]
        },
        "capabilities": ["instances", "spot_instances", "fleet_management"]
      }
    ]
  }
}
```

### Primary-Backup Multi-Region
```json
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "health_check_interval": 120,
    "providers": [
      {
        "name": "aws_production_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 100,
        "config": {
          "region": "us-east-1",
          "profile": "production"
        }
      },
      {
        "name": "aws_production_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 100,
        "config": {
          "region": "us-west-2",
          "profile": "production"
        }
      }
    ]
  }
}
```

## Multi-Account Setup

### Cross-Account Configuration
```json
{
  "provider": {
    "selection_policy": "ROUND_ROBIN",
    "providers": [
      {
        "name": "aws_prod-account_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 70,
        "config": {
          "region": "us-east-1",
          "profile": "prod-account",
          "role_arn": "arn:aws:iam::123456789012:role/HostFactoryRole"
        }
      },
      {
        "name": "aws_dev-account_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 30,
        "config": {
          "region": "us-east-1",
          "profile": "dev-account",
          "role_arn": "arn:aws:iam::987654321098:role/HostFactoryRole"
        }
      }
    ]
  }
}
```

## Environment-Specific Configurations

### Development Environment
```json
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "health_check_interval": 600,
    "circuit_breaker": {
      "enabled": false
    },
    "providers": [
      {
        "name": "aws_dev_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "dev",
          "max_instances": 10,
          "machine_types": {"t3.micro": 1, "t3.small": 1}
        }
      }
    ]
  }
}
```

### Production Environment
```json
{
  "provider": {
    "selection_policy": "LEAST_RESPONSE_TIME",
    "health_check_interval": 60,
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 3,
      "recovery_timeout": 300
    },
    "providers": [
      {
        "name": "aws_prod_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 80,
        "config": {
          "region": "us-east-1",
          "profile": "prod",
          "max_instances": 1000,
          "machine_types": {"m5.large": 1, "m5.xlarge": 1, "c5.large": 1}
        },
        "health_check": {
          "enabled": true,
          "interval": 30,
          "timeout": 15,
          "retry_count": 2
        }
      },
      {
        "name": "aws_prod_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 20,
        "config": {
          "region": "us-west-2",
          "profile": "prod",
          "max_instances": 500,
          "machine_types": {"m5.large": 1, "m5.xlarge": 1, "c5.large": 1}
        },
        "health_check": {
          "enabled": true,
          "interval": 30,
          "timeout": 15,
          "retry_count": 2
        }
      }
    ]
  }
}
```

## Load Balancing Scenarios

### Equal Distribution
```json
{
  "provider": {
    "selection_policy": "ROUND_ROBIN",
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "weight": 50,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      },
      {
        "name": "aws_default_us-west-2",
        "type": "aws",
        "enabled": true,
        "weight": 50,
        "config": {
          "region": "us-west-2",
          "profile": "default"
        }
      }
    ]
  }
}
```

### Capacity-Based Distribution
```json
{
  "provider": {
    "selection_policy": "WEIGHTED_ROUND_ROBIN",
    "providers": [
      {
        "name": "aws_prod_us-east-1",
        "type": "aws",
        "enabled": true,
        "weight": 70,
        "config": {
          "region": "us-east-1",
          "profile": "prod",
          "max_instances": 1000
        }
      },
      {
        "name": "aws_prod_us-west-2",
        "type": "aws",
        "enabled": true,
        "weight": 20,
        "config": {
          "region": "us-west-2",
          "profile": "prod",
          "max_instances": 300
        }
      },
      {
        "name": "aws_prod_eu-west-1",
        "type": "aws",
        "enabled": true,
        "weight": 10,
        "config": {
          "region": "eu-west-1",
          "profile": "prod",
          "max_instances": 100
        }
      }
    ]
  }
}
```

## Disaster Recovery Scenarios

### Hot Standby
```json
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "health_check_interval": 30,
    "providers": [
      {
        "name": "aws_prod_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "config": {
          "region": "us-east-1",
          "profile": "prod"
        },
        "health_check": {
          "enabled": true,
          "interval": 15,
          "timeout": 10,
          "retry_count": 2
        }
      },
      {
        "name": "aws_prod_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "config": {
          "region": "us-west-2",
          "profile": "prod"
        },
        "health_check": {
          "enabled": true,
          "interval": 15,
          "timeout": 10,
          "retry_count": 2
        }
      }
    ]
  }
}
```

### Multi-Site Active
```json
{
  "provider": {
    "selection_policy": "LEAST_CONNECTIONS",
    "health_check_interval": 60,
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 2,
      "recovery_timeout": 180
    },
    "providers": [
      {
        "name": "aws_prod_us-east-1",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 50,
        "config": {
          "region": "us-east-1",
          "profile": "prod"
        }
      },
      {
        "name": "aws_prod_us-west-2",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 50,
        "config": {
          "region": "us-west-2",
          "profile": "prod"
        }
      },
      {
        "name": "aws_prod_eu-west-1",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 30,
        "config": {
          "region": "eu-west-1",
          "profile": "prod"
        }
      }
    ]
  }
}
```

## Testing and Development

### Local Development
```json
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "health_check_interval": 0,
    "circuit_breaker": {
      "enabled": false
    },
    "providers": [
      {
        "name": "aws_localstack_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "localstack",
          "endpoint_url": "http://localhost:4566",
          "aws_access_key_id": "test",
          "aws_secret_access_key": "test"
        }
      }
    ]
  }
}
```

### Integration Testing
```json
{
  "provider": {
    "selection_policy": "ROUND_ROBIN",
    "providers": [
      {
        "name": "aws_test_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "test",
          "max_instances": 5
        }
      },
      {
        "name": "aws_test_us-west-2",
        "type": "aws",
        "enabled": false,
        "config": {
          "region": "us-west-2",
          "profile": "test",
          "max_instances": 5
        }
      }
    ]
  }
}
```

## Scheduler Configuration

The scheduler configuration determines how the system interfaces with job schedulers like IBM Symphony Host Factory.

### Basic Scheduler Configuration
```json
{
  "scheduler": {
    "strategy": "hostfactory",
    "config_root": "config"
  }
}
```

### Advanced Scheduler Configuration
```json
{
  "scheduler": {
    "strategy": "hostfactory",
    "config_root": "config",
    "template_path": "awsprov_templates.json",
    "field_mapping": {
      "template_id_field": "templateId",
      "max_instances_field": "maxNumber",
      "image_id_field": "imageId",
      "machine_type_field": "vmType"
    },
    "output_format": {
      "use_camel_case": true,
      "include_attributes": true,
      "attribute_format": "hostfactory"
    }
  }
}
```

### Complete Configuration with Scheduler
```json
{
  "version": "2.0.0",
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "providers": [
      {
        "name": "aws-default",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      }
    ]
  },
  "scheduler": {
    "strategy": "hostfactory",
    "config_root": "config",
    "template_path": "awsprov_templates.json"
  },
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "storage_type": "single_file",
      "base_path": "data",
      "filenames": {
        "single_file": "request_database.json"
      }
    }
  },
  "logging": {
    "level": "INFO",
    "file_path": "logs/app.log",
    "console_enabled": true
  }
}
```

## Migration Examples

### From Legacy to New Format
```json
// OLD FORMAT (no longer supported)
{
  "provider": {
    "type": "aws",
    "aws": {
      "region": "us-east-1",
      "profile": "default"
    }
  }
}

// NEW FORMAT
{
  "provider": {
    "selection_policy": "FIRST_AVAILABLE",
    "providers": [
      {
        "name": "aws_default_us-east-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "default"
        }
      }
    ]
  }
}
```

### Provider Name Migration Examples

**Single Region Migration:**
```json
// Before: Generic naming
{
  "name": "aws-primary",
  "config": {
    "region": "us-east-1",
    "profile": "default"
  }
}

// After: Structured naming
{
  "name": "aws_default_us-east-1",
  "config": {
    "region": "us-east-1", 
    "profile": "default"
  }
}
```

**Multi-Region Migration:**
```json
// Before: Generic naming
{
  "providers": [
    {"name": "aws-east", "config": {"region": "us-east-1", "profile": "prod"}},
    {"name": "aws-west", "config": {"region": "us-west-2", "profile": "prod"}}
  ]
}

// After: Structured naming
{
  "providers": [
    {"name": "aws_prod_us-east-1", "config": {"region": "us-east-1", "profile": "prod"}},
    {"name": "aws_prod_us-west-2", "config": {"region": "us-west-2", "profile": "prod"}}
  ]
}
```

### Template File Migration

**Before:**
- `awsprov_templates.json` (generic)
- `aws-primary_templates.json` (old naming)

**After:**
- `aws_default_us-east-1_templates.json` (structured naming)
- `aws_prod_us-west-2_templates.json` (structured naming)
