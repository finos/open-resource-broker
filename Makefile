# Makefile for Open Host Factory Plugin

.PHONY: help install install-pip install-uv dev-install dev-install-pip dev-install-uv test test-unit test-integration test-e2e test-all test-cov test-html test-parallel test-quick test-performance test-aws test-report lint format security security-container security-full security-scan security-validate-sarif security-report sbom-generate clean clean-all build build-test docs docs-build docs-serve docs-deploy docs-clean docs-deploy-version docs-list-versions docs-delete-version ci-docs-build ci-docs-build-for-pages ci-docs-deploy run run-dev version-show version-bump version-bump-patch version-bump-minor version-bump-major generate-pyproject ci-quality ci-security ci-security-codeql ci-security-container ci-architecture ci-imports ci-tests-unit ci-tests-integration ci-tests-e2e ci-tests-matrix ci-tests-performance ci-check ci-check-quick ci-check-fix ci-check-verbose ci ci-quick workflow-ci workflow-test-matrix workflow-security architecture-check architecture-report quality-check quality-check-fix quality-check-files quality-gates quality-full generate-completions install-completions install-bash-completions install-zsh-completions uninstall-completions test-completions dev-setup install-package uninstall-package reinstall-package init-db create-config validate-config docker-build docker-run docker-compose-up docker-compose-down dev status uv-lock uv-sync uv-sync-dev uv-check uv-benchmark file-sizes file-sizes-report

# Python settings
PYTHON := python3
VENV := .venv
BIN := $(VENV)/bin

# Project configuration (single source of truth)
PROJECT_CONFIG := .project.yml

# Python version settings (loaded from project config)
PYTHON_VERSIONS := $(shell yq '.python.versions | join(" ")' $(PROJECT_CONFIG))
DEFAULT_PYTHON_VERSION := $(shell yq '.python.default_version' $(PROJECT_CONFIG))

# Generate pyproject.toml from template with project configuration
generate-pyproject:  ## Generate pyproject.toml from template using project config
	@echo "Generating pyproject.toml from template using $(PROJECT_CONFIG)..."
	@./dev-tools/scripts/generate-pyproject.py --config $(PROJECT_CONFIG)

# Package information (loaded from project config)
PACKAGE_NAME := $(shell yq '.project.name' $(PROJECT_CONFIG))
PACKAGE_NAME_SHORT := $(shell yq '.project.short_name' $(PROJECT_CONFIG))
VERSION := $(shell yq '.project.version' $(PROJECT_CONFIG))

# Repository information (loaded from project config)
REPO_ORG := $(shell yq '.repository.org' $(PROJECT_CONFIG))
CONTAINER_REGISTRY := $(shell yq '.repository.registry' $(PROJECT_CONFIG))/$(REPO_ORG)
CONTAINER_IMAGE := $(PACKAGE_NAME)
DOCS_URL := https://$(REPO_ORG).github.io/$(PACKAGE_NAME)

# Project settings
PROJECT := $(PACKAGE_NAME)
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

# Centralized tool execution function
# Usage: $(call run-tool,tool-name,arguments)
define run-tool
	@dev-tools/scripts/run_tool.sh $(1) $(2)
endef

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Installation targets
install: $(VENV)/bin/activate  ## Install production dependencies (UV-first)
	@echo "Installing production dependencies with UV..."
	uv sync --no-dev

install-pip: $(VENV)/bin/activate  ## Install production dependencies (pip alternative)
	@echo "Generating production requirements from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	@echo "Installing with pip..."
	$(BIN)/pip install -r requirements.txt

dev-install: generate-pyproject $(VENV)/bin/activate  ## Install development dependencies (UV-first)
	@echo "Installing with UV (dev + ci dependencies)..."
	uv sync --group ci --group dev

dev-install-pip: generate-pyproject $(VENV)/bin/activate  ## Install development dependencies (pip alternative)
	@echo "Generating requirements from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	uv export --no-header --output-file requirements-dev.txt
	@echo "Installing with pip..."
	pip install -r requirements-dev.txt

