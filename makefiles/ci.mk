# CI/CD targets that match GitHub Actions workflows exactly

# @SECTION CI Quality Checks
# Individual code quality targets (with tool names)
ci-quality-ruff:  ## Run Ruff formatting and linting check (basic rules only)
	@# In CI the venv is pre-populated by setup-uv-cached (group: lint).
	@# Local fresh-checkout: run `make dev-install` first.
	@echo "Running Ruff formatting and linting check (basic rules only)..."
	@uv run --no-sync ruff check --select W,F,I --ignore E501 --quiet .
	@uv run --no-sync ruff format --check --quiet .

ci-quality-ruff-optional:  ## Run Ruff extended linting (warnings only)
	@echo "Running Ruff extended linting..."
	uv run --no-sync ruff check --select=E501,N,UP,B,PL,C90,RUF . || true

ci-quality-radon:  ## Run radon complexity analysis
	@echo "Running radon complexity analysis..."
	$(call run-tool,radon,cc $(PACKAGE) --min B --show-complexity)
	$(call run-tool,radon,mi $(PACKAGE) --min B)

ci-quality-pyright:  ## Run pyright type checking
	@echo "Running pyright type check..."
	$(call run-tool,pyright,)

# Composite target (for local convenience)
ci-quality: ci-quality-ruff ci-quality-pyright  ## Run all enforced code quality checks

ci-quality-full: ci-quality-ruff ci-quality-ruff-optional ci-quality-pyright  ## Run all code quality checks including optional

# Individual architecture quality targets (with tool names)
ci-arch-cqrs:  ## Run CQRS pattern validation
	@echo "Running CQRS pattern validation..."
	uv run --no-sync python ./dev-tools/quality/validate_cqrs.py

ci-arch-clean:  ## Run Clean Architecture dependency validation
	@echo "Running Clean Architecture validation..."
	uv run --no-sync python ./dev-tools/quality/check_architecture.py

ci-arch-imports:  ## Run import validation
	@# In CI the venv is pre-populated by setup-uv-cached (group: arch).
	@# Local fresh-checkout: run `make dev-install` first.
	@echo "Running import validation..."
	uv run --no-sync python ./dev-tools/quality/validate_imports.py

ci-arch-file-sizes:  ## Check file size compliance
	@echo "Running file size checks..."
	uv run --no-sync python ./dev-tools/quality/dev_tools_runner.py check-file-sizes --warn-only

ci-arch-lint-imports:  ## Run import-linter layer-boundary contracts
	@# In CI the venv is pre-populated by setup-uv-cached (group: arch).
	@# Local fresh-checkout: run `make dev-install` first.
	@echo "Running import-linter layer-boundary checks..."
	$(call run-tool,lint-imports,)

# Composite target
ci-architecture: ci-arch-cqrs ci-arch-clean ci-arch-imports ci-arch-file-sizes ci-arch-lint-imports  ## Run all architecture checks

# Individual security targets (with tool names)
ci-security-bandit:  ## Run Bandit security scan
	@./dev-tools/ci/ci_security_dispatcher.py bandit

ci-security-safety:  ## Run Safety dependency scan
	@./dev-tools/ci/ci_security_dispatcher.py safety

ci-security-trivy: dev-install  ## Run Trivy container scan
	@./dev-tools/ci/ci_security_dispatcher.py trivy

ci-security-hadolint: dev-install  ## Run Hadolint Dockerfile scan
	@./dev-tools/ci/ci_security_dispatcher.py hadolint

ci-security-semgrep: dev-install  ## Run Semgrep static analysis
	@./dev-tools/ci/ci_security_dispatcher.py semgrep

ci-security-trivy-fs: dev-install  ## Run Trivy filesystem scan
	@./dev-tools/ci/ci_security_dispatcher.py trivy-fs

ci-security-trufflehog: dev-install  ## Run TruffleHog secrets scan
	@./dev-tools/ci/ci_security_dispatcher.py trufflehog

ci-security-container: dev-install  ## Run container security scans (Trivy image + Hadolint)
	@./dev-tools/security/security_container.py

# Composite target
ci-security: ci-security-bandit ci-security-safety ci-security-semgrep ci-security-trivy-fs ci-security-trufflehog  ## Run all security scans

