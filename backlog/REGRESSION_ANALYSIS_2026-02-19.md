# Regression Analysis: Last 2 Days (Feb 17-19, 2026)

**Analysis Date**: 2026-02-19  
**Branch**: feat/unified-registry-dependencies  
**Analyst**: orb-architect

## Executive Summary

### Statistics
- **Total Commits**: 69 commits
- **Files Changed**: 152 files
- **Lines Added**: 3,361 insertions
- **Lines Removed**: 2,669 deletions
- **Major Refactorings**: 8 large-scale changes
- **Critical Fixes**: 12 high-priority bug fixes
- **Deleted Files**: 15 files (mostly test files and legacy code)

### Confirmed Regressions Found
1. **Bug gpv** (P0): CLI routing regression - scheduler formatting broken
2. **Bug f6z** (P1): Validation error - machine_ids contains None values

### Status
- ✅ All confirmed regressions fixed
- ⚠️ Multiple high-risk areas identified
- 📋 Comprehensive testing recommended

---

## Section 1: Confirmed Regressions

### 1.1 Bug gpv: CLI Routing Regression (P0) ✅ FIXED

**Description**: Request status returned generic format instead of scheduler-specific (HostFactory) format

**Root Cause**: CLI factory refactoring (commit 96446099) broke scheduler-based formatting
- Orchestrator created CQRS queries for `requests status`
- CQRS queries bypassed scheduler formatting entirely
- Output went directly to CLI formatter (generic format)
- Scheduler-aware handlers never called

**Impact**: HIGH - IBM Symphony integration depends on specific field formats

**Fix**: Modified orchestrator to return None for requests status, triggering fallback to scheduler-aware handlers

**Commit**: b776b373

**Evidence**:
- Expected: `{"requests": [{"requestId": "...", "machines": [...]}]}` (camelCase)
- Actual: `{"requests": [{"request_id": "...", "machine_references": [...]}]}` (snake_case)

---

### 1.2 Bug f6z: machine_ids Validation Error (P1) ✅ FIXED

**Description**: Request status/list failed with validation error about machine_ids containing None values

**Root Cause**: `PopulateMachineIdsHandler._discover_machine_ids()` didn't filter None values
- `instance.get("instance_id")` returned None when key didn't exist
- None values added to machine_ids array
- Pydantic validation failed (expected list[str])

**Impact**: MEDIUM - Request status and list commands completely broken

**Fix**: Added filter condition to list comprehension

**Commit**: bca709cc

**Code Change**:
```python
# Before:
return [instance.get("instance_id") for instance in result.data["instances"]]

# After:
return [
    instance.get("instance_id")
    for instance in result.data["instances"]
    if instance.get("instance_id")
]
```

---

## Section 2: High-Risk Changes

### 2.1 CLI Factory Refactoring (CRITICAL RISK)

**Commit**: 96446099  
**Date**: 2026-02-18  
**Risk Level**: HIGH

**Change**: Split 1,205-line CLI factory into orchestrator + 8 specialized factories

**Files Affected**:
- `src/cli/factories/cli_command_factory_orchestrator.py` (new)
- `src/cli/factories/request_command_factory.py` (new)
- `src/cli/factories/machine_command_factory.py` (new)
- `src/cli/factories/template_command_factory.py` (new)
- `src/cli/factories/provider_command_factory.py` (new)
- `src/cli/factories/system_command_factory.py` (new)
- `src/cli/factories/config_command_factory.py` (new)
- `src/cli/factories/storage_command_factory.py` (new)
- `src/cli/factories/scheduler_command_factory.py` (new)

**Why High-Risk**:
- Complex routing logic split across multiple files
- Command routing may have subtle bugs
- Integration points may be broken
- Fallback logic may not work correctly

**Confirmed Issues**:
- ✅ Scheduler formatting bypass (Bug gpv) - FIXED
- ✅ machine_count argument routing (Bug jm1) - FIXED

**Potential Issues**:
- Other command routing bugs
- Argument extraction issues
- Factory selection logic
- CQRS vs handler routing

**Testing Required**:
- [ ] All CLI commands with various parameter combinations
- [ ] Positional vs flag arguments
- [ ] Scheduler override (--scheduler flag)
- [ ] Provider override (--provider flag)
- [ ] All output formats (json, yaml, table, list)

---

### 2.2 Provider Selection Service Removal (HIGH RISK)

**Commits**: e4722f8f, b5b85e5a  
**Date**: 2026-02-18  
**Risk Level**: HIGH

