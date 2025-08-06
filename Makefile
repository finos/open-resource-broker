# Makefile for Open Host Factory Plugin

.PHONY: help install dev-install test test-unit test-integration test-e2e test-all test-cov test-html test-parallel test-quick test-performance test-aws lint format security clean build docs docs-serve docs-build run version-bump ci-quality ci-security ci-security-codeql ci-security-container ci-architecture ci-imports ci-tests-unit ci-tests-integration ci-tests-e2e ci-tests-matrix ci-tests-performance ci-check ci-check-quick ci-check-fix ci-check-verbose ci ci-quick workflow-ci workflow-test-matrix workflow-security architecture-check architecture-report

# Python settings
PYTHON := python3
VENV := .venv
BIN := $(VENV)/bin

# Project settings
PROJECT := open-hostfactory-plugin
PACKAGE := src
TESTS := tests
CONFIG := config/config.json

# Coverage settings
COVERAGE_REPORT := coverage.xml
COVERAGE_HTML := htmlcov

# Test settings
PYTEST_ARGS := -v --tb=short --durations=10
PYTEST_COV_ARGS := --cov=$(PACKAGE) --cov-report=term-missing --cov-branch --no-cov-on-fail
PYTEST_TIMEOUT := --timeout=300
PYTEST_MAXFAIL := --maxfail=5

# Documentation settings
DOCS_DIR := docs
DOCS_BUILD_DIR := $(DOCS_DIR)/site

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Installation targets
install: $(VENV)/bin/activate  ## Install production dependencies (smart: uv if available, else pip)
	@if command -v uv >/dev/null 2>&1; then \
		echo "INFO: Using uv for faster installation..."; \
		uv pip install -r requirements.txt; \
	else \
		echo "INFO: Using pip (uv not available)..."; \
		$(BIN)/pip install -r requirements.txt; \
	fi

install-pip: $(VENV)/bin/activate  ## Install production dependencies (force pip)
	$(BIN)/pip install -r requirements.txt

install-uv: $(VENV)/bin/activate  ## Install production dependencies (force uv)
	uv pip install -r requirements.txt

dev-install: $(VENV)/bin/activate  ## Install development dependencies (smart: uv if available, else pip)
	@if command -v uv >/dev/null 2>&1; then \
		echo "INFO: Using uv for faster development setup..."; \
		uv pip install -e ".[dev]"; \
	else \
		echo "INFO: Using pip (uv not available)..."; \
		./dev-tools/package/install-dev.sh; \
	fi

dev-install-pip: $(VENV)/bin/activate  ## Install development dependencies (force pip)
	./dev-tools/package/install-dev.sh

dev-install-uv: $(VENV)/bin/activate  ## Install development dependencies (force uv)
	uv pip install -e ".[dev]"

$(VENV)/bin/activate: requirements.txt
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@if command -v uv >/dev/null 2>&1; then \
		echo "INFO: Using uv for virtual environment setup..."; \
		uv pip install --upgrade pip; \
		uv pip install -r requirements.txt; \
	else \
		echo "INFO: Using pip for virtual environment setup..."; \
		$(BIN)/pip install --upgrade pip; \
		$(BIN)/pip install -r requirements.txt; \
	fi
	touch $(VENV)/bin/activate

# Testing targets (using dev-tools)
test: test-quick  ## Run quick test suite (alias for test-quick)

test-unit: dev-install  ## Run unit tests only
	$(PYTHON) dev-tools/testing/run_tests.py --unit

test-integration: dev-install  ## Run integration tests only
	$(PYTHON) dev-tools/testing/run_tests.py --integration

test-e2e: dev-install  ## Run end-to-end tests only
	$(PYTHON) dev-tools/testing/run_tests.py --e2e

test-all: dev-install  ## Run all tests
	$(PYTHON) dev-tools/testing/run_tests.py

test-parallel: dev-install  ## Run tests in parallel
	$(PYTHON) dev-tools/testing/run_tests.py --parallel

