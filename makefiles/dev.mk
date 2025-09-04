# Development and testing targets

# @SECTION Setup & Installation
install: venv-setup  ## Install production dependencies (UV-first)
	@echo "Installing production dependencies with UV..."
	uv sync --no-dev

install-pip: venv-setup  ## Install production dependencies (pip alternative)
	@echo "Generating production requirements from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	@echo "Installing with pip..."
	$(BIN)/pip install -r requirements.txt

dev-install: generate-pyproject venv-setup  ## Install development dependencies (UV-first)
	@echo "Installing with UV (all dependencies)..."
	@uv sync --all-groups --quiet

dev-install-pip: generate-pyproject venv-setup  ## Install development dependencies (pip alternative)
	@echo "Generating requirements from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	uv export --no-header --output-file requirements-dev.txt
	@echo "Installing with pip..."
	pip install -r requirements-dev.txt

# CI installation targets
ci-install: generate-pyproject  ## Install dependencies for CI (UV frozen)
	@echo "Installing with UV (frozen mode - all dependencies)..."
	@uv sync --frozen --all-groups --quiet

# Requirements generation
requirements-generate:  ## Generate requirements files from uv.lock
	@echo "Generating requirements files from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	uv export --no-header --output-file requirements-dev.txt

clean-requirements:  ## Remove generated requirements files
	rm -f requirements.txt requirements-dev.txt
# Testing targets (using enhanced dispatcher)
test: dev-install  ## Run tests (supports: make test path/to/tests -k pattern -v)
	@./dev-tools/testing/run_tests.py $(filter-out $@,$(MAKECMDGOALS))

test-unit: dev-install  ## Run unit tests (supports same args: make test-unit path -v)
	@./dev-tools/testing/run_tests.py --unit $(filter-out $@,$(MAKECMDGOALS))

test-integration: dev-install  ## Run integration tests (supports same args)
	@./dev-tools/testing/run_tests.py --integration $(filter-out $@,$(MAKECMDGOALS))

test-e2e: dev-install  ## Run end-to-end tests (supports same args)
	@./dev-tools/testing/run_tests.py --e2e $(filter-out $@,$(MAKECMDGOALS))

test-all: dev-install  ## Run all tests (supports same args)
	@./dev-tools/testing/run_tests.py --all $(filter-out $@,$(MAKECMDGOALS))

test-parallel: dev-install  ## Run tests in parallel (supports same args)
	@./dev-tools/testing/run_tests.py --parallel $(filter-out $@,$(MAKECMDGOALS))

test-quick: dev-install  ## Run quick test suite (supports same args)
	@./dev-tools/testing/run_tests.py --unit --fast $(filter-out $@,$(MAKECMDGOALS))

test-performance: dev-install  ## Run performance tests (supports same args)
	@./dev-tools/testing/run_tests.py --markers slow $(filter-out $@,$(MAKECMDGOALS))

test-aws: dev-install  ## Run AWS-specific tests (supports same args)
	@./dev-tools/testing/run_tests.py --markers aws $(filter-out $@,$(MAKECMDGOALS))

test-cov: dev-install  ## Run tests with coverage (supports same args)
	@./dev-tools/testing/run_tests.py --coverage $(filter-out $@,$(MAKECMDGOALS))

test-html: dev-install  ## Run tests with HTML coverage (supports same args)
	@./dev-tools/testing/run_tests.py --html-coverage $(filter-out $@,$(MAKECMDGOALS))
	@echo "Coverage report generated in htmlcov/index.html"

# Dummy target to prevent "No rule to make target" errors
%:
	@:

test-report: dev-install  ## Generate comprehensive test report
	./dev-tools/testing/run_tests.py --all --coverage --junit-xml=test-results-combined.xml --cov-xml=coverage-combined.xml --html-coverage --maxfail=1 --timeout=60

# @SECTION Development Tools
generate-pyproject:  ## Generate pyproject.toml from template using project config
	@echo "Generating pyproject.toml from template using $(PROJECT_CONFIG)..."
	@./dev-tools/scripts/generate_pyproject.py --config $(PROJECT_CONFIG)

deps-add:  ## Add new dependency (usage: make deps-add PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then \
		echo "Error: PACKAGE is required. Usage: make deps-add PACKAGE=package-name"; \
		exit 1; \
	fi
	./dev-tools/scripts/deps_manager.py add $(PACKAGE)