**Change**: Removed ProviderSelectionService from domain layer, moved logic to ProviderRegistry (infrastructure layer)

**Reason**: Circular dependency resolution

**Files Affected**:
- `src/providers/registry.py` (provider selection logic added)
- `src/domain/services/provider_selection_service.py` (removed)

**Why High-Risk**:
- Architectural change affecting core functionality
- Provider selection behavior may have changed
- Integration points may be affected
- Selection strategy may behave differently

**Testing Required**:
- [ ] Provider selection with all 5 strategies
- [ ] CLI override (--provider flag)
- [ ] Template-specific provider selection
- [ ] Multi-provider template generation
- [ ] Load balancing across providers

---

### 2.3 Registry Pattern Migration (HIGH RISK)

**Commits**: 11c1a975, c2777854, 31f54f65  
**Date**: 2026-02-18  
**Risk Level**: HIGH

**Change**: Complete registry pattern migration to BaseRegistry

**Files Affected**:
- `src/infrastructure/registry/base_registry.py`
- `src/infrastructure/registry/registry_factory.py`
- `src/providers/registry.py`
- `src/infrastructure/storage/registry.py`
- `src/infrastructure/scheduler/registry.py`

**Why High-Risk**:
- Registry initialization may be affected
- Dependency injection may have issues
- Lazy loading may cause problems
- Service resolution order may change

**Testing Required**:
- [ ] Application bootstrap
- [ ] Service resolution
- [ ] Registry initialization
- [ ] Lazy loading behavior
- [ ] Circular dependency prevention

---

### 2.4 CQRS Compliance Fixes (MEDIUM RISK)

**Commits**: cc8e4fd5, b040c2c8, bc6aaccd  
**Date**: 2026-02-18  
**Risk Level**: MEDIUM

**Change**: Query handlers no longer modify state

**Files Affected**:
- `src/application/queries/handlers.py`
- `src/application/queries/bulk_handlers.py`

**Why Medium-Risk**:
- State synchronization logic may be affected
- Query results may be stale
- Side effects may be missing

**Testing Required**:
- [ ] Request status synchronization
- [ ] Machine status updates
- [ ] Template cache refresh
- [ ] Provider health checks

---

### 2.5 Circular Dependency Resolution (MEDIUM RISK)

**Commits**: f3a58620, d7f4dfe3, b992a789  
**Date**: 2026-02-18  
**Risk Level**: MEDIUM

**Change**: Lazy initialization and dependency injection changes

**Files Affected**:
- Multiple files across all layers

**Why Medium-Risk**:
- Service initialization order may cause issues
- Lazy loading may fail
- Dependency resolution may break

**Testing Required**:
- [ ] Application startup
- [ ] Service initialization
- [ ] Dependency resolution
- [ ] Error handling during init

---

## Section 3: Potential Regressions (Need Testing)

### 3.1 Template Generation Field Mapping (MEDIUM PRIORITY)

**Status**: Fixed but risky  
**Fix Commit**: cccaa8f6  
**Risk**: Field mapping logic may have other initialization issues

**What Changed**: Moved `_field_mapper` initialization inside `__init__`

**Why Risky**: Template generation was completely broken, fix may not cover all cases

**Testing Required**:
- [ ] Template generation for all provider APIs (EC2Fleet, SpotFleet, ASG, RunInstances)
- [ ] Template generation with --all-providers
- [ ] Template generation with --provider override
- [ ] Field mapping for all template types
- [ ] SSM parameter resolution

---

### 3.2 SSM Parameter Resolution (MEDIUM PRIORITY)

**Status**: Fixed but risky  
**Fix Commit**: 8704a2bc  
**Risk**: Other AWS service integrations may have similar issues

**What Changed**: Fixed method calls in AWS image resolution service

**Why Risky**: AWS client interface changes may affect other services

**Testing Required**:
- [ ] Template generation with SSM parameters
- [ ] AMI ID resolution
- [ ] Multi-region SSM resolution
- [ ] SSM parameter caching
- [ ] Error handling for invalid SSM parameters

---

### 3.3 CLI Command Routing (HIGH PRIORITY)

**Status**: Partially fixed  
**Fix Commits**: 2cb19feb, 58bd73b2, b776b373  
**Risk**: Complex routing logic may have edge cases

**What Changed**: Implemented 16 broken CLI commands, fixed argument routing

**Why Risky**: Multiple fixes suggest systemic issues

