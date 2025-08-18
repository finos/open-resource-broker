# Field Mapping Architecture

## Overview

This document describes the scheduler-aware field mapping architecture implemented to handle the mapping between HostFactory template fields and internal domain model fields, with provider-specific extensions.

## Problem Statement

The original implementation had several issues:
1. **Field Inheritance Issues**: AWSTemplate was shadowing inherited fields from CoreTemplate
2. **Missing Field Mappings**: The scheduler wasn't mapping `vmTypes` → `instance_types`
3. **Handler Field Access Errors**: Handlers were trying to access fields that didn't exist
4. **Inconsistent Field Names**: Different layers used different field naming conventions

## Solution Architecture

### 1. Scheduler-Aware Field Mapping Registry

**Location**: `src/infrastructure/scheduler/field_mappings.py`

The `SchedulerProviderFieldMappings` class provides a centralized registry for mapping scheduler-specific field names to internal domain model fields, with provider-aware conditional mapping.

```python
class SchedulerProviderFieldMappings:
    MAPPINGS = {
        'hostfactory': {
            'generic': {
                'templateId': 'template_id',
                'vmType': 'instance_type',
                'vmTypes': 'instance_types',
                # ... other generic fields
            },
            'aws': {
                'vmTypesOnDemand': 'instance_types_ondemand',
                'percentOnDemand': 'percent_on_demand',
                'fleetRole': 'fleet_role',
                # ... other AWS-specific fields
            }
        }
    }
```

**Key Features**:
- **Generic Fields**: Work across all providers (e.g., `vmType`, `imageId`)
- **Provider-Specific Fields**: Only mapped when the provider is active (e.g., `vmTypesOnDemand` for AWS)
- **Extensible**: Easy to add new schedulers and providers

### 2. Improved Scheduler Strategy

**Location**: `src/infrastructure/scheduler/strategies/symphony_hostfactory.py`

The scheduler strategy now uses the field mapping registry to perform provider-aware field mapping:

```python
def _map_template_fields(self, template: Dict[str, Any]) -> Dict[str, Any]:
    # Get active provider type
    provider_type = self._get_active_provider_type()

    # Get field mappings for HostFactory + active provider
    field_mappings = SchedulerProviderFieldMappings.get_mappings(
        scheduler_type='hostfactory',
        provider_type=provider_type
    )

    # Apply registry-based field mappings
    mapped = {}
    for hf_field, internal_field in field_mappings.items():
        if hf_field in template:
            mapped[internal_field] = template[hf_field]

    # Apply field transformations
    mapped = FieldTransformationUtils.apply_field_transformations(mapped)

    return mapped
```

### 3. Field Transformation Utilities

**Location**: `src/infrastructure/scheduler/field_mappings.py`

The `FieldTransformationUtils` class handles complex field transformations:

- **Subnet ID Transformation**: Converts single subnet to list format
- **Instance Tags Transformation**: Parses HostFactory tag string format (`"key1=value1;key2=value2"`)
- **Instance Type Consistency**: Ensures `instance_type` is set when `instance_types` is provided

### 4. Fixed AWSTemplate Field Inheritance

**Location**: `src/providers/aws/domain/template/aggregate.py`

The AWSTemplate now properly inherits fields from CoreTemplate:

```python
class AWSTemplate(CoreTemplate):
    # AWS-specific extensions only
    instance_types_ondemand: Optional[Dict[str, int]] = None
    instance_types_priority: Optional[Dict[str, int]] = None
    percent_on_demand: Optional[int] = None
    # ... other AWS-specific fields

    # Note: instance_type and instance_types are inherited from CoreTemplate
    # No need to redefine them here - this was causing the field access issues
```

### 5. Updated AWS Handlers

**Locations**: 
- `src/providers/aws/infrastructure/handlers/run_instances_handler.py`
- `src/providers/aws/infrastructure/handlers/spot_fleet_handler.py`
- `src/providers/aws/infrastructure/handlers/ec2_fleet_handler.py`

All handlers now use the correct inherited field names:

```python
# Before (causing errors)
if aws_template.vm_type:  # Field doesn't exist!
    params['InstanceType'] = aws_template.vm_type

# After (working correctly)
if aws_template.instance_type:  # Inherited from CoreTemplate
    params['InstanceType'] = aws_template.instance_type
```

## Field Mapping Examples

### OnDemand Template

**HostFactory JSON**:
```json
{
  "templateId": "OnDemand-Template",
  "vmType": "t2.micro",
  "imageId": "ami-12345678",
  "subnetId": "subnet-abcd1234",
  "priceType": "ondemand"
}
```

**Mapped Internal Fields**:
```python
{
  "template_id": "OnDemand-Template",
  "instance_type": "t2.micro",
  "image_id": "ami-12345678",
  "subnet_ids": ["subnet-abcd1234"],  # Transformed to list
  "price_type": "ondemand"
}
```

### Spot Template