# CI installation targets
ci-install: generate-pyproject  ## Install dependencies for CI (UV frozen)
	@echo "Installing with UV (frozen mode - CI dependencies)..."
	uv sync --frozen --group ci

ci-install-pip: generate-pyproject  ## Install dependencies for CI (pip alternative)
	@echo "Generating requirements from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	uv export --group ci --no-header --output-file requirements-ci.txt
	pip install -e ".[ci]"
	@echo "Installing with pip..."
	pip install -r requirements-dev.txt

# Requirements generation
requirements-generate:  ## Generate requirements files from uv.lock
	@echo "Generating requirements files from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	uv export --no-header --output-file requirements-dev.txt
	@echo "Generated requirements.txt and requirements-dev.txt"

# Dependency management
deps-update:  ## Update dependencies and regenerate lock file
	@echo "Updating dependencies..."
	uv lock --upgrade

deps-add:  ## Add new dependency (usage: make deps-add PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then \
		echo "Error: PACKAGE is required. Usage: make deps-add PACKAGE=package-name"; \
		exit 1; \
	fi
	uv add $(PACKAGE)

deps-add-dev:  ## Add new dev dependency (usage: make deps-add-dev PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then \
		echo "Error: PACKAGE is required. Usage: make deps-add-dev PACKAGE=package-name"; \
		exit 1; \
	fi
	uv add --dev $(PACKAGE)

# Cleanup
clean-requirements:  ## Remove generated requirements files
	rm -f requirements.txt requirements-dev.txt

$(VENV)/bin/activate: uv.lock
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@if command -v uv >/dev/null 2>&1; then \
		echo "INFO: Using uv for virtual environment setup..."; \
		uv pip install --upgrade pip; \
	else \
		echo "INFO: Using pip for virtual environment setup..."; \
		$(BIN)/pip install --upgrade pip; \
	fi
	touch $(VENV)/bin/activate

# Testing targets (using dev-tools)
test: test-quick  ## Run quick test suite (alias for test-quick)

test-unit: dev-install  ## Run unit tests only
	./dev-tools/testing/run_tests.py --unit

test-integration: dev-install  ## Run integration tests only
	./dev-tools/testing/run_tests.py --integration

test-e2e: dev-install  ## Run end-to-end tests only
	./dev-tools/testing/run_tests.py --e2e

test-all: dev-install  ## Run all tests
	./dev-tools/testing/run_tests.py

test-parallel: dev-install  ## Run tests in parallel
	./dev-tools/testing/run_tests.py --parallel

test-quick: dev-install  ## Run quick test suite (unit + fast integration)
	./dev-tools/testing/run_tests.py --unit --fast

test-performance: dev-install  ## Run performance tests
	./dev-tools/testing/run_tests.py --markers slow

test-aws: dev-install  ## Run AWS-specific tests
	./dev-tools/testing/run_tests.py --markers aws

test-cov: dev-install  ## Run tests with coverage report
	./dev-tools/testing/run_tests.py --coverage

test-html: dev-install  ## Run tests with HTML coverage report
	./dev-tools/testing/run_tests.py --html-coverage
	@echo "Coverage report generated in htmlcov/index.html"

test-report: dev-install  ## Generate comprehensive test report
	./dev-tools/testing/run_tests.py --coverage --html-coverage

# Code quality targets
quality-check: dev-install  ## Run professional quality checks
	@echo "Running professional quality checks..."
	./dev-tools/scripts/quality_check.py --strict

quality-check-fix: dev-install  ## Run quality checks with auto-fix
	@echo "Running professional quality checks with auto-fix..."
	./dev-tools/scripts/quality_check.py --fix