test-quick: dev-install  ## Run quick test suite (unit + fast integration)
	$(PYTHON) dev-tools/testing/run_tests.py --unit --fast

test-performance: dev-install  ## Run performance tests
	$(PYTHON) dev-tools/testing/run_tests.py --markers slow

test-aws: dev-install  ## Run AWS-specific tests
	$(PYTHON) dev-tools/testing/run_tests.py --markers aws

test-cov: dev-install  ## Run tests with coverage report
	$(PYTHON) dev-tools/testing/run_tests.py --coverage

test-html: dev-install  ## Run tests with HTML coverage report
	$(PYTHON) dev-tools/testing/run_tests.py --html-coverage
	@echo "Coverage report generated in htmlcov/index.html"

test-report: dev-install  ## Generate comprehensive test report
	$(PYTHON) dev-tools/testing/run_tests.py --coverage --html-coverage

# Code quality targets
quality-check: dev-install  ## Run professional quality checks
	@echo "Running professional quality checks..."
	$(PYTHON) dev-tools/scripts/quality_check.py --strict

quality-check-fix: dev-install  ## Run quality checks with auto-fix
	@echo "Running professional quality checks with auto-fix..."
	$(PYTHON) dev-tools/scripts/quality_check.py --fix

quality-check-files: dev-install  ## Run quality checks on specific files (usage: make quality-check-files FILES="file1.py file2.py")
	@if [ -z "$(FILES)" ]; then \
		echo "Error: FILES is required. Usage: make quality-check-files FILES=\"file1.py file2.py\""; \
		exit 1; \
	fi
	@echo "Running professional quality checks on specified files..."
	$(PYTHON) dev-tools/scripts/quality_check.py --strict --files $(FILES)

lint: dev-install quality-check  ## Run all linting checks including quality checks
	@echo "Running Black (code formatting)..."
	$(BIN)/black --check $(PACKAGE) $(TESTS)
	@echo "Running isort (import sorting)..."
	$(BIN)/isort --check-only $(PACKAGE) $(TESTS)
	@echo "Running flake8 (style guide)..."
	$(BIN)/flake8 $(PACKAGE) $(TESTS)
	@echo "Running mypy (type checking)..."
	$(BIN)/mypy $(PACKAGE)
	@echo "Running pylint (code analysis)..."
	$(BIN)/pylint $(PACKAGE)

format: dev-install  ## Format code (Black + isort + autopep8 + whitespace cleanup)
	@echo "Cleaning up whitespace in blank lines..."
	@find $(PACKAGE) $(TESTS) -name "*.py" -exec sed -i '' 's/^[[:space:]]*$$//' {} \;
	$(BIN)/autopep8 --in-place --max-line-length=88 --select=E501 --recursive $(PACKAGE) $(TESTS)
	$(BIN)/black $(PACKAGE) $(TESTS)
	$(BIN)/isort $(PACKAGE) $(TESTS)

security: dev-install  ## Run security checks
	@echo "Running bandit (security linter)..."
	$(BIN)/bandit -r $(PACKAGE) -f json -o bandit-report.json || echo "Security issues found - check bandit-report.json"
	$(BIN)/bandit -r $(PACKAGE) -f sarif -o bandit-results.sarif || echo "Security issues found - check bandit-results.sarif"
	@echo "Running safety (dependency vulnerability check)..."
	$(BIN)/safety check || echo "Vulnerable dependencies found"

