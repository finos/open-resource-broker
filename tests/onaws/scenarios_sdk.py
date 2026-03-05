"""SDK test scenarios configuration — timeout constants and feature flags."""

# Timeouts (seconds)
SDK_TIMEOUTS = {
    "request_completion": 600,  # 10 min — same as CLI tests
    "return_completion": 300,  # 5 min
    "poll_interval": 5,
}

# Feature flags — set to False to skip large parametrised suites during development
SDK_RUN_DEFAULT_COMBINATIONS = True
SDK_RUN_CUSTOM_CASES = False