quality-check-files: dev-install  ## Run quality checks on specific files (usage: make quality-check-files FILES="file1.py file2.py")
	@if [ -z "$(FILES)" ]; then \
		echo "Error: FILES is required. Usage: make quality-check-files FILES=\"file1.py file2.py\""; \
		exit 1; \
	fi
	@echo "Running professional quality checks on specified files..."
	./dev-tools/scripts/quality_check.py --strict --files $(FILES)

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

security-container: dev-install ## Run container security scans
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
	./dev-tools/security/security_scan.py

security-validate-sarif: dev-install  ## Validate SARIF files
	@echo "Validating SARIF files..."
	./dev-tools/security/validate_sarif.py *.sarif

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
	./dev-tools/scripts/validate_cqrs.py --warn-only
	./dev-tools/scripts/check_architecture.py --warn-only

architecture-report: dev-install  ## Generate detailed architecture report
	@echo "Generating architecture dependency report..."
	./dev-tools/scripts/check_architecture.py --report

# Architecture Documentation Generation
quality-gates: lint test architecture-check  ## Run all quality gates
	@echo "All quality gates completed successfully!"

quality-full: lint test architecture-check docs-build  ## Run quality gates and generate docs
	@echo "Full quality check and documentation generation completed!"

# Completion targets
generate-completions:     ## Generate completion scripts (bash and zsh)
	@echo "Generating bash completion..."
	$(PYTHON) src/run.py --completion bash > dev-tools/completions/bash/$(PACKAGE_NAME_SHORT)-completion.bash
	@echo "Generating zsh completion..."
	$(PYTHON) src/run.py --completion zsh > dev-tools/completions/zsh/_$(PACKAGE_NAME_SHORT)
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
docs: docs-build  ## Build documentation (main docs entry point)

docs-build: dev-install  ## Build documentation locally with mike (no push)
	@echo "Building documentation locally with mike..."
	cd $(DOCS_DIR) && ../$(BIN)/mike deploy --update-aliases latest
	@echo "Documentation built with mike versioning"

ci-docs-build:  ## Build documentation for CI PR testing (matches docs.yml PR builds)
	@dev-tools/scripts/ci_docs_build.sh

ci-docs-build-for-pages:  ## Build documentation for GitHub Pages deployment (no push)
	@dev-tools/scripts/ci_docs_build_for_pages.sh

docs-serve: dev-install  ## Serve versioned documentation locally with live reload
	@echo "Starting versioned documentation server at http://127.0.0.1:8000"
	@echo "Press Ctrl+C to stop the server"
	@if [ ! -f "$(BIN)/mike" ]; then \
		echo "Mike not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike serve

docs-deploy: dev-install  ## Deploy documentation locally (for testing deployment)
	@echo "Deploying documentation locally with mike..."
	@echo "WARNING: This will commit to your local gh-pages branch"
	cd $(DOCS_DIR) && ../$(BIN)/mike deploy --update-aliases latest
	@echo "Documentation deployed locally. Use 'git push origin gh-pages' to publish."

ci-docs-deploy:  ## Deploy documentation to GitHub Pages (matches docs.yml main branch)
	@dev-tools/scripts/ci_docs_deploy.sh

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

docs-clean:  ## Clean documentation build files
	rm -rf $(DOCS_BUILD_DIR)

# Version management targets
version-show:  ## Show current version from project config
	@echo "Current version: $(VERSION)"

version-bump-patch:  ## Bump patch version (1.0.0 -> 1.0.1)
	@./dev-tools/package/version-bump.sh patch

version-bump-minor:  ## Bump minor version (1.0.0 -> 1.1.0)
	@./dev-tools/package/version-bump.sh minor

version-bump-major:  ## Bump major version (1.0.0 -> 2.0.0)
	@./dev-tools/package/version-bump.sh major