ci-build-sbom:  ## Generate SBOM files (matches publish.yml workflow)
	@echo "Generating SBOM files for CI..."
	@echo "This matches the GitHub Actions publish.yml workflow exactly"
	$(MAKE) sbom-generate

# pytest-xdist parallelisation.
#
# Two variants are provided:
#
#   PYTEST_PARALLEL_LOCAL  — used by the local ``make test`` targets.
#     ``-n auto`` spawns one worker per CPU core, which is ideal on developer
#     machines that typically have 8-16 cores.
#
#   PYTEST_PARALLEL_CI  — used by CI targets (ci-tests-*).
#     GitHub-hosted runners expose exactly 2 vCPUs.  ``-n auto`` therefore
#     spawns 2 workers, but the xdist scheduler introduces coordination
#     overhead that can make single-worker runs *slower* on 2-core hosts.
#     Using ``-n 2`` is explicit and avoids surprises if the runner class
#     changes.  ``--dist=loadscope`` keeps tests in the same class/module on
#     one worker so class-scoped setUp / fixtures don't re-run per worker.
#
#   PYTEST_PARALLEL (kept for backward compat) — points at the CI variant so
#     any external callers that reference the old variable name still work.
#
# Tests that share global state (live AWS, docker daemon, e2e tempdirs) are
# tagged ``serial`` and run sequentially via a second pytest pass (PYTEST_SERIAL).
# PYTEST_N_WORKERS defaults to 2 (GitHub ubuntu-latest = 2 vCPU).  Larger-runner
# CI legs override it (e.g. PYTEST_N_WORKERS=auto on ubuntu-latest-16-cores) to
# use every core — the reusable-test workflow passes it through from its
# `workers` input.
PYTEST_N_WORKERS ?= 2
PYTEST_PARALLEL_LOCAL := -n auto --dist=loadscope -m "not serial"
PYTEST_PARALLEL_CI := -n $(PYTEST_N_WORKERS) --dist=loadscope -m "not serial"
PYTEST_PARALLEL := $(PYTEST_PARALLEL_CI)
PYTEST_SERIAL := -m serial

ci-tests-unit:  ## Run unit tests only (matches ci.yml unit-tests job)
	@echo "Running unit tests (parallel)..."
	$(call run-tool,pytest,$(TESTS_UNIT) $(PYTEST_PARALLEL) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-unit.xml --junitxml=junit-unit.xml)

ci-tests-ui-unit:  ## Run UI unit tests (tests/ui/) on every pull_request
	@echo "Running UI unit tests..."
	$(call run-tool,pytest,tests/ui/ $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-ui-unit.xml --junitxml=junit-ui-unit.xml)

ci-tests-integration:  ## Run integration tests only (matches ci.yml integration-tests job)
	@echo "Running integration tests (parallel)..."
	$(call run-tool,pytest,$(TESTS_INTEGRATION) $(PYTEST_PARALLEL) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-integration.xml --junitxml=junit-integration.xml)

ci-tests-e2e:  ## Run end-to-end tests only (matches ci.yml e2e-tests job)
	@echo "Running end-to-end tests..."
	$(call run-tool,pytest,$(TESTS_E2E) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-e2e.xml --junitxml=junit-e2e.xml)

ci-tests-matrix:  ## Run comprehensive test matrix (matches test-matrix.yml workflow)
	@echo "Running comprehensive test matrix..."
	$(call run-tool,pytest,$(TESTS) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-matrix.xml --junitxml=junit-matrix.xml)

ci-tests-performance:  ## Run performance tests only (matches ci.yml performance-tests job)
	@echo "Running performance tests..."
	$(call run-tool,pytest,$(TESTS_PERFORMANCE) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-performance.xml --junitxml=junit-performance.xml)

