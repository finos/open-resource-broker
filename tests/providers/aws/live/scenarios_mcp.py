"""MCP test scenarios configuration — timeout constants and feature flags."""

# Timeouts (seconds)
MCP_TIMEOUTS = {
    "request_completion": 600,  # 10 min — same as CLI/SDK tests
    "return_completion": 300,  # 5 min
    "poll_interval": 5,
}

# Feature flags for parametrised test suites
MCP_RUN_DEFAULT_COMBINATIONS = True
