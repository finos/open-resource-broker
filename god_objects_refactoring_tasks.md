# God Objects Refactoring Tasks

## Task 1: Extract EC2Fleet Configuration Service
- [x] Create EC2FleetConfigurationService
- [x] Extract _prepare_template_context method
- [x] Extract _prepare_ec2fleet_specific_context method  
- [x] Extract _get_default_capacity_type method
- [x] Test configuration service

## Task 2: Extract EC2Fleet Management Service
- [ ] Create EC2FleetManagementService
- [ ] Extract _create_fleet_with_response method
- [ ] Extract _create_fleet_config method
- [ ] Extract _create_fleet_config_legacy method
- [ ] Extract allocation strategy methods
- [ ] Test management service

## Task 3: Extract EC2Fleet Status Service
- [ ] Create EC2FleetStatusService
- [ ] Extract check_hosts_status method
- [ ] Extract status monitoring logic
- [ ] Test status service

## Task 4: Extract EC2Fleet Cleanup Service
- [ ] Create EC2FleetCleanupService
- [ ] Extract release_hosts method
- [ ] Extract _release_hosts_for_single_ec2_fleet method
- [ ] Extract cleanup logic
- [ ] Test cleanup service

## Task 5: Extract EC2Fleet Response Service
- [ ] Create EC2FleetResponseService
- [ ] Extract _extract_instant_instance_ids method
- [ ] Extract _extract_fleet_errors method
- [ ] Extract _format_instance_data method
- [ ] Extract error handling logic
- [ ] Test response service

## Task 6: Refactor EC2FleetHandler
- [ ] Update EC2FleetHandler to use composed services
- [ ] Remove god object methods
- [ ] Update constructor with service dependencies
- [ ] Test refactored handler

## Task 7: Extract SpotFleet Services
- [ ] Apply same decomposition pattern to SpotFleetHandler
- [ ] Create SpotFleet*Service classes
- [ ] Extract methods by responsibility

## Task 8: Refactor SpotFleetHandler
- [ ] Update SpotFleetHandler to use composed services
- [ ] Remove god object methods
- [ ] Test refactored handler

## Task 9: Extract ASG Services
- [ ] Apply same decomposition pattern to ASGHandler
- [ ] Create ASG*Service classes
- [ ] Extract methods by responsibility

## Task 10: Refactor ASGHandler
- [ ] Update ASGHandler to use composed services
- [ ] Remove god object methods
- [ ] Test refactored handler

## Task 11: Remove Legacy God Objects
- [ ] Delete aws_provider_strategy_original.py
- [ ] Verify no references to legacy file
- [ ] Verify no god objects remain (>500 lines or >20 methods)

## Task 12: Integration Testing
- [ ] Test all handlers work with decomposed services
- [ ] Run full test suite
- [ ] Verify no regressions

## Task 13: Commit and Close
- [ ] Final commit with refactoring complete
- [ ] Close task: bd close 2pt