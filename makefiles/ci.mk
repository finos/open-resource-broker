# CI/CD targets that match GitHub Actions workflows exactly

# @SECTION CI Quality Checks
# Individual code quality targets (with tool names)
ci-quality-ruff: dev-install  ## Run Ruff formatting and linting check (basic rules only)
	@echo "Running Ruff formatting and linting check (basic rules only)..."
	@uv run ruff check --select W,F,I --ignore E501 --quiet .
	@uv run ruff format --check --quiet .

ci-quality-ruff-optional:  ## Run Ruff extended linting (warnings only)
	@echo "Running Ruff extended linting..."
	uv run ruff check --select=E501,N,UP,B,PL,C90,RUF . || true

ci-quality-radon:  ## Run radon complexity analysis
	@echo "Running radon complexity analysis..."
	$(call run-tool,radon,cc $(PACKAGE) --min B --show-complexity)
	$(call run-tool,radon,mi $(PACKAGE) --min B)

ci-quality-mypy:  ## Run mypy type checking
	@echo "Running mypy type check..."
	$(call run-tool,mypy,-p api -p application -p cli -p config -p domain -p infrastructure -p interface -p mcp -p metrics -p monitoring -p orb_py -p providers -p sdk)

ci-quality-pyright:  ## Run pyright type checking
	@echo "Running pyright type check..."
	$(call run-tool,pyright,src/)

# Composite target (for local convenience)
ci-quality: ci-quality-ruff ci-quality-mypy  ## Run all enforced code quality checks

ci-quality-full: ci-quality-ruff ci-quality-ruff-optional ci-quality-mypy  ## Run all code quality checks including optional

# Individual architecture quality targets (with tool names)
ci-arch-cqrs:  ## Run CQRS pattern validation
	@echo "Running CQRS pattern validation..."
	./dev-tools/scripts/validate_cqrs.py

ci-arch-clean:  ## Run Clean Architecture dependency validation
	@echo "Running Clean Architecture validation..."
	./dev-tools/scripts/check_architecture.py

ci-arch-imports:  ## Run import validation
	@echo "Running import validation..."
	./dev-tools/scripts/validate_imports.py

ci-arch-file-sizes:  ## Check file size compliance
	@echo "Running file size checks..."
	./dev-tools/scripts/dev_tools_runner.py check-file-sizes --warn-only

# Composite target
ci-architecture: ci-arch-cqrs ci-arch-clean ci-arch-imports ci-arch-file-sizes  ## Run all architecture checks

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

# Composite target
ci-security: ci-security-bandit ci-security-safety ci-security-semgrep ci-security-trivy-fs ci-security-trufflehog  ## Run all security scans

ci-build-sbom:  ## Generate SBOM files (matches publish.yml workflow)
	@echo "Generating SBOM files for CI..."
	@echo "This matches the GitHub Actions publish.yml workflow exactly"
	$(MAKE) sbom-generate

ci-tests-unit:  ## Run unit tests only (matches ci.yml unit-tests job)
	@echo "Running unit tests..."
	$(call run-tool,pytest,$(TESTS_UNIT) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-unit.xml --junitxml=junit-unit.xml)

ci-tests-integration:  ## Run integration tests only (matches ci.yml integration-tests job)
	@echo "Running integration tests..."
	$(call run-tool,pytest,$(TESTS_INTEGRATION) $(PYTEST_ARGS) --junitxml=junit-integration.xml)

ci-tests-e2e:  ## Run end-to-end tests only (matches ci.yml e2e-tests job)
	@echo "Running end-to-end tests..."
	$(call run-tool,pytest,$(TESTS_E2E) $(PYTEST_ARGS) --junitxml=junit-e2e.xml)

ci-tests-matrix:  ## Run comprehensive test matrix (matches test-matrix.yml workflow)
	@echo "Running comprehensive test matrix..."
	$(call run-tool,pytest,$(TESTS) $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-matrix.xml --junitxml=junit-matrix.xml)

ci-tests-performance:  ## Run performance tests only (matches ci.yml performance-tests job)
	@echo "Running performance tests..."
	$(call run-tool,pytest,$(TESTS_PERFORMANCE) $(PYTEST_ARGS) --junitxml=junit-performance.xml)

ci-check:  ## Run comprehensive CI checks (matches GitHub Actions exactly)
	@echo "Running comprehensive CI checks that match GitHub Actions pipeline..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture
	$(MAKE) ci-tests-unit

ci-check-quick:  ## Run quick CI checks (fast checks only)
	@echo "Running quick CI checks..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture

ci-check-verbose:  ## Run CI checks with verbose output
	@echo "Running CI checks with verbose output..."
	./dev-tools/scripts/workflow_orchestrator.py ci-check --verbose

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

ci-git-setup:  ## Setup git configuration for CI automated commits
	git config --local user.name "github-actions[bot]"
	git config --local user.email "github-actions[bot]@users.noreply.github.com"