version-bump:  ## Show version bump help
	@echo "Version Management Commands:"
	@echo "  make version-show         - Show current version"
	@echo "  make version-bump-patch   - Bump patch version (1.0.0 -> 1.0.1)"
	@echo "  make version-bump-minor   - Bump minor version (1.0.0 -> 1.1.0)"
	@echo "  make version-bump-major   - Bump major version (1.0.0 -> 2.0.0)"
	@echo ""
	@echo "Current version: $(VERSION)"

# Build targets (using dev-tools)
build: generate-pyproject clean dev-install  ## Build package
	./dev-tools/package/build.sh

build-test: build  ## Build and test package installation
	./dev-tools/package/test-install.sh

# CI/CD targets
# Individual code quality targets (with tool names)
ci-quality-black:  ## Run Black code formatting check
	@echo "Running Black formatting check..."
	$(call run-tool,black,--check src/ tests/)

ci-quality-isort:  ## Run isort import sorting check
	@echo "Running isort import check..."
	$(call run-tool,isort,--check-only src/ tests/)

ci-quality-flake8:  ## Run flake8 style guide check
	@echo "Running flake8 style check..."
	$(call run-tool,flake8,src/ tests/)

ci-quality-mypy:  ## Run mypy type checking
	@echo "Running mypy type check..."
	$(call run-tool,mypy,src/)

ci-quality-pylint:  ## Run pylint code analysis
	@echo "Running pylint analysis..."
	$(call run-tool,pylint,src/)

ci-quality-radon:  ## Run radon complexity analysis
	@echo "Running radon complexity analysis..."
	$(call run-tool,radon,cc src/ --min B --show-complexity)
	$(call run-tool,radon,mi src/ --min B)

# Composite target (for local convenience)
ci-quality: ci-quality-black ci-quality-isort ci-quality-flake8 ci-quality-mypy ci-quality-pylint ci-quality-radon  ## Run all code quality checks

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

file-sizes: dev-install  ## Check file sizes (developer-friendly alias)
	./dev-tools/scripts/check_file_sizes.py --warn-only

file-sizes-report: dev-install  ## Generate detailed file size report
	./dev-tools/scripts/check_file_sizes.py --report

# Composite target
ci-architecture: ci-arch-cqrs ci-arch-clean ci-arch-imports ci-arch-file-sizes  ## Run all architecture checks

# Individual security targets (with tool names)
ci-security-bandit:  ## Run Bandit security scan
	@echo "Running Bandit security scan..."
	$(call run-tool,bandit,-r src/)

ci-security-safety:  ## Run Safety dependency scan
	@echo "Running Safety dependency scan..."
	$(call run-tool,safety,check)

ci-security-trivy:  ## Run Trivy container scan
	@echo "Running Trivy container scan..."
	@if command -v docker >/dev/null 2>&1; then \
		docker build -t security-scan:latest .; \
		docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
			-v $(PWD):/workspace aquasec/trivy:latest image security-scan:latest; \
	else \
		echo "Docker not available - Trivy requires Docker"; \
	fi

ci-security-hadolint:  ## Run Hadolint Dockerfile scan
	@echo "Running Hadolint Dockerfile scan..."
	@if command -v hadolint >/dev/null 2>&1; then \
		hadolint Dockerfile; \
	else \
		echo "Hadolint not available - install with: brew install hadolint"; \
	fi

# Composite target
ci-security: ci-security-bandit ci-security-safety  ## Run all security scans

ci-build-sbom:  ## Generate SBOM files (matches publish.yml workflow)
	@echo "Generating SBOM files for CI..."
	@echo "This matches the GitHub Actions publish.yml workflow exactly"
	$(MAKE) sbom-generate

ci-tests-unit:  ## Run unit tests only (matches ci.yml unit-tests job)
	@echo "Running unit tests..."
	$(call run-tool,pytest,tests/unit/ $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-unit.xml --junitxml=junit-unit.xml)

ci-tests-integration:  ## Run integration tests only (matches ci.yml integration-tests job)
	@echo "Running integration tests..."
	$(call run-tool,pytest,tests/integration/ $(PYTEST_ARGS) --junitxml=junit-integration.xml)