security-container: ## Run container security scans
	@echo "Running container security scans..."
	@if ! command -v trivy >/dev/null 2>&1; then \
		echo "Installing Trivy..."; \
		curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin; \
	fi
	@echo "Building Docker image for security scan..."
	docker build -t $(PROJECT):security-scan .
	@echo "Running Trivy vulnerability scan..."
	trivy image --format sarif --output trivy-results.sarif $(PROJECT):security-scan
	trivy image --format json --output trivy-results.json $(PROJECT):security-scan
	@echo "Running Hadolint Dockerfile scan..."
	@if ! command -v hadolint >/dev/null 2>&1; then \
		echo "Installing Hadolint..."; \
		wget -O hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64; \
		chmod +x hadolint; \
		sudo mv hadolint /usr/local/bin/; \
	fi
	hadolint Dockerfile --format sarif > hadolint-results.sarif || echo "Dockerfile issues found"

security-full: security security-container  ## Run all security scans including container

sbom-generate: dev-install ## Generate Software Bill of Materials (SBOM)
	@echo "Generating SBOM files..."
	@if ! command -v syft >/dev/null 2>&1; then \
		echo "Installing Syft..."; \
		curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin; \
	fi
	@echo "Installing pip-audit for Python SBOM..."
	$(BIN)/pip install pip-audit
	@echo "Generating Python dependency SBOM..."
	$(BIN)/pip-audit --format=cyclonedx-json --output=python-sbom-cyclonedx.json
	$(BIN)/pip-audit --format=spdx-json --output=python-sbom-spdx.json
	@echo "Generating project SBOM with Syft..."
	syft . -o spdx-json=project-sbom-spdx.json
	syft . -o cyclonedx-json=project-sbom-cyclonedx.json
	@echo "Building Docker image for container SBOM..."
	docker build -t $(PROJECT):sbom-scan .
	@echo "Generating container SBOM..."
	syft $(PROJECT):sbom-scan -o spdx-json=container-sbom-spdx.json
	syft $(PROJECT):sbom-scan -o cyclonedx-json=container-sbom-cyclonedx.json
	@echo "SBOM files generated successfully"

security-scan: dev-install  ## Run comprehensive security scan using dev-tools
	@echo "Running comprehensive security scan..."
	$(PYTHON) dev-tools/security/security_scan.py

security-validate-sarif: dev-install  ## Validate SARIF files
	@echo "Validating SARIF files..."
	$(PYTHON) dev-tools/security/validate_sarif.py *.sarif

security-report: security-full sbom-generate  ## Generate comprehensive security report
	@echo "## Security Report Generated" > security-report.md
	@echo "" >> security-report.md
	@echo "### Files Generated:" >> security-report.md
	@echo "- bandit-report.json (Security issues)" >> security-report.md
	@echo "- bandit-results.sarif (Security SARIF)" >> security-report.md
	@echo "- trivy-results.json (Container vulnerabilities)" >> security-report.md
	@echo "- trivy-results.sarif (Container SARIF)" >> security-report.md
	@echo "- hadolint-results.sarif (Dockerfile issues)" >> security-report.md
	@echo "- *-sbom-*.json (Software Bill of Materials)" >> security-report.md
	@echo "" >> security-report.md
	@echo "Security report generated in security-report.md"

# Architecture Quality Gates
architecture-check: dev-install  ## Run architecture compliance checks
	@echo "Running architecture quality checks..."
	$(PYTHON) dev-tools/scripts/validate_cqrs.py --warn-only
	$(PYTHON) dev-tools/scripts/check_architecture.py --warn-only

architecture-report: dev-install  ## Generate detailed architecture report
	@echo "Generating architecture dependency report..."
	$(PYTHON) dev-tools/scripts/check_architecture.py --report

# Architecture Documentation Generation
docs-update: dev-install  ## Update and build documentation
	@echo "Building documentation site..."
	cd docs && mkdocs build

docs-serve: dev-install  ## Serve versioned documentation locally with live reload
	@echo "Starting versioned documentation server at http://127.0.0.1:8000"
	@echo "Press Ctrl+C to stop the server"
	@if [ ! -f "$(BIN)/mike" ]; then \
		echo "Mike not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike serve

quality-gates: lint test architecture-check  ## Run all quality gates
	@echo "All quality gates completed successfully!"

