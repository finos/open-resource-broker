# Migration Guide: Legacy Templates to Native Specs

## Overview

This guide provides a comprehensive approach to migrating from legacy template configurations to native AWS spec support, ensuring zero downtime and minimal risk.

## Migration Strategy

### Preparation and Parallel Operation (Week 1-2)

#### 1.1 Enable Native Specs Alongside Legacy Templates

```json
{
  "native_spec": {
    "enabled": true,
    "merge_mode": "extend",
    "error_handling": {
      "fallback_to_legacy": true,
      "log_rendering_errors": true
    }
  }
}
```

#### 1.2 Inventory Existing Templates

```bash
# List all current templates
ohfp templates list --format table

# Analyze template complexity
ohfp templates analyze --migration-readiness

# Export template configurations
ohfp templates export --output-dir ./legacy-templates
```

#### 1.3 Create Migration Plan

1. **Low Risk Templates** (migrate first):
   - Simple EC2Fleet instant deployments
   - Basic launch templates
   - Templates with minimal customization

2. **Medium Risk Templates** (migrate second):
   - SpotFleet configurations
   - Auto Scaling Groups
   - Templates with custom networking

3. **High Risk Templates** (migrate last):
   - Complex multi-tier architectures
   - Templates with extensive customization
   - Business-critical production workloads

### Template Conversion (Week 3-4)

#### 2.1 Conversion Process

For each template, follow this process:

1. **Analyze Legacy Template**
2. **Create Native Spec Equivalent**
3. **Test in Development Environment**
4. **Validate Functionality**
5. **Document Changes**

#### 2.2 Template Conversion Examples

##### Example 1: Basic EC2Fleet Template

**Legacy Template:**
```json
{
  "template_id": "web-server-fleet",
  "provider_api": "EC2Fleet",
  "instance_config": {
    "image_id": "ami-12345678",
    "instance_type": "t3.medium",
    "key_name": "web-server-key",
    "security_group_ids": ["sg-web", "sg-common"]
  },
  "fleet_config": {
    "target_capacity": 5,
    "fleet_type": "maintain",
    "on_demand_percentage": 50
  },
  "networking": {
    "subnet_ids": ["subnet-web-1", "subnet-web-2"],
    "associate_public_ip": false
  }
}
```

**Native Spec Equivalent:**
```json
{
  "template_id": "web-server-fleet",
  "provider_api": "EC2Fleet",
  "instance_config": {
    "image_id": "ami-12345678",
    "instance_type": "t3.medium"
  },
  "provider_api_spec": {
    "Type": "maintain",
    "TargetCapacitySpecification": {
      "TotalTargetCapacity": "{{ requested_count }}",
      "OnDemandTargetCapacity": "{{ (requested_count * 0.5) | round | int }}",
      "SpotTargetCapacity": "{{ (requested_count * 0.5) | round | int }}",
      "DefaultTargetCapacityType": "on-demand"
    },
    "LaunchTemplateConfigs": [{
      "LaunchTemplateSpecification": {
        "LaunchTemplateId": "{{ launch_template_id }}",
        "Version": "{{ launch_template_version }}"
      },
      "Overrides": [
        {% for subnet_id in ["subnet-web-1", "subnet-web-2"] %}
        {
          "SubnetId": "{{ subnet_id }}",
          "InstanceType": "{{ instance_type }}"
        }{% if not loop.last %},{% endif %}
        {% endfor %}
      ]
    }],
    "ReplaceUnhealthyInstances": true
  },
  "launch_template_spec": {
    "LaunchTemplateName": "lt-web-server-{{ request_id }}",
    "LaunchTemplateData": {
      "ImageId": "{{ image_id }}",
      "InstanceType": "{{ instance_type }}",
      "KeyName": "web-server-key",
      "SecurityGroupIds": ["sg-web", "sg-common"],
      "TagSpecifications": [{
        "ResourceType": "instance",
        "Tags": [
          {"Key": "Name", "Value": "web-server-{{ request_id }}"},
          {"Key": "Environment", "Value": "production"},
          {"Key": "CreatedBy", "Value": "{{ package_name }}"}
        ]
      }]
    }
  }
}
```

##### Example 2: Auto Scaling Group Template

