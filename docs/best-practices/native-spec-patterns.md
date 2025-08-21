# Native Spec Best Practices and Patterns

## Overview

This guide provides best practices, design patterns, and recommendations for effectively using native AWS specs in production environments.

## Template Organization

### File Structure Best Practices

#### Recommended Directory Structure

```
specs/aws/
├── examples/                    # Sample templates for learning
│   ├── basic/                  # Simple use cases
│   ├── advanced/               # Complex configurations
│   └── complex/                # Multi-tier architectures
├── production/                 # Production-ready templates
│   ├── web-tier/              # Web server templates
│   ├── app-tier/              # Application server templates
│   ├── data-tier/             # Database templates
│   └── shared/                # Reusable components
├── staging/                   # Staging environment templates
├── development/               # Development templates
└── templates/                 # Base template library
    ├── launch-templates/      # Launch template specs
    ├── fleet-configs/         # Fleet configuration specs
    └── common/                # Common patterns
```

#### File Naming Conventions

**Good Naming:**
- `ec2fleet-web-tier-production.json`
- `launch-template-app-server-staging.json`
- `asg-database-multi-az.json`
- `spotfleet-batch-processing.json`

**Poor Naming:**
- `template1.json`
- `config.json`
- `test.json`
- `new-template.json`

#### Template Versioning

```
specs/aws/production/web-tier/
├── ec2fleet-web-v1.0.json
├── ec2fleet-web-v1.1.json
├── ec2fleet-web-v2.0.json
└── ec2fleet-web-latest.json -> ec2fleet-web-v2.0.json
```

### Template Composition Patterns

#### Pattern 1: Base Template with Overrides

**Base Template** (`templates/common/base-web-server.json`):
```json
{
  "LaunchTemplateName": "lt-base-{{ request_id }}",
  "LaunchTemplateData": {
    "ImageId": "{{ image_id }}",
    "InstanceType": "{{ instance_type | default('t3.medium') }}",
    "KeyName": "{{ key_name | default('default-key') }}",
    "SecurityGroupIds": ["{{ security_groups | default(['sg-default']) | join('\", \"') }}"],
    "IamInstanceProfile": {
      "Name": "{{ iam_role | default('default-instance-role') }}"
    },
    "TagSpecifications": [{
      "ResourceType": "instance",
      "Tags": [
        {"Key": "Name", "Value": "{{ instance_name_prefix }}-{{ request_id }}"},
        {"Key": "Environment", "Value": "{{ environment }}"},
        {"Key": "CreatedBy", "Value": "{{ package_name }}"}
      ]
    }]
  }
}
```

**Environment-Specific Override** (`production/web-tier/web-server-prod.json`):
```json
{
  "launch_template_spec_file": "templates/common/base-web-server.json",
  "custom_variables": {
    "instance_name_prefix": "prod-web",
    "environment": "production",
    "iam_role": "prod-web-server-role",
    "security_groups": ["sg-prod-web", "sg-prod-common"]
  }
}
```

#### Pattern 2: Modular Component Assembly

**Network Configuration** (`templates/common/network-config.json`):
```json
{
  "NetworkInterfaces": [{
    "DeviceIndex": 0,
    "AssociatePublicIpAddress": "{{ associate_public_ip | default(false) }}",
    "Groups": [
      {% for sg_id in security_group_ids %}
      "{{ sg_id }}"{% if not loop.last %},{% endif %}
      {% endfor %}
    ],
    "SubnetId": "{{ subnet_id }}"
  }]
}
```

**Storage Configuration** (`templates/common/storage-config.json`):
```json
{
  "BlockDeviceMappings": [
    {
      "DeviceName": "/dev/xvda",
      "Ebs": {
        "VolumeSize": "{{ root_volume_size | default(20) }}",
        "VolumeType": "{{ volume_type | default('gp3') }}",
        "DeleteOnTermination": true,
        "Encrypted": "{{ encrypt_volumes | default(true) }}"
      }
    }
  ]
}
```

## Security Best Practices

### Tagging Strategy

#### Mandatory Tags

Every resource should include these tags:

```json
{
  "Tags": [
    {"Key": "Name", "Value": "{{ resource_name }}"},
    {"Key": "Environment", "Value": "{{ environment }}"},
    {"Key": "Project", "Value": "{{ project_name }}"},
    {"Key": "Owner", "Value": "{{ team_name }}"},
    {"Key": "CostCenter", "Value": "{{ cost_center }}"},
    {"Key": "CreatedBy", "Value": "{{ package_name }}"},
    {"Key": "CreatedAt", "Value": "{{ created_timestamp }}"},
    {"Key": "RequestId", "Value": "{{ request_id }}"},
    {"Key": "TemplateId", "Value": "{{ template_id }}"}
  ]
}
```