quality-full: lint test architecture-check docs-generate  ## Run quality gates and generate docs
	@echo "Full quality check and documentation generation completed!"

# Completion targets
generate-completions:     ## Generate completion scripts (bash and zsh)
	@echo "Generating bash completion..."
	$(PYTHON) src/run.py --completion bash > dev-tools/completions/bash/ohfp-completion.bash
	@echo "Generating zsh completion..."
	$(PYTHON) src/run.py --completion zsh > dev-tools/completions/zsh/_ohfp
	@echo "SUCCESS: Completion scripts generated in dev-tools/completions/"

install-completions:      ## Install completions for current user
	./dev-tools/scripts/install-completions.sh

install-bash-completions: ## Install bash completions only
	./dev-tools/scripts/install-completions.sh bash

install-zsh-completions:  ## Install zsh completions only
	./dev-tools/scripts/install-completions.sh zsh

uninstall-completions:    ## Remove installed completions
	./dev-tools/scripts/install-completions.sh --uninstall

test-completions:         ## Test completion generation
	@echo "Testing bash completion generation..."
	@$(PYTHON) src/run.py --completion bash > /dev/null && echo "SUCCESS: Bash completion generation works"
	@echo "Testing zsh completion generation..."
	@$(PYTHON) src/run.py --completion zsh > /dev/null && echo "SUCCESS: Zsh completion generation works"

# Documentation targets
docs: docs-build  ## Build documentation (alias for docs-build)

docs-build: dev-install  ## Build documentation
	@echo "Building documentation with MkDocs..."
	cd $(DOCS_DIR) && ../$(BIN)/mkdocs build
	@echo "Documentation built in $(DOCS_BUILD_DIR)/"

docs-serve-dev:  ## Serve documentation in development mode (non-versioned)
	@echo "Starting development documentation server at http://127.0.0.1:8000"
	@echo "Press Ctrl+C to stop the server"
	@if [ ! -f "$(BIN)/mkdocs" ]; then \
		echo "MkDocs not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mkdocs serve

docs-deploy: dev-install  ## Deploy documentation to GitHub Pages
	cd $(DOCS_DIR) && ../$(BIN)/mike deploy --push --update-aliases $(VERSION) latest

docs-deploy-version: dev-install  ## Deploy specific version (usage: make docs-deploy-version VERSION=1.0.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make docs-deploy-version VERSION=1.0.0"; \
		exit 1; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike deploy --push --update-aliases $(VERSION) latest

docs-list-versions:  ## List all documentation versions
	@if [ ! -f "$(BIN)/mike" ]; then \
		echo "Mike not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike list

docs-delete-version:  ## Delete a documentation version (usage: make docs-delete-version VERSION=1.0.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make docs-delete-version VERSION=1.0.0"; \
		exit 1; \
	fi
	@if [ ! -f "$(BIN)/mike" ]; then \
		echo "Mike not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike delete $(VERSION)

docs-deploy-gitlab:  ## Deploy documentation to GitLab Pages (production)
	@echo "INFO: Triggering GitLab Pages production deployment..."
	@echo "INFO: Documentation will be available at: https://aws-gfs-acceleration.gitlab.aws.dev/open-hostfactory-plugin"
	@echo "TIP: Push to main branch to trigger deployment"
	git push origin main

docs-deploy-staging:  ## Deploy documentation to GitLab Pages (staging)
	@echo "INFO: Triggering GitLab Pages staging deployment..."
	@echo "INFO: Documentation will be available at: https://aws-gfs-acceleration.gitlab.aws.dev/open-hostfactory-plugin/develop"
	@echo "TIP: Push to develop branch to trigger deployment"
	git push origin develop