**Legacy Template:**
```json
{
  "template_id": "app-server-asg",
  "provider_api": "AutoScaling",
  "instance_config": {
    "image_id": "ami-87654321",
    "instance_type": "m5.large"
  },
  "asg_config": {
    "min_size": 2,
    "max_size": 10,
    "desired_capacity": 4,
    "health_check_type": "ELB",
    "health_check_grace_period": 300
  }
}
```

**Native Spec Equivalent:**
```json
{
  "template_id": "app-server-asg",
  "provider_api": "AutoScaling",
  "instance_config": {
    "image_id": "ami-87654321",
    "instance_type": "m5.large"
  },
  "provider_api_spec": {
    "AutoScalingGroupName": "asg-app-server-{{ request_id }}",
    "MinSize": 2,
    "MaxSize": 10,
    "DesiredCapacity": "{{ requested_count }}",
    "HealthCheckType": "ELB",
    "HealthCheckGracePeriod": 300,
    "LaunchTemplate": {
      "LaunchTemplateId": "{{ launch_template_id }}",
      "Version": "{{ launch_template_version }}"
    },
    "VPCZoneIdentifier": ["{{ subnet_ids | join('\", \"') }}"],
    "Tags": [{
      "Key": "Name",
      "Value": "app-server-{{ request_id }}",
      "PropagateAtLaunch": true,
      "ResourceId": "asg-app-server-{{ request_id }}",
      "ResourceType": "auto-scaling-group"
    }]
  }
}
```

#### 2.3 Conversion Tools

Use the migration tool to assist with conversion:

```bash
# Analyze legacy template for migration opportunities
ohfp migrate analyze --template-id web-server-fleet

# Generate native spec equivalent
ohfp migrate convert --template-id web-server-fleet --output native-web-server.json

# Validate converted template
ohfp templates validate --file native-web-server.json

# Compare legacy vs native spec behavior
ohfp migrate compare --legacy web-server-fleet --native native-web-server.json
```

### Testing and Validation (Week 5-6)

#### 3.1 Development Environment Testing

```bash
# Test native spec template
ohfp requests create --template-id web-server-fleet-native --count 2 --dry-run

# Compare with legacy template
ohfp requests create --template-id web-server-fleet-legacy --count 2 --dry-run

# Validate AWS API calls match expected behavior
ohfp debug aws-calls --template-id web-server-fleet-native --count 2
```

#### 3.2 Staging Environment Validation

1. **Deploy Side-by-Side**: Run both legacy and native spec versions
2. **Monitor Resource Creation**: Ensure identical AWS resources are created
3. **Performance Testing**: Validate template rendering performance
4. **Error Handling**: Test fallback scenarios

#### 3.3 Validation Checklist

- [ ] Template renders without errors
- [ ] All required variables are defined
- [ ] AWS API calls match expected parameters
- [ ] Resource tags are correctly applied
- [ ] Security groups and networking are identical
- [ ] Performance is acceptable
- [ ] Error handling works correctly
- [ ] Monitoring and alerting function properly

### Production Migration (Week 7-8)

#### 4.1 Gradual Rollout Strategy

1. **Canary Deployment** (10% of templates):
   - Migrate lowest-risk templates first
   - Monitor for 48 hours
   - Validate all functionality

2. **Progressive Rollout** (50% of templates):
   - Migrate medium-risk templates
   - Monitor for 24 hours
   - Address any issues

3. **Full Rollout** (100% of templates):
   - Migrate remaining templates
   - Monitor continuously
   - Maintain rollback capability

#### 4.2 Rollback Plan

Maintain ability to rollback at each phase:

```json
{
  "native_spec": {
    "enabled": false,
    "error_handling": {
      "fallback_to_legacy": true
    }
  }
}
```

Or per-template rollback:
```bash
# Disable native spec for specific template
ohfp templates update web-server-fleet --disable-native-spec

# Revert to legacy configuration
ohfp templates revert web-server-fleet --to-legacy
```

### Cleanup and Optimization (Week 9-10)

#### 5.1 Remove Legacy Configurations

Once native specs are stable:

```json
{
  "native_spec": {
    "enabled": true,
    "merge_mode": "override",
    "error_handling": {
      "fallback_to_legacy": false
    }
  }
}
```

#### 5.2 Optimize Native Specs

1. **Template Consolidation**: Merge similar templates
2. **Performance Optimization**: Optimize Jinja2 expressions
3. **File Organization**: Organize spec files logically
4. **Documentation Updates**: Update all documentation

## Common Migration Patterns

### Pattern 1: Simple Field Mapping