#### Security Tags

```json
{
  "Tags": [
    {"Key": "SecurityLevel", "Value": "{{ security_level | default('standard') }}"},
    {"Key": "DataClassification", "Value": "{{ data_classification | default('internal') }}"},
    {"Key": "ComplianceRequired", "Value": "{{ compliance_required | default('false') }}"},
    {"Key": "EncryptionRequired", "Value": "{{ encryption_required | default('true') }}"}
  ]
}
```

### Access Control Patterns

#### IAM Instance Profile Template

```json
{
  "IamInstanceProfile": {
    "Name": "{{ iam_instance_profile }}"
  },
  "TagSpecifications": [{
    "ResourceType": "instance",
    "Tags": [
      {"Key": "IAMRole", "Value": "{{ iam_instance_profile }}"},
      {"Key": "AccessLevel", "Value": "{{ access_level | default('read-only') }}"}
    ]
  }]
}
```

#### Security Group Best Practices

```json
{
  "SecurityGroupIds": [
    "{{ base_security_group }}",
    {% if environment == 'production' %}
    "{{ production_security_group }}",
    {% endif %}
    {% for additional_sg in additional_security_groups %}
    "{{ additional_sg }}"{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
```

### Encryption Patterns

#### Volume Encryption

```json
{
  "BlockDeviceMappings": [
    {
      "DeviceName": "/dev/xvda",
      "Ebs": {
        "VolumeSize": "{{ root_volume_size | default(20) }}",
        "VolumeType": "{{ volume_type | default('gp3') }}",
        "Encrypted": true,
        "KmsKeyId": "{{ kms_key_id | default('alias/aws/ebs') }}",
        "DeleteOnTermination": true
      }
    }
  ]
}
```

#### Network Encryption

```json
{
  "MetadataOptions": {
    "HttpEndpoint": "enabled",
    "HttpTokens": "required",
    "HttpPutResponseHopLimit": 2,
    "InstanceMetadataTags": "enabled"
  }
}
```

## Performance Optimization

### Template Caching Strategies

#### Cache-Friendly Template Design

**Good - Simple Variable Substitution:**
```json
{
  "InstanceType": "{{ instance_type }}",
  "ImageId": "{{ image_id }}"
}
```

**Avoid - Complex Computations:**
```json
{
  "InstanceType": "{{ instance_types[environment][tier][region] | calculate_optimal_size(workload_type) }}"
}
```

#### Pre-computed Values

**Instead of:**
```json
{
  "TotalCapacity": "{{ (base_capacity * scaling_factor * environment_multiplier) | round | int }}"
}
```

**Use:**
```json
{
  "TotalCapacity": "{{ computed_capacity }}"
}
```

### Variable Optimization

#### Efficient Loops

**Good - Simple Iteration:**
```json
{
  "Overrides": [
    {% for subnet_id in subnet_ids %}
    {
      "SubnetId": "{{ subnet_id }}",
      "InstanceType": "{{ instance_type }}"
    }{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
```

**Avoid - Nested Complex Loops:**
```json
{
  "Overrides": [
    {% for region in regions %}
    {% for az in region.availability_zones %}
    {% for subnet in az.subnets %}
    {% for instance_type in instance_types %}
    {
      "SubnetId": "{{ subnet.id }}",
      "InstanceType": "{{ instance_type }}",
      "AvailabilityZone": "{{ az.name }}"
    }{% if not (loop.last and loop.outer.last and loop.outer.outer.last and loop.outer.outer.outer.last) %},{% endif %}
    {% endfor %}
    {% endfor %}
    {% endfor %}
    {% endfor %}
  ]
}
```

### Template Inheritance

#### Base Template Pattern

**Base Template** (`templates/base/ec2fleet-base.json`):
```json
{
  "Type": "{{ fleet_type | default('instant') }}",
  "TargetCapacitySpecification": {
    "TotalTargetCapacity": "{{ requested_count }}",
    "DefaultTargetCapacityType": "{{ default_capacity_type | default('on-demand') }}"
  },
  "LaunchTemplateConfigs": [{
    "LaunchTemplateSpecification": {
      "LaunchTemplateId": "{{ launch_template_id }}",
      "Version": "{{ launch_template_version }}"
    }
  }],
  "TagSpecifications": [{
    "ResourceType": "fleet",
    "Tags": [
      {"Key": "Name", "Value": "{{ fleet_name }}"},
      {"Key": "CreatedBy", "Value": "{{ package_name }}"}
    ]
  }]
}
```