# Per-provider matrix target. Pass PROVIDER=<name> to scope the run to a
# single provider's test subtree (e.g. PROVIDER=aws → tests/providers/aws).
# CI's per-provider matrix invokes this with PROVIDER set for each entry;
# local dev can omit it to run the full tests/providers tree.
PROVIDER ?=
PROVIDER_SUFFIX := $(if $(PROVIDER),-$(PROVIDER),)
PROVIDER_PATH := $(if $(PROVIDER),$(TESTS_PROVIDERS)/$(PROVIDER),$(TESTS_PROVIDERS))
# Serial-marked provider tests live under each provider's ``live/`` subtree.
# That directory is listed in pyproject's ``norecursedirs`` so the parallel
# ``ci-tests-providers`` target never descends into it; the serial target
# below has to point pytest at the path explicitly so collection succeeds.
# Without --live the root conftest adds ``skip_live`` to every collected
# test, so the job exits 0 with a clear "163 skipped" line.  CI with live
# AWS credentials can opt in by setting PYTEST_LIVE=--live.
PROVIDER_SERIAL_PATH := $(if $(PROVIDER),$(TESTS_PROVIDERS)/$(PROVIDER)/live,$(TESTS_PROVIDERS))
PYTEST_LIVE ?=
ci-tests-providers:  ## Run providers tests (PROVIDER=<name> scopes to one provider)
	@echo "Running provider tests (parallel): $(if $(PROVIDER),$(PROVIDER),all)..."
	$(call run-tool,pytest,$(PROVIDER_PATH) $(PYTEST_PARALLEL) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-providers$(PROVIDER_SUFFIX).xml --junitxml=junit-providers$(PROVIDER_SUFFIX).xml)

ci-tests-providers-serial:  ## Run the serial-marked subset of provider tests (live AWS, etc.)
	@echo "Running serial provider tests: $(if $(PROVIDER),$(PROVIDER),all)..."
	# Report filenames must match the caller's Codecov upload pattern
	# junit-<test-type>-<provider>.xml (test-type=providers-serial), i.e.
	# junit-providers-serial-<provider>.xml — NOT providers-<provider>-serial,
	# or the serial leg's results never reach Codecov's test count.
	$(call run-tool,pytest,$(PROVIDER_SERIAL_PATH) $(PYTEST_SERIAL) $(PYTEST_LIVE) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-providers-serial$(PROVIDER_SUFFIX).xml --junitxml=junit-providers-serial$(PROVIDER_SUFFIX).xml)

ci-tests-infrastructure:  ## Run infrastructure tests only (matches ci.yml infrastructure-tests job)
	@echo "Running infrastructure tests (parallel)..."
	$(call run-tool,pytest,$(TESTS_INFRASTRUCTURE) $(PYTEST_PARALLEL) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-infrastructure.xml --junitxml=junit-infrastructure.xml)

# Legacy k8s HostFactory plugin unit suite.  It lives under
# src/orb/k8s_legacy/tests/unit (outside the main tests/ tree) and needs the
# heavy k8s deps (kubernetes, watchdog, kmock, …).  Those are already part of
# the `test` dependency-group, so this leg installs the same group as the other
# ci-tests-* legs and needs no special extra.  Its .coverage data is produced
# with the identical PYTEST_COV_ARGS / xml / junit naming so the coverage-combine
# fan-in job merges it exactly like every other leg.
TESTS_K8S_LEGACY := src/orb/k8s_legacy/tests/unit
ci-tests-k8s-legacy:  ## Run legacy k8s HostFactory unit tests (matches ci-tests.yml k8s-legacy leg)
	@# Run serially (-n0): these tests share a fixed on-disk workdir
	@# (get_workdir()) whose per-class tearDowns rmtree it, so parallel xdist
	@# workers race and delete each other's directories.
	@echo "Running legacy k8s HostFactory unit tests..."
	$(call run-tool,pytest,$(TESTS_K8S_LEGACY) -n0 $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-k8s-legacy.xml --junitxml=junit-k8s-legacy.xml)