**Testing Required**:
- [ ] All 16 previously broken commands
- [ ] All argument patterns (positional, flags, mixed)
- [ ] All resource types (templates, machines, requests, providers)
- [ ] All actions (list, show, create, update, delete, status)
- [ ] All overrides (--provider, --scheduler, --region, --profile)

---

### 3.4 Error Handling Refactoring (LOW PRIORITY)

**Status**: Refactored  
**Commits**: a8e944c3, 31f54f65, 00b2e690  
**Risk**: Error propagation and handling may be affected

**What Changed**: Exception type mapping and HTTP error handling extracted

**Why Risky**: Error handling is cross-cutting concern

**Testing Required**:
- [ ] Error messages are correct
- [ ] HTTP status codes are correct
- [ ] Exception types are mapped correctly
- [ ] Error context is preserved
- [ ] Stack traces are available in debug mode

---

## Section 4: Testing Recommendations

### Priority 1: CRITICAL (Test Immediately)

- [ ] **IBM Symphony HostFactory Integration**
  - Request status format (camelCase, machines field)
  - Machine provisioning workflow
  - Return request workflow
  - Template generation
  - Field mapping accuracy

- [ ] **CLI Command Routing**
  - All resource commands (templates, machines, requests, providers)
  - All actions (list, show, create, update, delete, status)
  - Positional vs flag arguments
  - Scheduler override (--scheduler)
  - Provider override (--provider)

- [ ] **AWS Provider Functionality**
  - EC2Fleet (instant and request)
  - SpotFleet
  - Auto Scaling Groups
  - RunInstances
  - SSM parameter resolution
  - Multi-region support

### Priority 2: HIGH (Test Soon)

- [ ] **Provider Selection Logic**
  - 5-strategy hierarchy
  - CLI override
  - Template-specific selection
  - Multi-provider support
  - Load balancing

- [ ] **Template Generation**
  - All provider APIs
  - --all-providers flag
  - --provider override
  - Field mapping
  - SSM resolution

- [ ] **Request Management**
  - Create requests
  - Status queries
  - Return requests
  - Machine association
  - State synchronization

### Priority 3: MEDIUM (Test When Possible)

- [ ] **Registry Pattern**
  - Service resolution
  - Lazy loading
  - Circular dependency prevention
  - Initialization order

- [ ] **CQRS Compliance**
  - Query handlers don't modify state
  - State synchronization
  - Side effects

- [ ] **Error Handling**
  - Exception mapping
  - HTTP error responses
  - Error context preservation

### Priority 4: LOW (Test Eventually)

- [ ] **Storage Strategies**
  - JSON storage
  - DynamoDB storage
  - SQL storage
  - Migration between strategies

- [ ] **Scheduler Strategies**
  - HostFactory scheduler
  - Default scheduler
  - Strategy switching

---

## Section 5: Architectural Impact

### 5.1 Layer Boundary Changes

**Provider Selection Service Removal**:
- **Before**: Domain service in domain layer
- **After**: Logic in ProviderRegistry (infrastructure layer)
- **Impact**: Cleaner architecture, no circular dependencies
- **Risk**: Provider selection behavior may differ

**Registry Pattern Migration**:
- **Before**: Custom registry implementations
- **After**: BaseRegistry pattern with RegistryFactory
- **Impact**: Consistent registry behavior, better DI
- **Risk**: Initialization order, lazy loading issues

### 5.2 Pattern Changes

**CQRS Compliance**:
- **Before**: Query handlers could modify state
- **After**: Strict CQRS - queries read-only
- **Impact**: Better separation of concerns
- **Risk**: State synchronization, stale data

**CLI Factory Pattern**:
- **Before**: Single 1,205-line factory
- **After**: Orchestrator + 8 specialized factories
- **Impact**: Better separation, easier maintenance
- **Risk**: Complex routing, integration issues

### 5.3 Dependency Changes

**Circular Dependency Resolution**:
- **Before**: Multiple circular dependencies
- **After**: Lazy initialization, dependency injection
- **Impact**: Cleaner dependency graph
- **Risk**: Initialization failures, lazy loading issues

---

## Section 6: Detailed Commit List

### Major Refactorings (8 commits)

