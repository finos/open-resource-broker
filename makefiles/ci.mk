# CI/CD integration targets
# GitHub Actions workflows, local CI execution, and CI-specific checks

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
	$(call run-tool,mypy,.)

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
	./dev-tools/scripts/check_file_sizes.py --warn-only

# Composite target
ci-architecture: ci-arch-cqrs ci-arch-clean ci-arch-imports ci-arch-file-sizes  ## Run all architecture checks

# Individual security targets (with tool names)
ci-security-bandit:  ## Run Bandit security scan
	@echo "Running Bandit security scan..."
	$(call run-tool,bandit,-r $(PACKAGE))

ci-security-safety:  ## Run Safety dependency scan
	@echo "Running Safety dependency scan..."
	$(call run-tool,safety,check)

ci-security-trivy: dev-install  ## Run Trivy container scan
	./dev-tools/scripts/ci_security.py trivy

ci-security-hadolint: dev-install  ## Run Hadolint Dockerfile scan
	./dev-tools/scripts/ci_security.py hadolint

ci-security-semgrep: dev-install  ## Run Semgrep static analysis
	./dev-tools/scripts/ci_security.py semgrep

ci-security-trivy-fs: dev-install  ## Run Trivy filesystem scan
	./dev-tools/scripts/ci_security.py trivy-fs

ci-security-trufflehog: dev-install  ## Run TruffleHog secrets scan
	./dev-tools/scripts/ci_security.py trufflehog

ci-security-container: dev-install ## Run container security scans (CI)
	./dev-tools/scripts/security_container.py

# Composite target
ci-security: ci-security-bandit ci-security-safety ci-security-semgrep ci-security-trivy-fs ci-security-trufflehog  ## Run all security scans

ci-build-sbom:  ## Generate SBOM files (matches publish.yml workflow)
	@echo "Generating SBOM files for CI..."
	@echo "This matches the GitHub Actions publish.yml workflow exactly"
	$(MAKE) sbom-generate

# CI test targets
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

# CI pipeline targets
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
	./dev-tools/scripts/ci_check.py --verbose

ci: ci-check ci-tests-integration ci-tests-e2e  ## Run full CI pipeline (comprehensive checks + all tests)
	@echo "Full CI pipeline completed successfully!"

ci-quick: ci-check-quick  ## Run quick CI pipeline (fast checks only)
	@echo "Quick CI pipeline completed successfully!"

# Git setup for CI
ci-git-setup:  ## Setup git configuration for CI automated commits
	git config --local user.name "github-actions[bot]"
	git config --local user.email "github-actions[bot]@users.noreply.github.com"

# @SECTION Workflow-specific targets (match GitHub Actions workflow names)
workflow-ci: ci-check ci-tests-unit ci-tests-integration  ## Run complete CI workflow locally
	@echo "CI workflow completed successfully!"

workflow-test-matrix: ci-tests-matrix  ## Run test matrix workflow locally
	@echo "Test matrix workflow completed successfully!"

workflow-security: ci-security ci-security-container  ## Run security workflow locally
	@echo "Security workflow completed successfully!"

# @SECTION Local CI (GitHub Actions)
# Local GitHub Actions execution with act
local-list: dev-install  ## List available workflows and jobs for local execution
	@echo "Available workflows and jobs for local execution:"
	@if command -v act >/dev/null 2>&1; then \
		act -l; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-dry-run: dev-install  ## Validate all workflows without running (dry run)
	@echo "Validating workflows with act (dry run)..."
	@if command -v act >/dev/null 2>&1; then \
		act --dryrun; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-push: dev-install  ## Simulate push event (triggers CI workflow)
	@echo "Simulating push event locally..."
	@if command -v act >/dev/null 2>&1; then \
		act push; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-pr: dev-install  ## Simulate pull request event (triggers CI + security workflows)
	@echo "Simulating pull request event locally..."
	@if command -v act >/dev/null 2>&1; then \
		act pull_request; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-release: dev-install  ## Simulate release event (triggers publish workflow)
	@echo "Simulating release event locally..."
	@if command -v act >/dev/null 2>&1; then \
		act release; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-ci: dev-install  ## Run CI workflow locally (.github/workflows/ci.yml)
	@echo "Running CI workflow locally..."
	@if command -v act >/dev/null 2>&1; then \
		act -W .github/workflows/ci.yml; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-security: dev-install  ## Run security workflow locally (.github/workflows/security.yml)
	@echo "Running security workflow locally..."
	@if command -v act >/dev/null 2>&1; then \
		act -W .github/workflows/security.yml; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-test-matrix: dev-install  ## Run test matrix workflow locally (.github/workflows/test-matrix.yml)
	@echo "Running test matrix workflow locally..."
	@if command -v act >/dev/null 2>&1; then \
		act -W .github/workflows/test-matrix.yml; \
	else \
		echo "Error: act not installed. Run 'make install-dev-tools' to install."; \
		exit 1; \
	fi

local-clean: ## Clean local act artifacts and containers
	@echo "Cleaning local act artifacts..."
	@rm -rf .local/artifacts
	@if command -v docker >/dev/null 2>&1; then \
		echo "Removing act containers..."; \
		docker ps -a --filter "label=act" -q | xargs -r docker rm -f; \
		echo "Removing act images (unused)..."; \
		docker images --filter "dangling=true" -q | xargs -r docker rmi; \
	fi
	@echo "Local cleanup complete!"