**Specialized Template** (`production/web-tier/web-fleet.json`):
```json
{
  "provider_api_spec_file": "templates/base/ec2fleet-base.json",
  "custom_variables": {
    "fleet_type": "maintain",
    "fleet_name": "web-tier-fleet-{{ request_id }}",
    "default_capacity_type": "on-demand"
  }
}
```

## Cost Optimization Patterns

### Spot Instance Integration

#### Hybrid Spot/On-Demand Fleet

```json
{
  "Type": "maintain",
  "TargetCapacitySpecification": {
    "TotalTargetCapacity": "{{ requested_count }}",
    "OnDemandTargetCapacity": "{{ (requested_count * on_demand_percentage / 100) | round | int }}",
    "SpotTargetCapacity": "{{ (requested_count * spot_percentage / 100) | round | int }}",
    "DefaultTargetCapacityType": "spot"
  },
  "SpotOptions": {
    "AllocationStrategy": "price-capacity-optimized",
    "InstanceInterruptionBehavior": "terminate",
    "MaintenanceStrategies": {
      "CapacityRebalance": {
        "ReplacementStrategy": "launch"
      }
    }
  }
}
```

#### Cost-Aware Instance Selection

```json
{
  "Overrides": [
    {% for instance_config in cost_optimized_instances %}
    {
      "InstanceType": "{{ instance_config.type }}",
      "SubnetId": "{{ instance_config.subnet }}",
      "WeightedCapacity": "{{ instance_config.weight }}",
      "Priority": "{{ instance_config.cost_priority }}"
    }{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
```

### Resource Right-Sizing

#### Dynamic Instance Type Selection

```json
{
  "InstanceType": "{% if workload_type == 'cpu-intensive' %}{{ cpu_optimized_instance }}{% elif workload_type == 'memory-intensive' %}{{ memory_optimized_instance }}{% else %}{{ general_purpose_instance }}{% endif %}"
}
```

#### Storage Optimization

```json
{
  "BlockDeviceMappings": [
    {
      "DeviceName": "/dev/xvda",
      "Ebs": {
        "VolumeSize": "{{ root_volume_size | default(20) }}",
        "VolumeType": "{% if performance_tier == 'high' %}io2{% elif performance_tier == 'standard' %}gp3{% else %}gp2{% endif %}",
        "Iops": "{% if performance_tier == 'high' %}{{ high_performance_iops }}{% else %}{{ standard_iops }}{% endif %}"
      }
    }
  ]
}
```

## High Availability Patterns

### Multi-AZ Deployment

#### Cross-AZ Fleet Configuration

```json
{
  "LaunchTemplateConfigs": [{
    "LaunchTemplateSpecification": {
      "LaunchTemplateId": "{{ launch_template_id }}",
      "Version": "{{ launch_template_version }}"
    },
    "Overrides": [
      {% for az_config in availability_zones %}
      {
        "InstanceType": "{{ instance_type }}",
        "SubnetId": "{{ az_config.subnet_id }}",
        "AvailabilityZone": "{{ az_config.name }}",
        "WeightedCapacity": "{{ az_config.capacity_weight }}"
      }{% if not loop.last %},{% endif %}
      {% endfor %}
    ]
  }]
}
```

#### Fault-Tolerant Auto Scaling

```json
{
  "AutoScalingGroupName": "asg-{{ service_name }}-{{ request_id }}",
  "MinSize": "{{ min_instances_per_az * availability_zones | length }}",
  "MaxSize": "{{ max_instances_per_az * availability_zones | length }}",
  "DesiredCapacity": "{{ requested_count }}",
  "VPCZoneIdentifier": [
    {% for az in availability_zones %}
    "{{ az.subnet_id }}"{% if not loop.last %},{% endif %}
    {% endfor %}
  ],
  "HealthCheckType": "ELB",
  "HealthCheckGracePeriod": 300,
  "TerminationPolicies": ["OldestInstance", "Default"]
}
```

### Load Balancer Integration

#### Target Group Configuration

```json
{
  "TargetGroupARNs": [
    {% for tg_arn in target_group_arns %}
    "{{ tg_arn }}"{% if not loop.last %},{% endif %}
    {% endfor %}
  ],
  "HealthCheckType": "ELB",
  "HealthCheckGracePeriod": "{{ health_check_grace_period | default(300) }}"
}
```

## Monitoring and Observability

### CloudWatch Integration

#### Detailed Monitoring