ci-tests-e2e:  ## Run end-to-end tests only (matches ci.yml e2e-tests job)
	@echo "Running end-to-end tests..."
	$(call run-tool,pytest,tests/e2e/ $(PYTEST_ARGS) --junitxml=junit-e2e.xml)

ci-tests-matrix:  ## Run comprehensive test matrix (matches test-matrix.yml workflow)
	@echo "Running comprehensive test matrix..."
	$(call run-tool,pytest,tests/ $(PYTEST_ARGS) $(PYTEST_COV_ARGS) --cov-report=xml:coverage-matrix.xml --junitxml=junit-matrix.xml)

ci-tests-performance:  ## Run performance tests only (matches ci.yml performance-tests job)
	@echo "Running performance tests..."
	$(call run-tool,pytest,tests/performance/ $(PYTEST_ARGS) --junitxml=junit-performance.xml)

ci-check:  ## Run comprehensive CI checks (matches GitHub Actions exactly)
	@echo "Running comprehensive CI checks that match GitHub Actions pipeline..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture
	$(MAKE) ci-tests-unit

ci-check-quick:  ## Run quick CI checks (fast checks only)
	@echo "Running quick CI checks..."
	$(MAKE) ci-quality
	$(MAKE) ci-architecture

ci-check-fix:  ## Run CI checks with automatic formatting fixes
	@echo "Running CI checks with automatic fixes..."
	$(call run-tool,black,src/ tests/)
	$(call run-tool,isort,src/ tests/)
	$(MAKE) ci-quality

ci-check-verbose:  ## Run CI checks with verbose output
	@echo "Running CI checks with verbose output..."
	./dev-tools/scripts/ci_check.py --verbose

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
	rm -f pyproject.toml
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
	REGISTRY=$(CONTAINER_REGISTRY) \
	VERSION=$(VERSION) \
	IMAGE_NAME=$(CONTAINER_IMAGE) \
	./dev-tools/scripts/container-build.sh

docker-run:  ## Run Docker container
	docker run -p 8000:8000 $(PROJECT):latest

docker-compose-up:  ## Start with docker-compose
	docker-compose -f deployment/docker/docker-compose.yml up -d

docker-compose-down:  ## Stop docker-compose
	docker-compose -f deployment/docker/docker-compose.yml down

# Container build targets (multi-Python support)
container-build-multi: dev-install  ## Build container images for all Python versions
	@for py_ver in $(PYTHON_VERSIONS); do \
		echo "Building container for Python $$py_ver..."; \
		REGISTRY=$(CONTAINER_REGISTRY) \
		VERSION=$(VERSION) \
		IMAGE_NAME=$(CONTAINER_IMAGE) \
		PYTHON_VERSION=$$py_ver \
		MULTI_PYTHON=true \
		./dev-tools/scripts/container-build.sh; \
	done
	@echo "Tagging default Python $(DEFAULT_PYTHON_VERSION) as latest..."
	@docker tag $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$(DEFAULT_PYTHON_VERSION) $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)

container-build-single: dev-install  ## Build container image for single Python version (usage: make container-build-single PYTHON_VERSION=3.11)
	@if [ -z "$(PYTHON_VERSION)" ]; then \
		echo "Error: PYTHON_VERSION is required. Usage: make container-build-single PYTHON_VERSION=3.11"; \
		exit 1; \
	fi
	REGISTRY=$(CONTAINER_REGISTRY) \
	VERSION=$(VERSION) \
	IMAGE_NAME=$(CONTAINER_IMAGE) \
	PYTHON_VERSION=$(PYTHON_VERSION) \
	./dev-tools/scripts/container-build.sh

container-push-multi: container-build-multi  ## Push all container images to registry
	@for py_ver in $(PYTHON_VERSIONS); do \
		echo "Pushing $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$$py_ver"; \
		docker push $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$$py_ver; \
	done
	@echo "Pushing $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)"
	@docker push $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)

