# Template Format System

## Overview

The Open Host Factory Plugin implements a comprehensive format system that handles both field selection and field naming through a single, cohesive approach. This eliminates the previous dual-system complexity while maintaining full compatibility with IBM Symphony Host Factory requirements.

## Architecture

### Before: Dual System (Eliminated)
```
- Separate HF format methods + camelCase methods
- convert_to_hf_minimal() + convert_to_legacy()
- Duplicate logic and maintenance overhead
```

### After: Integrated System
```
- Single convert_templates() method
- Two orthogonal parameters control behavior
- Clean separation of concerns
```

## Core Method

```python
def convert_templates(
    templates: list[Template], 
    include_full_config: bool = False, 
    use_camel_case: bool = False
) -> Dict[str, Any]:
    """
    Template conversion method.

    Args:
        templates: List of Template domain objects
        include_full_config: If True, include all fields. If False, include only essential fields (HF minimal)
        use_camel_case: If True, use camelCase field names. If False, use snake_case
    """
```

## Field Selection Logic

### Minimal Fields (HF Compatible)
When `include_full_config=False`:
- **3 essential fields**: `template_id`, `max_instances`, `attributes`
- **HF attributes object**: Contains `type`, `ncpus`, `nram` with derived values
- **IBM Symphony compatible**: Meets Host Factory specification requirements

### Full Fields
When `include_full_config=True`:
- **20+ fields**: All available template configuration
- **Complete details**: Subnets, security groups, pricing, etc.
- **Debugging friendly**: Full visibility into template configuration

## Field Naming Logic

### snake_case (Default)
When `use_camel_case=False`:
- **Python convention**: `template_id`, `max_instances`, `security_group_ids`
- **Internal tools**: CLI, debugging, development
- **Consistent**: All fields follow Python naming conventions

### camelCase (Legacy)
When `use_camel_case=True`:
- **JavaScript convention**: `templateId`, `maxNumber`, `securityGroupIds`
- **IBM Symphony compatible**: Matches expected field names
- **API compatibility**: External system integration

## Hybrid Field Mapping

### Special Mappings
Business logic mappings that don't follow standard conversion:
```python
special_mappings = {
    'max_instances': 'maxNumber',    # Business logic
    'instance_type': 'vmType',       # Domain-specific naming
}
```

### Automatic Conversion
All other fields use automatic conversion:
```python
# snake_case -> camelCase
'template_id' -> 'templateId'
'security_group_ids' -> 'securityGroupIds'
'some_new_field' -> 'someNewField'  # Future fields automatically handled
```

### Benefits
- **Special cases handled**: Business logic preserved
- **Future-proof**: New fields automatically converted
- **No maintenance**: No manual mapping updates needed
- **Consistent**: No mixed case outputs

## CLI Flag Mapping

| CLI Flags | include_full_config | use_camel_case | Result |
|-----------|-------------------|----------------|---------|
| (default) | `False` | `False` | HF minimal, snake_case |
| `--long` | `True` | `False` | Full config, snake_case |
| `--legacy` | `False` | `True` | HF minimal, camelCase |
| `--legacy --long` | `True` | `True` | Full config, camelCase |

## Output Examples

### Default: HF Minimal, snake_case
```json
{
  "templates": [
    {
      "template_id": "TestTemplate",
      "max_instances": 2,
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "1"],
        "nram": ["Numeric", "1024"]
      }
    }
  ]
}
```

### --long: Full Config, snake_case
```json
{
  "templates": [
    {
      "template_id": "TestTemplate",
      "name": "TestTemplate",
      "description": null,
      "instance_type": "t2.micro",
      "image_id": "/aws/service/ami-amazon-linux-latest/...",
      "max_instances": 2,
      "subnet_ids": ["subnet-123"],
      "security_group_ids": ["sg-123"],
      // ... 20+ fields total
    }
  ]
}
```

### --legacy: HF Minimal, camelCase
```json
{
  "templates": [
    {
      "templateId": "TestTemplate",
      "maxNumber": 2,
      "attributes": {
        "type": ["String", "X86_64"],
        "ncpus": ["Numeric", "1"],
        "nram": ["Numeric", "1024"]
      }
    }
  ]
}
```

### --legacy --long: Full Config, camelCase
```json
{
  "templates": [
    {
      "templateId": "TestTemplate",
      "name": "TestTemplate",
      "description": null,
      "vmType": "t2.micro",
      "imageId": "/aws/service/ami-amazon-linux-latest/...",
      "maxNumber": 2,
      "subnetIds": ["subnet-123"],
      "securityGroupIds": ["sg-123"],
      // ... 20+ fields total
    }
  ]
}
```

## Implementation Benefits

### Code Quality
- **DRY Principle**: No duplicate conversion logic
- **Single Responsibility**: One method handles all conversions
- **Maintainability**: New fields automatically handled
- **Testability**: Single method to test all scenarios

### User Experience
- **Consistent**: Predictable behavior across all combinations
- **Flexible**: All 4 combinations supported
- **Compatible**: IBM Symphony Host Factory compliant
- **Future-proof**: New fields automatically converted

### Architecture
- **Clean**: Clear separation of concerns
- **Extensible**: Easy to add new format options
- **Maintainable**: Single source of truth for conversions
- **Robust**: Comprehensive error handling and validation