```json
{
  "Monitoring": {
    "Enabled": true
  },
  "TagSpecifications": [{
    "ResourceType": "instance",
    "Tags": [
      {"Key": "MonitoringEnabled", "Value": "true"},
      {"Key": "LogGroup", "Value": "/aws/ec2/{{ service_name }}"},
      {"Key": "MetricNamespace", "Value": "{{ metric_namespace | default('Custom/Application') }}"}
    ]
  }]
}
```

#### Custom Metrics Tags

```json
{
  "Tags": [
    {"Key": "ServiceName", "Value": "{{ service_name }}"},
    {"Key": "ServiceVersion", "Value": "{{ service_version }}"},
    {"Key": "MetricsEnabled", "Value": "{{ enable_custom_metrics | default('true') }}"},
    {"Key": "AlertingEnabled", "Value": "{{ enable_alerting | default('true') }}"}
  ]
}
```

### Logging Configuration

#### Structured Logging Tags

```json
{
  "Tags": [
    {"Key": "LogLevel", "Value": "{{ log_level | default('INFO') }}"},
    {"Key": "LogRetention", "Value": "{{ log_retention_days | default('30') }}"},
    {"Key": "LogDestination", "Value": "{{ log_destination | default('cloudwatch') }}"}
  ]
}
```

## Error Handling and Resilience

### Graceful Degradation

#### Fallback Configuration

```json
{
  "InstanceType": "{{ preferred_instance_type | default(fallback_instance_type) }}",
  "SpotPrice": "{{ max_spot_price if use_spot else '' }}",
  "OnDemandOptions": {
    "AllocationStrategy": "{% if spot_unavailable %}lowest-price{% else %}{{ preferred_allocation_strategy }}{% endif %}"
  }
}
```

#### Circuit Breaker Pattern

```json
{
  "SpotOptions": {
    "AllocationStrategy": "price-capacity-optimized",
    "InstanceInterruptionBehavior": "terminate",
    "MaintenanceStrategies": {
      "CapacityRebalance": {
        "ReplacementStrategy": "{% if high_availability_mode %}launch-before-terminate{% else %}launch{% endif %}"
      }
    }
  }
}
```

### Retry and Recovery

#### Auto-Recovery Configuration

```json
{
  "ReplaceUnhealthyInstances": true,
  "HealthCheckType": "{{ health_check_type | default('EC2') }}",
  "HealthCheckGracePeriod": "{{ health_check_grace_period | default(300) }}",
  "Tags": [
    {"Key": "AutoRecovery", "Value": "enabled"},
    {"Key": "HealthCheckInterval", "Value": "{{ health_check_interval | default('30') }}"}
  ]
}
```

## Testing and Validation

### Template Testing Patterns

#### Validation Tags

```json
{
  "Tags": [
    {"Key": "TestingEnabled", "Value": "{{ enable_testing | default('false') }}"},
    {"Key": "ValidationLevel", "Value": "{{ validation_level | default('basic') }}"},
    {"Key": "TestEnvironment", "Value": "{{ test_environment | default('staging') }}"}
  ]
}
```

#### Dry-Run Configuration

```json
{
  "Type": "{% if dry_run_mode %}instant{% else %}{{ fleet_type }}{% endif %}",
  "TargetCapacitySpecification": {
    "TotalTargetCapacity": "{% if dry_run_mode %}1{% else %}{{ requested_count }}{% endif %}"
  }
}
```

## Documentation Patterns

### Self-Documenting Templates

#### Template Metadata

```json
{
  "_metadata": {
    "description": "Production web server fleet with auto-scaling",
    "version": "2.1.0",
    "author": "Infrastructure Team",
    "last_updated": "2024-01-15",
    "supported_environments": ["production", "staging"],
    "required_variables": ["image_id", "instance_type", "subnet_ids"],
    "optional_variables": ["spot_percentage", "health_check_grace_period"]
  }
}
```

#### Inline Documentation

```json
{
  "Type": "maintain",
  "_comment_type": "Fleet type: instant for one-time deployment, maintain for persistent fleet",
  
  "TargetCapacitySpecification": {
    "TotalTargetCapacity": "{{ requested_count }}",
    "_comment_capacity": "Total number of instances across all AZs and instance types"
  }
}
```

## Conclusion

Following these best practices ensures:

- **Maintainable Templates**: Well-organized, documented, and versioned
- **Secure Deployments**: Appropriate tagging, encryption, and access controls
- **Cost-Effective Operations**: Optimized instance selection and spot integration
- **High Availability**: Multi-AZ deployments with fault tolerance
- **Observable Systems**: Comprehensive monitoring and logging
- **Resilient Architecture**: Graceful degradation and auto-recovery

These patterns provide a solid foundation for building production-ready native spec templates that are secure, performant, and maintainable.