ci-tests-coverage-check:  ## Combined-coverage gate: merge per-leg data + enforce threshold
	@echo "Combining per-leg coverage data and checking combined threshold ($(COVERAGE_THRESHOLD)%)..."
	$(call run-tool,coverage,combine)
	# Emit the merged XML BEFORE the threshold check so it is always produced
	# for the single Codecov upload, even when the gate below fails.
	$(call run-tool,coverage,xml -o coverage-combined.xml)
	# Diagnostic: print BOTH the branch-inclusive total (what the gate below
	# enforces) and the line-only total (closer to what Codecov's line-rate
	# reports), plus the line-rate embedded in the XML Codecov actually reads.
	# This makes any gate-vs-Codecov discrepancy self-explaining in the run log.
	# Diagnostic: the gate below enforces coverage.py's branch-inclusive TOTAL;
	# Codecov reads the XML's line-rate.  Print both from the SAME combined data
	# (plus statement/branch counts) so any gate-vs-Codecov delta is explained
	# in-run and never has to be guessed at again.
	@echo "=== COVERAGE DIAGNOSTIC (single coverage-combined dataset) ==="
	@python3 -c "import xml.etree.ElementTree as ET; r=ET.parse('coverage-combined.xml').getroot(); lr=float(r.get('line-rate'))*100; br=float(r.get('branch-rate'))*100; lc=int(r.get('lines-covered',0)); lv=int(r.get('lines-valid',0)); bc=int(r.get('branches-covered',0)); bv=int(r.get('branches-valid',0)); print(f'XML line-rate (Codecov reads this): {lr:.2f}%  ({lc}/{lv} lines)'); print(f'XML branch-rate: {br:.2f}%  ({bc}/{bv} branches)')" || echo "(xml parse failed)"
	@echo "coverage.py branch-inclusive TOTAL (this gate enforces) ->"
	@echo "=== END DIAGNOSTIC ==="
	# `coverage report` uses --fail-under (the pytest-cov spelling
	# --cov-fail-under is not valid for the coverage CLI).
	$(call run-tool,coverage,report --fail-under=$(COVERAGE_THRESHOLD))

# @SECTION UI Build

ui-build:  ## Build the Reflex static bundle into src/orb/ui/_static
	@./dev-tools/package/build_ui.sh

ci-tests-ui-smoke:  ## Boot embedded UI + curl each page + shut down (matches ci.yml ui-smoke job)
	@./dev-tools/ci/run_ui_smoke.sh

ci-check-python-version-drift:  ## Assert that workflow fallback strings match .project.yml python.versions
	@# pyyaml is available via the project's core [dependencies]; uv run --no-sync
	@# uses the venv pre-populated by setup-uv-cached (group: arch) in CI.
	@echo "Checking Python version drift between .project.yml and workflow fallback strings..."
	@uv run --no-sync python dev-tools/ci/check_python_version_drift.py

ci-check:  ## Run comprehensive CI checks (matches GitHub Actions exactly)
	@echo "Running comprehensive CI checks that match GitHub Actions pipeline..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture
	$(MAKE) ci-check-python-version-drift
	$(MAKE) ci-tests-unit

ci-check-quick:  ## Run quick CI checks (fast checks only)
	@echo "Running quick CI checks..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture
	$(MAKE) ci-check-python-version-drift

ci-check-verbose:  ## Run CI checks with verbose output
	@echo "Running CI checks with verbose output..."
	$(MAKE) ci-check

ci: ci-check ci-tests-integration ci-tests-e2e  ## Run full CI pipeline (comprehensive checks + all tests)
	@echo "Full CI pipeline completed successfully!"

ci-quick: ci-check-quick  ## Run quick CI pipeline (fast checks only)
	@echo "Quick CI pipeline completed successfully!"

# Workflow-specific targets (match GitHub Actions workflow names)
workflow-ci: ci-check ci-tests-unit ci-tests-integration  ## Run complete CI workflow locally
	@echo "CI workflow completed successfully!"

workflow-test-matrix: ci-tests-matrix  ## Run test matrix workflow locally
	@echo "Test matrix workflow completed successfully!"

workflow-security: ci-security ci-security-container  ## Run security workflow locally
	@echo "Security workflow completed successfully!"

# @SECTION Local Workflow Execution (using act)
local-workflow: dev-install  ## Run local workflows (usage: make local-workflow [list|dry-run|push|pr|release|ci|security|test-matrix|clean])
	@if echo "$(MAKECMDGOALS)" | grep -q "list"; then \
		if command -v act >/dev/null 2>&1; then \
			act -l; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "dry-run"; then \
		if command -v act >/dev/null 2>&1; then \
			act --dryrun; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "push"; then \
		if command -v act >/dev/null 2>&1; then \
			act push; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "pr"; then \
		if command -v act >/dev/null 2>&1; then \
			act pull_request; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "release"; then \
		if command -v act >/dev/null 2>&1; then \
			act release; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "ci"; then \
		if command -v act >/dev/null 2>&1; then \
			act -W .github/workflows/ci.yml; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "security"; then \
		if command -v act >/dev/null 2>&1; then \
			act -W .github/workflows/security.yml; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "test-matrix"; then \
		if command -v act >/dev/null 2>&1; then \
			act -W .github/workflows/test-matrix.yml; \
		else \
			echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		fi; \
	elif echo "$(MAKECMDGOALS)" | grep -q "clean"; then \
		rm -rf .local/artifacts; \
		if command -v docker >/dev/null 2>&1; then \
			docker ps -a --filter "label=act" -q | xargs -r docker rm -f; \
		fi; \
	else \
		echo "Usage: make local-workflow [list|dry-run|push|pr|release|ci|security|test-matrix|clean]"; \
	fi