| Commit | Date | Title | Risk |
|--------|------|-------|------|
| 96446099 | 2026-02-18 | CLI factory split into focused factories | HIGH |
| 11c1a975 | 2026-02-18 | Complete registry pattern migration | HIGH |
| c2777854 | 2026-02-18 | Registry/DI separation | HIGH |
| 31f54f65 | 2026-02-18 | Remove safe duplicates (Phase 2) | MEDIUM |
| a8e944c3 | 2026-02-18 | Error handling extraction | MEDIUM |
| 00b2e690 | 2026-02-18 | Exception type mapper | MEDIUM |
| e4722f8f | 2026-02-18 | Provider selection to registry | HIGH |
| b5b85e5a | 2026-02-18 | Provider selection logic moved | HIGH |

### Critical Fixes (12 commits)

| Commit | Date | Title | Priority |
|--------|------|-------|----------|
| cccaa8f6 | 2026-02-18 | Fix template generation _field_mapper | P0 |
| 8704a2bc | 2026-02-19 | Fix SSM to AMI ID resolution | P1 |
| 2cb19feb | 2026-02-18 | Implement 16 broken CLI commands | P0 |
| 58bd73b2 | 2026-02-19 | Fix machines request missing requested_count | P1 |
| 90093a73 | 2026-02-19 | Fix singular command aliases | P2 |
| b776b373 | 2026-02-19 | Fix CLI routing for scheduler formatting | P0 |
| bca709cc | 2026-02-19 | Filter None values from machine_ids | P1 |
| 990d00ba | 2026-02-18 | Provider registry configuration access | P1 |
| bbe3345c | 2026-02-18 | Fix ListMachinesQuery handler | P2 |
| ab0fc612 | 2026-02-18 | Fix requests list --limit parameter | P2 |
| 23e4a8f1 | 2026-02-18 | Implement --long flag for templates list | P3 |
| c9a3f5a5 | 2026-02-19 | Remove debug statement | P4 |

### Architectural Changes (6 commits)

| Commit | Date | Title | Impact |
|--------|------|-------|--------|
| e4722f8f | 2026-02-18 | Provider selection service removal | HIGH |
| 11c1a975 | 2026-02-18 | Registry pattern migration | HIGH |
| cc8e4fd5 | 2026-02-18 | CQRS compliance fixes | MEDIUM |
| f3a58620 | 2026-02-18 | Circular dependency resolution | MEDIUM |
| d7f4dfe3 | 2026-02-18 | Lazy initialization | MEDIUM |
| b992a789 | 2026-02-18 | Dependency injection changes | MEDIUM |

---

## Section 7: Recommendations

### Immediate Actions

1. **Run Full Test Suite**
   - Unit tests
   - Integration tests
   - System tests
   - Performance tests

2. **Manual Testing**
   - IBM Symphony HostFactory integration
   - All CLI commands
   - AWS provider functionality
   - Multi-provider scenarios

3. **Monitoring**
   - Watch for error rates
   - Monitor performance
   - Check logs for warnings
   - Track user reports

### Short-Term Actions

1. **Regression Test Suite**
   - Create automated tests for confirmed regressions
   - Add tests for high-risk areas
   - Expand CLI command coverage

2. **Documentation**
   - Update architecture docs
   - Document breaking changes
   - Update CLI examples
   - Add troubleshooting guide

3. **Code Review**
   - Review high-risk changes
   - Check for similar patterns
   - Validate error handling
   - Verify test coverage

### Long-Term Actions

1. **Architecture Validation**
   - Verify layer boundaries
   - Check dependency graph
   - Validate patterns
   - Review design decisions

2. **Technical Debt**
   - Address remaining duplications
   - Improve test coverage
   - Refactor complex code
   - Document edge cases

3. **Process Improvement**
   - Add pre-commit checks
   - Improve CI/CD pipeline
   - Enhance code review process
   - Better regression testing

---

## Section 8: Conclusion

### Summary

The last 2 days saw extensive refactoring and bug fixes:
- **69 commits** with significant architectural changes
- **2 confirmed regressions** found and fixed
- **Multiple high-risk areas** identified
- **Comprehensive testing** recommended

### Risk Assessment

**Overall Risk**: MEDIUM-HIGH
- Major refactorings introduce regression risk
- Critical bugs were found and fixed
- High-risk areas need careful monitoring
- Comprehensive testing required

### Next Steps

1. ✅ Fix all confirmed regressions (COMPLETE)
2. 📋 Run comprehensive testing (IN PROGRESS)
3. 📋 Monitor high-risk areas (ONGOING)
4. 📋 Update documentation (PENDING)
5. 📋 Create regression test suite (PENDING)

---

**Document Version**: 1.0  
**Last Updated**: 2026-02-19  
**Status**: COMPLETE