**HostFactory JSON**:
```json
{
  "templateId": "Spot-Template",
  "vmTypes": {"t2.medium": 1, "t3.medium": 2},
  "priceType": "spot",
  "fleetRole": "arn:aws:iam::123456789012:role/spot-fleet-role"
}
```

**Mapped Internal Fields**:
```python
{
  "template_id": "Spot-Template",
  "instance_types": {"t2.medium": 1, "t3.medium": 2},
  "instance_type": "t2.medium",  # Auto-set from first instance_types entry
  "price_type": "spot",
  "fleet_role": "arn:aws:iam::123456789012:role/spot-fleet-role"
}
```

### Heterogeneous Template

**HostFactory JSON**:
```json
{
  "templateId": "Hetero-Template",
  "vmTypes": {"t2.medium": 1, "t3.large": 2},
  "vmTypesOnDemand": {"t2.medium": 1},
  "priceType": "heterogeneous",
  "percentOnDemand": 30,
  "allocationStrategyOnDemand": "prioritized"
}
```

**Mapped Internal Fields**:
```python
{
  "template_id": "Hetero-Template",
  "instance_types": {"t2.medium": 1, "t3.large": 2},
  "instance_types_ondemand": {"t2.medium": 1},
  "instance_type": "t2.medium",
  "price_type": "heterogeneous",
  "percent_on_demand": 30,
  "allocation_strategy_ondemand": "prioritized"
}
```

## Supported HostFactory Fields

### Generic Fields (All Providers)
- `templateId` → `template_id`
- `maxNumber` → `max_instances`
- `imageId` → `image_id`
- `vmType` → `instance_type`
- `vmTypes` → `instance_types`
- `subnetId` → `subnet_ids`
- `securityGroupIds` → `security_group_ids`
- `priceType` → `price_type`
- `maxSpotPrice` → `max_price`
- `allocationStrategy` → `allocation_strategy`
- `keyName` → `key_name`
- `instanceTags` → `tags`
- `rootDeviceVolumeSize` → `root_volume_size`
- `volumeType` → `root_volume_type`
- `iops` → `root_volume_iops`

### AWS-Specific Fields
- `vmTypesOnDemand` → `instance_types_ondemand`
- `vmTypesPriority` → `instance_types_priority`
- `percentOnDemand` → `percent_on_demand`
- `allocationStrategyOnDemand` → `allocation_strategy_ondemand`
- `fleetRole` → `fleet_role`
- `spotFleetRequestExpiry` → `spot_fleet_request_expiry`
- `poolsCount` → `pools_count`
- `launchTemplateId` → `launch_template_id`
- `instanceProfile` → `instance_profile`
- `userDataScript` → `user_data`

## Benefits

1. **Single Source of Truth**: All field mappings are centralized in one registry
2. **Provider Awareness**: Only maps provider-specific fields when the provider is active
3. **Extensible**: Easy to add new schedulers (LSF, SLURM) and providers (Azure, GCP)
4. **Type Safety**: Appropriate field inheritance ensures handlers can access required fields
5. **Maintainable**: Clear separation between scheduler-specific and provider-specific concerns

## Future Extensions

### Adding New Schedulers

To add support for LSF scheduler:

```python
MAPPINGS = {
    'hostfactory': { ... },
    'lsf': {
        'generic': {
            'TEMPLATE': 'template_id',
            'INSTANCE_TYPE': 'instance_type',
            'IMAGE_ID': 'image_id',
        },
        'aws': {
            'SPOT_FLEET_ROLE': 'fleet_role',
            'ON_DEMAND_PERCENT': 'percent_on_demand'
        }
    }
}
```

### Adding New Providers

To add support for Azure provider:

```python
MAPPINGS = {
    'hostfactory': {
        'generic': { ... },
        'aws': { ... },
        'azure': {
            'vmPriority': 'vm_priority',
            'spotEvictionPolicy': 'spot_eviction_policy',
            'proximityPlacementGroup': 'proximity_placement_group'
        }
    }
}
```

## Testing

The implementation includes comprehensive tests:

- **Unit Tests**: `tests/test_field_mapping_integration.py`
- **Standalone Tests**: `test_field_mapping_standalone.py`

Run tests with:
```bash
python3 test_field_mapping_standalone.py
```

## Migration Guide

### For Existing Templates

No changes required - the new field mapping is backward compatible.

### For New Development

1. Use the field mapping registry for any new scheduler integrations
2. Add provider-specific fields to the appropriate provider section
3. Ensure appropriate field inheritance in provider-specific template classes
4. Update handlers to use inherited field names

## Troubleshooting

### Common Issues

1. **Field Not Found Error**: Check if the field is properly mapped in the registry
2. **Provider-Specific Field Missing**: Ensure the provider type is correctly detected
3. **Field Transformation Issues**: Verify the transformation utilities are applied

### Debug Tips

1. Enable debug logging to see field mapping details
2. Check the active provider type detection
3. Verify field transformations are applied correctly
