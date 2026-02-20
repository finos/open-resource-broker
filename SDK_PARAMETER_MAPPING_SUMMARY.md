# SDK Parameter Mapping Implementation Summary

## Problem Solved
SDK parameter names didn't match CLI parameter names:
- CLI uses `--count`, SDK required `requested_count`
- CLI uses `--provider`, SDK had no documented `provider_name` parameter
- This created inconsistency between CLI and SDK interfaces

## Solution Implemented

### 1. Parameter Mapping Layer (`src/sdk/parameter_mapping.py`)
- **ParameterMapper** class that maps CLI-style names to CQRS names
- **Global mappings**: `count` → `requested_count`, `provider` → `provider_name`
- **Command-specific mappings**: Extensible for future needs
- **Smart mapping**: Only maps when target parameter exists in handler
- **Backward compatibility**: CQRS names still work, take precedence

### 2. SDK Discovery Integration (`src/sdk/discovery.py`)
- Updated `_create_query_method_cqrs()` to use parameter mapping
- Updated `_create_command_method_cqrs()` to use parameter mapping
- Enhanced error reporting with both original and mapped parameters
- Maintains all existing functionality

### 3. SDK Client Enhancement (`src/sdk/client.py`)
- Added `get_method_parameters()` method for parameter discovery
- Shows both CLI aliases and CQRS parameter names
- Fixed import issues for better type hints

### 4. Documentation Updates (`src/sdk/__init__.py`)
- Updated SDK documentation to explain parameter mapping
- Added examples showing both CLI and CQRS parameter styles
- Documented supported mappings and backward compatibility

### 5. Comprehensive Testing (`tests/sdk/test_parameter_mapping.py`)
- Tests for all mapping scenarios
- Backward compatibility verification
- Edge cases (precedence, missing parameters)
- Multiple mappings in single call

## Usage Examples

### Before (CQRS names only)
```python
async with orb() as client:
    request = await client.create_request(
        template_id="EC2FleetInstant",
        requested_count=5  # Had to use CQRS name
    )
```

### After (Both styles supported)
```python
async with orb() as client:
    # CLI-style (NEW)
    request1 = await client.create_request(
        template_id="EC2FleetInstant",
        count=5  # CLI name works now
    )
    
    # CQRS-style (Still works)
    request2 = await client.create_request(
        template_id="EC2FleetInstant", 
        requested_count=5  # Original name still works
    )
    
    # Parameter discovery
    params = client.get_method_parameters('create_request')
    # Returns: {'count': 'requested_count', 'requested_count': 'requested_count', ...}
```

## Key Benefits

1. **CLI Consistency**: SDK now accepts same parameter names as CLI
2. **Backward Compatibility**: Existing code using CQRS names continues to work
3. **No Breaking Changes**: All existing functionality preserved
4. **Extensible**: Easy to add more parameter mappings as needed
5. **Self-Documenting**: Parameter discovery shows available aliases
6. **Minimal Code**: Achieved with minimal, focused changes

## Supported Mappings

| CLI Name | CQRS Name | Description |
|----------|-----------|-------------|
| `count` | `requested_count` | Number of machines to request |
| `provider` | `provider_name` | Provider instance name |

## Files Modified

1. `src/sdk/parameter_mapping.py` - **NEW**: Core mapping logic
2. `src/sdk/discovery.py` - Updated to use parameter mapping
3. `src/sdk/client.py` - Added parameter discovery method
4. `src/sdk/__init__.py` - Updated documentation
5. `tests/sdk/test_parameter_mapping.py` - **NEW**: Comprehensive tests

## Testing Verification

All tests pass, confirming:
- ✅ CLI names map correctly to CQRS names
- ✅ CQRS names still work (backward compatibility)
- ✅ CQRS names take precedence when both provided
- ✅ No mapping when target parameter doesn't exist
- ✅ Multiple mappings work in single call
- ✅ Parameter discovery includes all aliases

## Implementation Notes

- **Zero Breaking Changes**: All existing SDK usage continues to work
- **Minimal Performance Impact**: Mapping only occurs during method calls
- **Type Safety**: Maintains all existing type checking and validation
- **Error Handling**: Enhanced error messages show both original and mapped parameters
- **Extensibility**: Easy to add new mappings by updating `GLOBAL_MAPPINGS` or command-specific mappings