# Development and testing targets

# @SECTION Setup & Installation
install: venv-setup  ## Install dependencies (auto-detects UV/pip, environment-aware)
	@if [ -n "$$CI" ]; then \
		echo "CI detected: using frozen UV sync"; \
		uv sync --frozen --all-groups --quiet; \
	elif command -v uv >/dev/null 2>&1; then \
		echo "UV available"; \
		if echo "$(MAKECMDGOALS)" | grep -q "_dev"; then \
			uv sync --all-groups --quiet; \
		else \
			uv sync --no-dev --quiet; \
		fi; \
	else \
		echo "Fallback to pip"; \
		$(MAKE) _install-pip; \
	fi

dev-install: generate-pyproject venv-setup  ## Install dev dependencies (preserves CI usage)
	@$(MAKE) install _dev

requirements: ## Generate/clean requirements (usage: make requirements _clean)
	@if echo "$(MAKECMDGOALS)" | grep -q "_clean"; then \
		rm -f requirements*.txt; \
	else \
		uv export --no-dev --no-header --output-file requirements.txt; \
		uv export --no-header --output-file requirements-dev.txt; \
	fi

_install-pip: venv-setup  ## Internal pip installation
	@echo "Generating requirements from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	uv export --no-header --output-file requirements-dev.txt
	@echo "Installing with pip..."
	$(BIN)/pip install -r requirements-dev.txt

# Dummy targets for flags
_dev _clean _pip:
	@:

# Backward compatibility aliases (removed duplicates)
install-pip: ; @$(MAKE) install _pip
dev-install-pip: ; @$(MAKE) dev-install _pip  
ci-install: ; @$(MAKE) install  # Environment auto-detected
dev-install-uv: ; @$(MAKE) dev-install  # CRITICAL: Preserve for ci.yml
requirements-generate: ; @$(MAKE) requirements
clean-requirements: ; @$(MAKE) requirements _clean
# Testing targets
test: dev-install  ## Run tests (usage: make test [unit|integration|e2e|coverage|parallel])
	@./dev-tools/testing/test_dispatcher.py $(filter-out $@,$(MAKECMDGOALS))

test-report: dev-install  ## Generate comprehensive test report (PRESERVE: used by ci.yml)
	./dev-tools/testing/run_tests.py --all --coverage --junit-xml=test-results-combined.xml --cov-xml=coverage-combined.xml --html-coverage --maxfail=1 --timeout=60

system-tests: dev-install  ## Run system integration tests
	@uv run python -m pytest tests/onaws/test_onaws.py -v -m manual_aws --no-cov --tb=long

# Backward compatibility aliases (direct calls to avoid loops)
test-unit: dev-install
	@./dev-tools/testing/test_dispatcher.py unit

test-integration: dev-install
	@./dev-tools/testing/test_dispatcher.py integration

test-e2e: dev-install
	@./dev-tools/testing/test_dispatcher.py e2e

test-onaws: dev-install
	@./dev-tools/testing/test_dispatcher.py onaws

test-all: dev-install
	@./dev-tools/testing/test_dispatcher.py all

test-parallel: dev-install
	@./dev-tools/testing/test_dispatcher.py parallel

test-quick: dev-install
	@./dev-tools/testing/test_dispatcher.py unit fast

test-performance: dev-install
	@./dev-tools/testing/test_dispatcher.py performance

test-aws: dev-install
	@./dev-tools/testing/test_dispatcher.py aws

test-cov: dev-install
	@./dev-tools/testing/test_dispatcher.py coverage

test-html: dev-install
	@./dev-tools/testing/test_dispatcher.py html-coverage

test-docker: dev-install  ## Run Docker containerization tests
	@./dev-tools/testing/test-docker.sh

# Dummy targets removed (consolidated in quality.mk)

# @SECTION Development Tools
# @SECTION Development Tools
# Core development workflow
dev: dev-install format lint test-quick  ## Quick development workflow (format, lint, test)
	@echo "Development workflow completed successfully!"

dev-setup: dev-install  ## Set up development environment
	@echo "Development environment setup complete!"
	@echo "Available commands:"
	@echo "  make test          - Run tests"
	@echo "  make docs-serve    - Start documentation server"
	@echo "  make lint          - Run code quality checks"
	@echo "  make format        - Format code"

# Dependency management  
deps-add: ## Add new dependency (usage: make deps-add PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then \
		echo "Error: PACKAGE is required. Usage: make deps-add PACKAGE=package-name"; \
		exit 1; \
	fi
	@uv add $(PACKAGE)

deps-add-dev: ## Add new dev dependency (usage: make deps-add-dev PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then \
		echo "Error: PACKAGE is required. Usage: make deps-add-dev PACKAGE=package-name"; \
		exit 1; \
	fi
	@uv add --dev $(PACKAGE)

deps-update: ## Update dependencies and regenerate lock file
	@uv lock --upgrade

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
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-all: clean  ## Clean everything including virtual environment
	@echo "Cleaning virtual environment..."
	rm -rf $(VENV)/

# Quick start for new developers
quick-start: dev-install create-config  ## Quick start for new developers
	@echo "Running quick start setup..."
	./dev-tools/setup/quick_start.py

# Configuration management
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

# Package management
install-package: dev-install  ## Install package in development mode
	@echo "Installing package in development mode..."
	uv pip install -e .

uninstall-package:  ## Uninstall package
	@echo "Uninstalling package..."
	uv pip uninstall $(PACKAGE_NAME) -y || pip uninstall $(PACKAGE_NAME) -y

reinstall-package: uninstall-package install-package  ## Reinstall package

# Database initialization (if applicable)
init-db:  ## Initialize database (if applicable)
	@echo "Initializing database..."
	# Add database initialization commands here

# UV management targets
uv-lock: generate-pyproject  ## Update uv.lock file
	@echo "Updating uv.lock file..."
	./dev-tools/setup/uv_manager.py lock

uv-sync: generate-pyproject  ## Sync dependencies from uv.lock
	@echo "Syncing dependencies from uv.lock..."
	./dev-tools/setup/uv_manager.py sync

uv-sync-dev: generate-pyproject  ## Sync all dependencies including dev
	@echo "Syncing all dependencies including dev..."
	./dev-tools/setup/uv_manager.py sync-dev

uv-check:  ## Check UV configuration and dependencies
	@echo "Checking UV configuration..."
	./dev-tools/setup/uv_manager.py check

uv-benchmark:  ## Benchmark UV vs pip performance
	@echo "Benchmarking UV vs pip performance..."
	./dev-tools/setup/uv_manager.py benchmark

# Utility targets
generate-pyproject:  ## Update pyproject.toml metadata from .project.yml (preserves dependencies)
	@echo "Updating pyproject.toml metadata from $(PROJECT_CONFIG)..."
	@./dev-tools/package/generate_pyproject.py --config $(PROJECT_CONFIG)

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