container-show-version:  ## Show current version and tags that would be created
	@echo "Package & Version Information"
	@echo "============================="
	@echo "Package Name: $(CONTAINER_IMAGE)"
	@echo "Version: $(VERSION)"
	@echo "Registry: $(CONTAINER_REGISTRY)"
	@echo "Python Versions: $(PYTHON_VERSIONS)"
	@echo "Default Python: $(DEFAULT_PYTHON_VERSION)"
	@echo ""
	@echo "Container tags that would be created:"
	@for py_ver in $(PYTHON_VERSIONS); do \
		echo "  - $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$$py_ver"; \
	done
	@echo "  - $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION) (default: Python $(DEFAULT_PYTHON_VERSION))"

# Configuration management targets
show-package-info:  ## Show current package and version metadata
	@echo "Package Information:"
	@echo "  Name: $(PACKAGE_NAME)"
	@echo "  Version: $(VERSION)"
	@echo "  CLI Command: $(PACKAGE_NAME_SHORT)"
	@echo "  Repository: $(REPO_ORG)/$(PACKAGE_NAME)"
	@echo "  Container Registry: $(CONTAINER_REGISTRY)"
	@echo "  Documentation: $(DOCS_URL)"

# Quick development workflow
dev: dev-install format lint test-quick  ## Quick development workflow (format, lint, test)
	@echo "Development workflow completed successfully!"

# Show project status
status:  ## Show project status and useful commands
	@echo "=== $(PACKAGE_NAME) v$(VERSION) Status ==="
	@echo ""
	@echo "Project Structure:"
	@echo "  Source code:     $(PACKAGE)/"
	@echo "  Tests:          $(TESTS)/"
	@echo "  Documentation:  $(DOCS_DIR)/"
	@echo "  Dev tools:      dev-tools/"
	@echo ""
	@echo "Package Information:"
	@echo "  Name:           $(PACKAGE_NAME)"
	@echo "  Version:        $(VERSION)"
	@echo "  CLI Command:    $(PACKAGE_NAME_SHORT)"
	@echo "  Repository:     $(REPO_ORG)/$(PACKAGE_NAME)"
	@echo ""
	@echo "Quick Commands:"
	@echo "  make dev-setup     - Set up development environment"
	@echo "  make test          - Run tests"
	@echo "  make docs          - Build documentation"
	@echo "  make docs-serve    - Start documentation server"
	@echo "  make dev           - Quick development workflow"
	@echo ""
	@echo "Documentation:"
	@echo "  Local docs:     make docs-serve (versioned)"
	@echo "  GitHub Pages:   $(DOCS_URL)"
	@echo "  Deploy docs:    make docs-deploy (local) or make ci-docs-deploy (CI)"
	@echo "  List versions:  make docs-list-versions"
	@echo "  Deploy version: make docs-deploy-version VERSION=1.0.0"
	@echo ""
	@echo "Version Management:"
	@echo "  Patch version:  make version-bump-patch"
	@echo "  Minor version:  make version-bump-minor"
	@echo "  Major version:  make version-bump-major"
	@echo ""
	@echo "Container Management:"
	@echo "  Show info:      make container-show-version"
	@echo "  Build single:   make container-build-single PYTHON_VERSION=3.11"
	@echo "  Build all:      make container-build-multi"

# Print variable targets for CI integration
print-%:
	@echo $($*)

# JSON print targets for GitHub Actions (handles both single values and lists)
print-json-PYTHON_VERSIONS:
	@echo "$(PYTHON_VERSIONS)" | tr ' ' '\n' | jq -R . | jq -s .

print-json-%:
	@value="$($*)"; \
	if echo "$$value" | grep -q " "; then \
		echo "$$value" | tr ' ' '\n' | jq -R . | jq -s .; \
	else \
		echo "$$value" | jq -R .; \
	fi

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
