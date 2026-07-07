# AWS provider test configuration fragment.
# Included by makefiles/providers.mk via -include $(wildcard tests/providers/*/testconf.mk)

# AWS live tests need FastAPI: they spawn ORB REST subprocesses.
EXTRAS_aws := api
# The live suite uses --live rather than --run-aws to gate execution.
LIVE_GATE_aws := --live
