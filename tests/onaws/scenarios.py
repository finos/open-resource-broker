def get_test_cases():
    return [
        # {
        #     "test_name": "EC2FleetInstant",
        #     "capacity_to_request": 2,
        #     "overrides": {
        #         "fleetType":"instant"
        #     }
        # },
        {
            "test_name": "EC2FleetRequest",
            "capacity_to_request": 2,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {"fleetType": "request"},
        },
        {
            "test_name": "EC2FleetInstant",
            "capacity_to_request": 2,
            "awsprov_base_template": "awsprov_templates.base.json",
            "overrides": {"fleetType": "instant"},
        },
        # {
        #     "test_name": "EC2FleetRequest",
        #     "capacity_to_request": 2
        # }
        # ,
        # {
        #     "test_name": "EC2FleetMaintain",
        #     "capacity_to_request": 2
        # },
        # {
        #     "test_name": "SpotFleet",
        #     "capacity_to_request": 2
        # },
        # {
        #     "test_name": "SpotFleetRequest",
        #     "capacity_to_request": 2
        # },
        # {
        #     "test_name": "SpotFleetMaintain",
        #     "capacity_to_request": 2
        # },
        # {
        #     "test_name": "ASG",
        #     "capacity_to_request": 2
        # }
    ]


def get_test_case_by_name(test_name: str):
    """Get a specific test case by test name."""
    test_cases = get_test_cases()
    for test_case in test_cases:
        if test_case["test_name"] == test_name:
            return test_case

    # Return a default test case if not found
    return {"test_name": test_name, "capacity_to_request": 2, "overrides": {}}