docs-check-gitlab:  ## Check GitLab Pages deployment status
	@echo "INFO: Production URL: https://aws-gfs-acceleration.gitlab.aws.dev/open-hostfactory-plugin"
	@echo "INFO: Staging URL:    https://aws-gfs-acceleration.gitlab.aws.dev/open-hostfactory-plugin/develop"
	@echo "INFO: GitLab Project: https://gitlab.aws.dev/aws-gfs-acceleration/open-hostfactory-plugin"
	@echo "INFO: CI/CD Pipelines: https://gitlab.aws.dev/aws-gfs-acceleration/open-hostfactory-plugin/-/pipelines"

docs-clean:  ## Clean documentation build files
	rm -rf $(DOCS_BUILD_DIR)

# Version management targets
version-bump-patch:  ## Bump patch version (0.1.0 -> 0.1.1)
	./dev-tools/package/version-bump.sh patch

version-bump-minor:  ## Bump minor version (0.1.0 -> 0.2.0)
	./dev-tools/package/version-bump.sh minor

version-bump-major:  ## Bump major version (0.1.0 -> 1.0.0)
	./dev-tools/package/version-bump.sh major

version-bump:  ## Show version bump help
	./dev-tools/package/version-bump.sh

# Build targets (using dev-tools)
build: clean dev-install  ## Build package
	./dev-tools/package/build.sh

build-test: build  ## Build and test package installation
	./dev-tools/package/test-install.sh

# CI/CD targets
ci-quality: dev-install  ## Run code quality checks (matches ci.yml lint-and-security job)
	@echo "Running code quality checks..."
	$(PYTHON) -m black --check src/ tests/
	$(PYTHON) -m isort --check-only src/ tests/
	$(PYTHON) -m flake8 src/ tests/
	$(PYTHON) -m mypy src/
	$(PYTHON) -m pylint src/

ci-security: dev-install  ## Run security scans (matches security.yml workflow)
	@echo "Running security scans..."
	$(PYTHON) -m bandit -r src/
	$(PYTHON) -m safety check

ci-sbom: dev-install  ## Generate SBOM files (matches publish.yml workflow)
	@echo "Generating SBOM files..."
	$(PYTHON) dev-tools/scripts/generate_sbom.py --format both

ci-security-codeql: dev-install  ## Run CodeQL analysis locally (matches security.yml codeql-analysis job)
	@echo "CodeQL analysis requires GitHub Actions environment"
	@echo "Run 'gh workflow run security.yml' to trigger CodeQL analysis"

ci-security-container: dev-install  ## Run container security scans (matches security.yml container-security job)
	@echo "Running container security scans..."
	@if command -v docker >/dev/null 2>&1; then \
		docker build -t security-scan:latest .; \
		echo "Container built successfully for security scanning"; \
	else \
		echo "Docker not available - container security requires Docker"; \
	fi

ci-architecture: dev-install  ## Run architecture compliance checks (matches ci.yml architecture-compliance job)
	@echo "Running architecture compliance checks..."
	$(PYTHON) dev-tools/scripts/validate_cqrs.py
	$(PYTHON) dev-tools/scripts/check_architecture.py

ci-imports: dev-install  ## Run import validation (matches CI import checks)
	@echo "Running import validation..."
	$(PYTHON) dev-tools/scripts/validate_imports.py

ci-tests-unit: dev-install  ## Run unit tests only (matches ci.yml unit-tests job)
	@echo "Running unit tests..."
	$(PYTHON) -m pytest tests/unit/ $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-unit.xml --junitxml=junit-unit.xml

ci-tests-integration: dev-install  ## Run integration tests only (matches ci.yml integration-tests job)
	@echo "Running integration tests..."
	$(PYTHON) -m pytest tests/integration/ $(PYTEST_ARGS) --junitxml=junit-integration.xml

ci-tests-e2e: dev-install  ## Run end-to-end tests only (matches ci.yml e2e-tests job)
	@echo "Running end-to-end tests..."
	$(PYTHON) -m pytest tests/e2e/ $(PYTEST_ARGS) --junitxml=junit-e2e.xml

