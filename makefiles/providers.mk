# Dynamic provider test targets
# Auto-discovers provider directories under tests/providers/*/
# Per-provider overrides live in optional tests/providers/<name>/testconf.mk

# @SECTION Provider Tests

# Auto-discover real providers: only directories under tests/providers/ that own
# a testconf.mk fragment. Shared helper dirs (base/, contract/, logs/) and
# __pycache__ have no testconf.mk and are excluded — matching print-providers
# below and the CI discovery in .github/workflows/shared-config.yml, so the
# generated per-provider targets and the aggregate roll-ups cover exactly the
# real providers (aws, k8s, …) and nothing else.
PROVIDERS := $(sort $(patsubst tests/providers/%/testconf.mk,%,$(wildcard tests/providers/*/testconf.mk)))

# Emit PROVIDERS as a JSON array for consumption by CI scripts.
# Only directories that have a testconf.mk fragment are considered real
# provider targets (shared helpers like base/ and contract/ are excluded).
print-providers:  ## Print discovered providers as a JSON array (used by CI discovery)
	@python3 -c "import os,json; print(json.dumps(sorted([d for d in os.listdir('tests/providers') if os.path.isdir(os.path.join('tests/providers',d)) and not d.startswith('__') and os.path.exists(os.path.join('tests/providers',d,'testconf.mk'))])))"

# Include per-provider override fragments (optional).
# Each fragment may define:
#   EXTRAS_<name>      — uv --extra flag value (default: <name>)
#   LIVE_GATE_<name>   — pytest flag to enable live tests (default: --run-<name>)
#   WORKERS_<name>     — pytest -n workers arg (default: -n $(PYTEST_WORKERS))
#                        Set to empty string to run serially with no -n flag.
-include $(wildcard tests/providers/*/testconf.mk)

# _workers_flag — resolve the correct -n flag for a provider.
# Usage: $(call _workers_flag,<provider-name>)
#
# Three cases:
#   WORKERS_<name> is undefined  → emit "-n $(PYTEST_WORKERS)"  (parallel, default)
#   WORKERS_<name> is set to ""  → emit nothing  (serial, no -n flag)
#   WORKERS_<name> is set to X   → emit "X"  (caller-supplied value, e.g. "-n 2")
define _workers_flag
$(if $(filter undefined,$(origin WORKERS_$(1))),-n $(PYTEST_WORKERS),$(WORKERS_$(1)))
endef

define _provider_targets
test-providers-$(1)-unit: dev-install  ## Run $(1) provider unit tests
	@if [ -d tests/providers/$(1)/unit ]; then \
	  uv run --no-sync pytest --no-cov -q -ra $(call _workers_flag,$(1)) tests/providers/$(1)/unit; \
	else \
	  echo "no unit tests for $(1)"; \
	fi

test-providers-$(1)-mocked: dev-install  ## Run $(1) provider mocked tests (in-process API mock)
	@if [ -d tests/providers/$(1)/mocked ]; then \
	  uv run --no-sync pytest --no-cov -q -ra $(call _workers_flag,$(1)) tests/providers/$(1)/mocked; \
	else \
	  echo "no mocked tests for $(1)"; \
	fi

test-providers-$(1)-contract: dev-install  ## Run $(1) provider contract tests
	@if [ -d tests/providers/$(1)/contract ]; then \
	  uv run --no-sync pytest --no-cov -q -ra $(call _workers_flag,$(1)) tests/providers/$(1)/contract; \
	else \
	  echo "no contract tests for $(1)"; \
	fi

# Live tests must sync the provider extra (--extra installs the cloud SDK).
# ORB_SKIP_UI_BUILD=1 prevents setup.py's build_ui.sh hook from firing during
# that extra-sync without stripping the --extra flag.
test-providers-$(1)-live: dev-install  ## Run $(1) provider live tests (real cloud / cluster)
	@if [ -d tests/providers/$(1)/live ]; then \
	  ORB_SKIP_UI_BUILD=1 uv run --extra $$(or $$(EXTRAS_$(1)),$(1)) pytest --no-cov -q -ra $(call _workers_flag,$(1)) $$(or $$(LIVE_GATE_$(1)),--run-$(1)) tests/providers/$(1)/live; \
	else \
	  echo "no live tests for $(1)"; \
	fi

test-providers-$(1): dev-install  ## Run all non-live $(1) provider tests
	@uv run --no-sync pytest --no-cov -q -ra $(call _workers_flag,$(1)) tests/providers/$(1) --ignore=tests/providers/$(1)/live

endef

$(foreach p,$(PROVIDERS),$(eval $(call _provider_targets,$(p))))

# Aggregate roll-ups over every discovered provider in $(PROVIDERS).
# Prereq-list form: each depends on the matching generated per-provider target,
# so `make test-providers-live` fans out to aws + k8s live suites (and any
# future provider) with no further edits.
test-providers-live: $(foreach p,$(PROVIDERS),test-providers-$(p)-live)  ## Run live tests for every provider (real cloud / cluster)

test-providers-all: $(foreach p,$(PROVIDERS),test-providers-$(p))  ## Run all non-live tests for every provider

.PHONY: test-providers-live test-providers-all