# Dummy targets for local workflow flags
dry-run pr test-matrix:
	@:

# Backward compatibility aliases
local-list: ; @$(MAKE) local-workflow list
local-dry-run: ; @$(MAKE) local-workflow dry-run
local-push: ; @$(MAKE) local-workflow push
local-pr: ; @$(MAKE) local-workflow pr
local-release: ; @$(MAKE) local-workflow release
local-ci: ; @$(MAKE) local-workflow ci
local-security: ; @$(MAKE) local-workflow security
local-test-matrix: ; @$(MAKE) local-workflow test-matrix
local-clean: ; @$(MAKE) local-workflow clean

# @SECTION Go SDK
# sdk-go-build, sdk-go-test, and sdk-go-generate are defined in makefiles/sdk.mk
# (the SDK module has its own go.mod; those targets cd into sdk/go so the build
# resolves against the SDK module, not the repo-root module).

# The Go SDK version bump + spec stamp are folded into the semantic-release
# BUILD hook (make semantic-release-build), so they land in the SAME commit
# python-semantic-release tags vX.Y.Z (see [tool.semantic_release] assets in
# pyproject.toml).  This keeps the release tag self-contained — the tagged
# commit already carries the bumped version.go and the version-stamped spec —
# and eliminates the standalone post-tag chore(sdk/go) commit that used to land
# on main and pollute the next release's commit range.

sdk-go-stamp-version:  ## Stamp Go SDK version.go + spec info.version to VERSION (no git). Usage: make sdk-go-stamp-version VERSION=1.6.0
	@# Stamp-only: no git.  Called by the semantic-release build hook so the
	@# result is committed+tagged as part of the release commit.  Idempotent —
	@# re-stamping the same version reproduces byte-identical files (the spec is
	@# re-serialised with the same canonical indent=2/trailing-newline formatting
	@# export_openapi_spec.sh writes), so the sdk-spec-drift-guard still passes.
	@# The committed spec's ROUTES are kept fresh on every PR by that guard, so at
	@# release time only info.version changes — no server boot needed (this runs in
	@# the slim build container that has no uv/orb).
	@if [ -z "$(VERSION)" ]; then echo "ERROR: VERSION is required"; exit 1; fi
	sed -i "s/MinCompatibleVersion = \".*\"/MinCompatibleVersion = \"$(VERSION)\"/" sdk/go/orb/version.go
	@python3 -c "import json,sys; p='sdk/spec/openapi.json'; d=json.load(open(p,encoding='utf-8')); d['info']['version']=sys.argv[1]; f=open(p,'w',encoding='utf-8'); f.write(json.dumps(d,indent=2,ensure_ascii=True)+chr(10)); f.close()" "$(VERSION)"

sdk-go-tag:  ## Tag the current release commit as sdk/go/vX.Y.Z and push it (no commit). Usage: make sdk-go-tag VERSION=1.6.0
	@# Tag-only: points a submodule-scoped tag at the CURRENT HEAD, which is the
	@# release commit python-semantic-release just created and tagged vX.Y.Z.  The
	@# Go module proxy serves
	@# go get github.com/finos/open-resource-broker/sdk/go@vX.Y.Z off this tag.
	@# No commit and no branch push here: PSR already pushed the release commit, so
	@# nothing extra lands on main.
	@if [ -z "$(VERSION)" ]; then echo "ERROR: VERSION is required"; exit 1; fi
	git tag sdk/go/v$(VERSION)
	git push origin sdk/go/v$(VERSION)

sdk-go-export-spec:  ## Export OpenAPI spec from running ORB server into sdk/spec/openapi.json
	@./dev-tools/release/export_openapi_spec.sh