deps-add-dev:  ## Add new dev dependency (usage: make deps-add-dev PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then \
		echo "Error: PACKAGE is required. Usage: make deps-add-dev PACKAGE=package-name"; \
		exit 1; \
	fi
	./dev-tools/scripts/deps_manager.py add --dev $(PACKAGE)

# Cleanup
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
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-all: clean  ## Clean everything including virtual environment
	@echo "Cleaning virtual environment..."
	rm -rf $(VENV)/

# Development targets
dev-setup: dev-install  ## Set up development environment
	@echo "Development environment setup complete!"
	@echo "Available commands:"
	@echo "  make test          - Run tests"
	@echo "  make docs-serve    - Start documentation server"
	@echo "  make lint          - Run code quality checks"
	@echo "  make format        - Format code"

install-package: dev-install  ## Install package in development mode
	@echo "Installing package in development mode..."
	uv pip install -e .

uninstall-package:  ## Uninstall package
	@echo "Uninstalling package..."
	uv pip uninstall $(PACKAGE_NAME) -y || pip uninstall $(PACKAGE_NAME) -y

reinstall-package: uninstall-package install-package  ## Reinstall package

init-db:  ## Initialize database (if applicable)
	@echo "Initializing database..."
	# Add database initialization commands here

create-config:  ## Create default configuration file
	@echo "Creating default configuration..."
	@mkdir -p config
	@echo '{"debug": false, "log_level": "INFO"}' > $(CONFIG)

validate-config:  ## Validate configuration file
	@echo "Validating configuration..."
	@if [ -f "$(CONFIG)" ]; then \
		echo "Configuration file exists: $(CONFIG)"; \
		python -m json.tool $(CONFIG) > /dev/null && echo "Configuration is valid JSON"; \
	else \
		echo "Configuration file not found: $(CONFIG)"; \
		echo "Run 'make create-config' to create a default configuration"; \
	fi

quick-start: dev-install create-config  ## Quick start for new developers
	@echo "Running quick start setup..."
	./dev-tools/scripts/quick_start.py

dev: dev-install format lint test-quick  ## Quick development workflow (format, lint, test)
	@echo "Development workflow completed successfully!"

# Show project status
status:  ## Show project status and useful commands
	@echo "=== $(PACKAGE_NAME) v$(VERSION) Status ==="
	@echo ""
	@echo "Python version: $(DEFAULT_PYTHON_VERSION)"
	@echo "Package: $(PACKAGE_NAME)"
	@echo "Version: $(VERSION)"
	@echo ""
	@echo "Available commands:"
	@echo "  make dev-setup     - Set up development environment"
	@echo "  make test          - Run tests"
	@echo "  make lint          - Run linting"
	@echo "  make format        - Format code"
	@echo "  make docs-serve    - Start documentation server"
	@echo "  make clean         - Clean build artifacts"

# UV management targets
uv-lock: generate-pyproject  ## Update uv.lock file
	@echo "Updating uv.lock file..."
	./dev-tools/scripts/uv_manager.py lock

uv-sync: generate-pyproject  ## Sync dependencies from uv.lock
	@echo "Syncing dependencies from uv.lock..."
	./dev-tools/scripts/uv_manager.py sync

uv-sync-dev: generate-pyproject  ## Sync all dependencies including dev
	@echo "Syncing all dependencies including dev..."
	./dev-tools/scripts/uv_manager.py sync-dev

uv-check:  ## Check UV configuration and dependencies
	@echo "Checking UV configuration..."
	./dev-tools/scripts/uv_manager.py check

deps-update:  ## Update dependencies and regenerate lock file
	@echo "Updating dependencies..."
	uv lock --upgrade

clean-requirements:  ## Remove generated requirements files
	rm -f requirements.txt requirements-dev.txt

show-package-info:  ## Show package information
	@echo "Package Name: $(PACKAGE_NAME)"
	@echo "Short Name: $(PACKAGE_NAME_SHORT)"
	@echo "Version: $(VERSION)"
	@echo "Author: $(AUTHOR)"
	@echo "License: $(LICENSE)"
	@echo "Repository: $(REPO_URL)"
	@echo "Container Registry: $(CONTAINER_REGISTRY)"

print-json-PYTHON_VERSIONS:  ## Print Python versions as JSON (for CI)
	@echo '$(PYTHON_VERSIONS)' | tr ' ' '\n' | jq -R . | jq -s .

uv-benchmark:  ## Benchmark UV vs pip performance
	@echo "Benchmarking UV vs pip performance..."
	./dev-tools/scripts/uv_manager.py benchmark