**Legacy Field** → **Native Spec Field**
- `fleet_config.target_capacity` → `TargetCapacitySpecification.TotalTargetCapacity`
- `asg_config.min_size` → `MinSize`
- `instance_config.image_id` → `LaunchTemplateData.ImageId`

### Pattern 2: Complex Logic Migration

**Legacy Logic:**
```python
if fleet_type == "spot":
    use_spot_fleet_api()
else:
    use_ec2_fleet_api()
```

**Native Spec Equivalent:**
```json
{
  "provider_api_spec": {
    "Type": "{{ 'maintain' if fleet_type == 'spot' else 'instant' }}",
    "SpotOptions": {
      "AllocationStrategy": "{{ spot_allocation_strategy if fleet_type == 'spot' else '' }}"
    }
  }
}
```

### Pattern 3: Dynamic Configuration

**Legacy Dynamic Config:**
```python
subnet_configs = []
for i, subnet in enumerate(subnet_ids):
    subnet_configs.append({
        "subnet_id": subnet,
        "instance_type": instance_types[i % len(instance_types)]
    })
```

**Native Spec Equivalent:**
```json
{
  "Overrides": [
    {% for subnet_id in subnet_ids %}
    {
      "SubnetId": "{{ subnet_id }}",
      "InstanceType": "{{ instance_types[loop.index0 % instance_types|length] }}"
    }{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
```

## Migration Validation

### Automated Validation

```bash
# Run comprehensive migration validation
ohfp migrate validate --all-templates

# Generate migration report
ohfp migrate report --output migration-report.html

# Test all converted templates
ohfp migrate test --dry-run --all-converted
```

### Manual Validation Steps

1. **Resource Comparison**: Compare AWS resources created by legacy vs native specs
2. **Cost Analysis**: Ensure no unexpected cost changes
3. **Performance Testing**: Validate template rendering performance
4. **Security Review**: Confirm security configurations are maintained
5. **Monitoring Validation**: Ensure monitoring and alerting work correctly

## Troubleshooting Migration Issues

### Common Issues and Solutions

#### Issue: Template Variable Not Found
```
Error: Variable 'custom_field' is undefined
```
**Solution**: Add custom variable to template configuration or use default filter
```json
{
  "Value": "{{ custom_field | default('default-value') }}"
}
```

#### Issue: AWS API Schema Validation Failed
```
Error: Invalid parameter 'InvalidField' for CreateFleet operation
```
**Solution**: Check AWS API documentation and correct field names/structure

#### Issue: Template Rendering Timeout
```
Error: Template rendering exceeded 30 second timeout
```
**Solution**: Optimize Jinja2 expressions or increase timeout setting

#### Issue: Fallback Not Working
```
Error: Native spec failed and fallback disabled
```
**Solution**: Enable fallback during migration period
```json
{
  "native_spec": {
    "error_handling": {
      "fallback_to_legacy": true
    }
  }
}
```

## Post-Migration Best Practices

### 1. Template Organization

```
specs/aws/
├── production/
│   ├── web-tier/
│   ├── app-tier/
│   └── data-tier/
├── staging/
└── shared/
    ├── common-tags.json
    └── security-groups.json
```

### 2. Version Control

- Tag all template versions
- Maintain migration history
- Document all changes
- Use semantic versioning

### 3. Monitoring and Alerting

- Monitor native spec adoption rates
- Alert on template rendering failures
- Track performance metrics
- Monitor AWS resource creation patterns

### 4. Documentation Maintenance

- Update all template documentation
- Create usage examples
- Maintain troubleshooting guides
- Document custom variables and patterns

## Success Metrics

### Migration Success Criteria

- [ ] 100% of templates successfully converted
- [ ] Zero production incidents during migration
- [ ] Template rendering performance maintained or improved
- [ ] All functionality preserved
- [ ] Team trained on native spec usage
- [ ] Documentation updated and complete

### Key Performance Indicators

- **Migration Completion Rate**: Percentage of templates migrated
- **Error Rate**: Native spec rendering error rate
- **Performance**: Template rendering time comparison
- **Adoption Rate**: Team usage of native spec features
- **Incident Rate**: Production incidents related to migration

## Conclusion

Following this migration guide ensures a smooth transition from legacy templates to native AWS specs with minimal risk and maximum benefit. The phased approach allows for thorough testing and validation at each step, while the comprehensive tooling and documentation support successful adoption across your organization.