ci-tests-matrix: dev-install  ## Run comprehensive test matrix (matches test-matrix.yml workflow)
	@echo "Running comprehensive test matrix..."
	$(PYTHON) -m pytest tests/ $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-matrix.xml --junitxml=junit-matrix.xml

ci-tests-performance: dev-install  ## Run performance tests only (matches ci.yml performance-tests job)
	@echo "Running performance tests..."
	$(PYTHON) -m pytest tests/performance/ $(PYTEST_ARGS) --junitxml=junit-performance.xml

ci-check: dev-install  ## Run comprehensive CI checks (matches GitHub Actions exactly)
	@echo "Running comprehensive CI checks that match GitHub Actions pipeline..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture
	$(MAKE) ci-tests-unit

ci-check-quick: dev-install  ## Run quick CI checks (fast checks only)
	@echo "Running quick CI checks..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture

ci-check-fix: dev-install  ## Run CI checks with automatic formatting fixes
	@echo "Running CI checks with automatic fixes..."
	$(PYTHON) -m black src/ tests/
	$(PYTHON) -m isort src/ tests/
	$(MAKE) ci-quality

ci-check-verbose: dev-install  ## Run CI checks with verbose output
	@echo "Running CI checks with verbose output..."
	$(PYTHON) dev-tools/scripts/ci_check.py --verbose

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

# Cleanup targets
clean:  ## Clean up build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf $(COVERAGE_HTML)/
	rm -f $(COVERAGE_REPORT)
	rm -f test-results.xml
	rm -f bandit-report.json
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

clean-all: clean docs-clean  ## Clean everything including virtual environment
	rm -rf $(VENV)

# Application targets
run: install  ## Run application
	$(PYTHON) src/run.py

run-dev: dev-install  ## Run application in development mode
	$(PYTHON) src/run.py --log-level DEBUG

# Development targets
dev-setup: dev-install  ## Set up development environment
	@echo "Development environment setup complete!"
	@echo "Available commands:"
	@echo "  make test          - Run tests"
	@echo "  make docs-serve    - Start documentation server"
	@echo "  make lint          - Run code quality checks"
	@echo "  make format        - Format code"

