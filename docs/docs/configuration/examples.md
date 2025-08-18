# Provider Configuration Examples

## AWS Context Field Support

The AWS provider supports an optional `context` field for EC2 Fleet, Auto Scaling Group, and Spot Fleet operations. This field maps directly to the AWS Context parameter (reserved parameter) in the respective APIs and should follow AWS Context format (e.g., "c-abc1234567890123").

### Template with Context Field
```json
{
  "template_defaults": {
    "context": "c-abc1234567890123",
    "provider_api": "EC2Fleet",
    "instance_type": "t3.medium"
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
        "name": "aws-default",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1"
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
        "name": "aws-production",
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
        "name": "aws-us-east-1",
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
        "name": "aws-us-west-2",
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
        "name": "aws-primary",
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
        "name": "aws-backup",
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
        "name": "aws-account-prod",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 70,
        "config": {
          "region": "us-east-1",
          "profile": "production-account",
          "role_arn": "arn:aws:iam::123456789012:role/HostFactoryRole"
        }
      },
      {
        "name": "aws-account-dev",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 30,
        "config": {
          "region": "us-east-1",
          "profile": "development-account",
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
        "name": "aws-dev",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "development",
          "max_instances": 10,
          "instance_types": ["t3.micro", "t3.small"]
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
        "name": "aws-prod-primary",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 80,
        "config": {
          "region": "us-east-1",
          "profile": "production",
          "max_instances": 1000,
          "instance_types": ["m5.large", "m5.xlarge", "c5.large"]
        },
        "health_check": {
          "enabled": true,
          "interval": 30,
          "timeout": 15,
          "retry_count": 2
        }
      },
      {
        "name": "aws-prod-secondary",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 20,
        "config": {
          "region": "us-west-2",
          "profile": "production",
          "max_instances": 500,
          "instance_types": ["m5.large", "m5.xlarge", "c5.large"]
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
        "name": "aws-region-1",
        "type": "aws",
        "enabled": true,
        "weight": 50,
        "config": {"region": "us-east-1"}
      },
      {
        "name": "aws-region-2",
        "type": "aws",
        "enabled": true,
        "weight": 50,
        "config": {"region": "us-west-2"}
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
        "name": "aws-large-region",
        "type": "aws",
        "enabled": true,
        "weight": 70,
        "config": {
          "region": "us-east-1",
          "max_instances": 1000
        }
      },
      {
        "name": "aws-medium-region",
        "type": "aws",
        "enabled": true,
        "weight": 20,
        "config": {
          "region": "us-west-2",
          "max_instances": 300
        }
      },
      {
        "name": "aws-small-region",
        "type": "aws",
        "enabled": true,
        "weight": 10,
        "config": {
          "region": "eu-west-1",
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
        "name": "aws-primary-site",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "config": {
          "region": "us-east-1",
          "profile": "production"
        },
        "health_check": {
          "enabled": true,
          "interval": 15,
          "timeout": 10,
          "retry_count": 2
        }
      },
      {
        "name": "aws-dr-site",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "config": {
          "region": "us-west-2",
          "profile": "production"
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
        "name": "aws-site-east",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 50,
        "config": {
          "region": "us-east-1",
          "profile": "production"
        }
      },
      {
        "name": "aws-site-west",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 50,
        "config": {
          "region": "us-west-2",
          "profile": "production"
        }
      },
      {
        "name": "aws-site-europe",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 30,
        "config": {
          "region": "eu-west-1",
          "profile": "production"
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
        "name": "aws-localstack",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
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
        "name": "aws-test-1",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "test",
          "max_instances": 5
        }
      },
      {
        "name": "aws-test-2",
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
      "instance_type_field": "vmType"
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
        "name": "aws-migrated",
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
