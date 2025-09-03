# Development workflow targets
# Installation, dependencies, testing, formatting, linting

# @SECTION Setup & Installation
install: $(VENV)/bin/activate  ## Install production dependencies (UV-first)
	@echo "Installing production dependencies with UV..."
	uv sync --no-dev

install-pip: $(VENV)/bin/activate  ## Install production dependencies (pip alternative)
	@echo "Generating production requirements from uv.lock..."
	uv export --no-dev --no-header --output-file requirements.txt
	@echo "Installing with pip..."
	$(BIN)/pip install -r requirements.txt

dev-install: generate-pyproject $(VENV)/bin/activate  ## Install development dependencies (UV-first)
	@echo "Installing with UV (all dependencies)..."
	@uv sync --all-groups --quiet

dev-install-pip: generate-pyproject $(VENV)/bin/activate  ## Install development dependencies (pip alternative)
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
	@echo "Generated requirements.txt and requirements-dev.txt"

# Dependency management
deps-update:  ## Update dependencies and regenerate lock file
	@echo "Updating dependencies..."
	uv lock --upgrade

deps-add:  ## Add new dependency (usage: make deps-add PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then echo "Usage: make deps-add PACKAGE=package-name"; exit 1; fi
	./dev-tools/scripts/deps_manager.py add $(PACKAGE)

deps-add-dev:  ## Add new dev dependency (usage: make deps-add-dev PACKAGE=package-name)
	@if [ -z "$(PACKAGE)" ]; then echo "Usage: make deps-add-dev PACKAGE=package-name"; exit 1; fi
	./dev-tools/scripts/deps_manager.py add --dev $(PACKAGE)

# Cleanup
clean-requirements:  ## Remove generated requirements files
	rm -f requirements.txt requirements-dev.txt

# @SECTION Testing
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

test-install: build  ## Test package installation
	./dev-tools/package/test_install.sh

test-completions:         ## Test completion generation
	@echo "Testing bash completion generation..."
	@$(call run-tool,python,src/run.py --completion bash) > /dev/null && echo "SUCCESS: Bash completion generation works"
	@echo "Testing zsh completion generation..."
	@$(call run-tool,python,src/run.py --completion zsh) > /dev/null && echo "SUCCESS: Zsh completion generation works"

# @SECTION Code Quality
# Code quality targets
format: dev-install clean-whitespace  ## Format code with Ruff (no auto-fix)
	@uv run ruff format --check --quiet .

format-fix: clean-whitespace  ## Auto-fix code formatting with Ruff
	@uv run ruff format --quiet .
	@uv run ruff check --fix --exit-zero --quiet .

format-container: ## Format code in container (no local tools needed)
	./dev-tools/scripts/run_dev_checks.sh format

lint: dev-install  ## Check enforced rules (fail on issues)
	@uv run ruff check --quiet .
	@uv run ruff format --check --quiet .

lint-optional: dev-install  ## Check optional rules (warnings only)
	@uv run ruff check --select=N,UP,B,PL,C90,RUF --quiet . || true

pre-commit: format lint validate-workflows  ## Simulate pre-commit checks locally
	@echo "All checks passed! Safe to commit."

pre-commit-check: dev-install  ## Run all pre-commit validation checks
	@echo "Running pre-commit validation checks..."
	./dev-tools/scripts/pre_commit_check.py

pre-commit-check-required: dev-install  ## Run only required pre-commit checks (skip warnings)
	@echo "Running required pre-commit validation checks..."
	./dev-tools/scripts/pre_commit_check.py --required-only

clean-whitespace:  ## Clean whitespace in blank lines from all files
	@echo "Cleaning whitespace in blank lines..."
	$(call run-tool,python,./dev-tools/scripts/clean_whitespace.py)

# Development targets
dev-setup: dev-install  ## Set up development environment
	@echo "Development environment setup complete!"
	@echo "Available commands:"
	@echo "  make test          - Run tests"
	@echo "  make docs-serve    - Start documentation server"
	@echo "  make lint          - Run code quality checks"
	@echo "  make format        - Format code"

dev: dev-install format lint test-quick  ## Quick development workflow (format, lint, test)
	@echo "Development workflow completed successfully!"

# Package management targets
install-package: build  ## Install package locally
	uv pip install dist/*.whl

uninstall-package:  ## Uninstall package
	uv pip uninstall $(PROJECT) -y

reinstall-package: uninstall-package install-package  ## Reinstall package

# UV-specific targets for performance optimization
uv-lock: ## Generate uv.lock file for reproducible builds
	./dev-tools/scripts/uv_manager.py lock

uv-sync: ## Sync environment with uv.lock file
	./dev-tools/scripts/uv_manager.py sync

uv-sync-dev: ## Sync development environment with uv.lock file
	./dev-tools/scripts/uv_manager.py sync-dev

uv-check: ## Check if uv is available and show version
	./dev-tools/scripts/uv_manager.py check

uv-benchmark: ## Benchmark uv vs pip installation speed
	./dev-tools/scripts/uv_manager.py benchmark

# Completion targets
generate-completions:     ## Generate completion scripts (bash and zsh)
	@echo "Generating bash completion..."
	$(call run-tool,python,src/run.py --completion bash) > dev-tools/completions/bash/$(PACKAGE_NAME_SHORT)-completion.bash
	@echo "Generating zsh completion..."
	$(call run-tool,python,src/run.py --completion zsh) > dev-tools/completions/zsh/_$(PACKAGE_NAME_SHORT)
	@echo "SUCCESS: Completion scripts generated in dev-tools/completions/"

install-completions:      ## Install completions for current user
	./dev-tools/scripts/install_completions.sh

install-bash-completions: ## Install bash completions only
	./dev-tools/scripts/install_completions.sh bash

install-zsh-completions:  ## Install zsh completions only
	./dev-tools/scripts/install_completions.sh zsh

uninstall-completions:    ## Remove installed completions
	./dev-tools/scripts/install_completions.sh --uninstall

# Tool installation
install-dev-tools: ## Install all development tools (yq, hadolint, trivy, syft, docker, uv, etc.)
	@echo "Installing development tools for $(shell uname -s)..."
	./dev-tools/scripts/install_dev_tools.py

install-dev-tools-required: ## Install only required development tools (yq, uv, docker)
	@echo "Installing required development tools..."
	./dev-tools/scripts/install_dev_tools.py --required-only

install-dev-tools-dry-run: ## Show what development tools would be installed
	@echo "Checking what development tools would be installed..."
	./dev-tools/scripts/install_dev_tools.py --dry-run

# Application targets
run: install  ## Run application
	$(call run-tool,python,src/run.py)

run-dev: dev-install  ## Run application in development mode
	$(call run-tool,python,src/run.py --log-level DEBUG)

# Database targets (if needed)
init-db: install  ## Initialize database
	$(call run-tool,python,src/run.py system init-db)

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
	$(call run-tool,python,src/run.py config validate)

# Quick start for new developers
quick-start: ## Complete setup for new developers (install tools + dependencies + verify)
	./dev-tools/scripts/quick_start.py