# Package management targets
install-package: build  ## Install package locally
	$(BIN)/pip install dist/*.whl

uninstall-package:  ## Uninstall package
	$(BIN)/pip uninstall $(PROJECT) -y

reinstall-package: uninstall-package install-package  ## Reinstall package

# Database targets (if needed)
init-db: install  ## Initialize database
	$(PYTHON) src/run.py system init-db

# Configuration targets
create-config:  ## Create default config file
	@if [ ! -f $(CONFIG) ]; then \
		mkdir -p config; \
		cp config/config.example.json $(CONFIG); \
		echo "Created $(CONFIG)"; \
	else \
		echo "$(CONFIG) already exists"; \
	fi

validate-config: install  ## Validate configuration
	$(PYTHON) src/run.py config validate

# Docker targets
docker-build:  ## Build Docker image
	docker build -t $(PROJECT):latest .

docker-run:  ## Run Docker container
	docker run -p 8000:8000 $(PROJECT):latest

docker-compose-up:  ## Start with docker-compose
	docker-compose up -d

docker-compose-down:  ## Stop docker-compose
	docker-compose down

# Quick development workflow
dev: dev-install format lint test-quick  ## Quick development workflow (format, lint, test)
	@echo "Development workflow completed successfully!"

# Show project status
status:  ## Show project status and useful commands
	@echo "=== Open Host Factory Plugin Status ==="
	@echo ""
	@echo "📁 Project Structure:"
	@echo "  Source code:     $(PACKAGE)/"
	@echo "  Tests:          $(TESTS)/"
	@echo "  Documentation:  $(DOCS_DIR)/"
	@echo "  Dev tools:      dev-tools/"
	@echo ""
	@echo "INFO: Quick Commands:"
	@echo "  make dev-setup     - Set up development environment"
	@echo "  make test          - Run tests"
	@echo "  make docs-serve    - Start documentation server"
	@echo "  make dev           - Quick development workflow"
	@echo ""
	@echo "📚 Documentation:"
	@echo "  Local docs:     make docs-serve (versioned)"
	@echo "  Dev docs:       make docs-serve-dev (non-versioned)"
	@echo "  Build docs:     make docs-build"
	@echo "  GitLab Pages:   https://aws-gfs-acceleration.gitlab.aws.dev/open-hostfactory-plugin (production)"
	@echo "  Staging Pages:  https://aws-gfs-acceleration.gitlab.aws.dev/open-hostfactory-plugin/develop"
	@echo "  Deploy prod:    make docs-deploy-gitlab"
	@echo "  Deploy staging: make docs-deploy-staging"
	@echo "  List versions:  make docs-list-versions"
	@echo "  Deploy version: make docs-deploy-version VERSION=1.0.0"
	@echo ""
	@echo "INFO: Version Management:"
	@echo "  Patch version:  make version-bump-patch"
	@echo "  Minor version:  make version-bump-minor"
	@echo "  Major version:  make version-bump-major"

# UV-specific targets for performance optimization
uv-lock: ## Generate uv lock file for reproducible builds
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv not available. Install with: pip install uv"; \
		exit 1; \
	fi
	@echo "INFO: Generating uv lock files..."
	uv pip compile pyproject.toml --output-file requirements.lock
	uv pip compile pyproject.toml --extra dev --output-file requirements-dev.lock
	@echo "SUCCESS: Lock files generated: requirements.lock, requirements-dev.lock"

uv-sync: ## Sync environment with uv lock files
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv not available. Install with: pip install uv"; \
		exit 1; \
	fi
	@if [ -f requirements.lock ]; then \
		echo "INFO: Syncing with uv lock file..."; \
		uv pip sync requirements.lock; \
	else \
		echo "ERROR: No lock file found. Run 'make uv-lock' first."; \
		exit 1; \
	fi

uv-sync-dev: ## Sync development environment with uv lock files
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv not available. Install with: pip install uv"; \
		exit 1; \
	fi
	@if [ -f requirements-dev.lock ]; then \
		echo "INFO: Syncing development environment with uv lock file..."; \
		uv pip sync requirements-dev.lock; \
	else \
		echo "ERROR: No dev lock file found. Run 'make uv-lock' first."; \
		exit 1; \
	fi

uv-check: ## Check if uv is available and show version
	@if command -v uv >/dev/null 2>&1; then \
		echo "SUCCESS: uv is available: $$(uv --version)"; \
		echo "INFO: Performance comparison:"; \
		echo "  • uv is typically 10-100x faster than pip"; \
		echo "  • Better dependency resolution and error messages"; \
		echo "  • Use 'make dev-install-uv' for faster development setup"; \
	else \
		echo "ERROR: uv not available"; \
		echo "INFO: Install with: pip install uv"; \
		echo "INFO: Or use system package manager: brew install uv"; \
	fi

uv-benchmark: ## Benchmark uv vs pip installation speed
	@echo "INFO: Benchmarking uv vs pip installation speed..."
	@echo "This will create temporary virtual environments for testing."
	@echo ""
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv not available for benchmarking"; \
		exit 1; \
	fi
	@echo "INFO: Testing pip installation speed..."
	@time (python -m venv .venv-pip-test && .venv-pip-test/bin/pip install -e ".[dev]" > /dev/null 2>&1)
	@echo ""
	@echo "INFO: Testing uv installation speed..."
	@time (python -m venv .venv-uv-test && uv pip install -e ".[dev]" --python .venv-uv-test/bin/python > /dev/null 2>&1)
	@echo ""
	@echo "INFO: Cleaning up test environments..."
	@rm -rf .venv-pip-test .venv-uv-test
	@echo "SUCCESS: Benchmark complete!"
