# Quality assurance targets
# Security, architecture, validation, and quality gates

# @SECTION Code Quality
quality: dev-install  ## Run quality checks (usage: make quality [all] [fix] [files])
	@./dev-tools/quality/quality_dispatcher.py $(filter-out $@,$(MAKECMDGOALS))

format: dev-install  ## Format code (usage: make format [fix])
	@if echo "$(MAKECMDGOALS)" | grep -q "fix"; then \
		./dev-tools/quality/quality_dispatcher.py fix; \
	else \
		uv run ruff format --check --quiet .; \
	fi

lint: dev-install  ## Lint code (usage: make lint [optional])
	@if echo "$(MAKECMDGOALS)" | grep -q "optional"; then \
		uv run ruff check --select=N,UP,B,PL,C90,RUF --quiet . || true; \
	else \
		uv run ruff check --quiet .; \
	fi

validate: lint test  ## Run all validation checks
	@echo "All validation checks passed!"

hadolint: dev-install  ## Check Dockerfile with hadolint
	@./dev-tools/scripts/dev_tools_runner.py hadolint-check

# Dummy targets for flags (consolidated)
all fix files optional quick unit integration e2e onaws parallel fast coverage html-coverage performance aws single multi push version serve deploy list:
	@:

# Backward compatibility aliases
quality-check: dev-install
	@./dev-tools/quality/quality_dispatcher.py

quality-check-all: dev-install  # CRITICAL: Used by ci.yml
	@./dev-tools/quality/quality_dispatcher.py all

quality-check-fix: dev-install
	@./dev-tools/quality/quality_dispatcher.py fix

quality-check-files: dev-install
	@./dev-tools/quality/quality_dispatcher.py files $(FILES)

format-fix: dev-install  # CRITICAL: Used by ci.yml
	@./dev-tools/quality/quality_dispatcher.py fix

lint-optional: dev-install
	@$(MAKE) lint optional

pre-commit: ; @$(MAKE) validate
hadolint-check: ; @$(MAKE) hadolint

dev-checks-container: dev-install  ## Run development checks in container
	./dev-tools/scripts/run_dev_checks.sh all

dev-checks-container-required: dev-install  ## Run required development checks in container
	./dev-tools/scripts/run_dev_checks.sh required

format-container: dev-install  ## Format code in container
	./dev-tools/scripts/run_dev_checks.sh format

hadolint-check-container: dev-install  ## Check Dockerfile with hadolint in container
	./dev-tools/scripts/run_dev_checks.sh all

install-dev-tools: dev-install  ## Install all development tools
	./dev-tools/scripts/install_dev_tools.py

install-dev-tools-required: dev-install  ## Install only required development tools
	./dev-tools/scripts/install_dev_tools.py --required-only

install-dev-tools-dry-run: dev-install  ## Show what development tools would be installed
	@echo "Checking what development tools would be installed..."
	./dev-tools/scripts/install_dev_tools.py --dry-run

clean-whitespace:  ## Clean whitespace in blank lines from all files
	@echo "Cleaning whitespace in blank lines..."
	./dev-tools/scripts/dev_tools_runner.py clean-whitespace

# @SECTION Security
security: dev-install  ## Run security checks (usage: make security [quick] [all])
	@./dev-tools/scripts/security_check.py $(if $(findstring quick,$(MAKECMDGOALS)),--quick,) $(if $(findstring all,$(MAKECMDGOALS)),--all,)

sbom-generate: dev-install ## Generate Software Bill of Materials (SBOM)
	@echo "Generating SBOM files..."
	@echo "Ensuring required tools are installed..."
	./dev-tools/scripts/install_dev_tools.py --tool syft
	./dev-tools/scripts/install_dev_tools.py --tool pip-audit
	@echo "Generating Python dependency SBOM..."
	$(call run-tool,pip-audit,--format=cyclonedx-json --output=python-sbom-cyclonedx.json)
	$(call run-tool,pip-audit,--format=json --output=python-sbom.json)
	@echo "SBOM files generated successfully"

security-report: security sbom-generate  ## Generate comprehensive security report
	@echo "## Security Report Generated" > security-report.md

# Backward compatibility aliases
security-quick: ; @$(MAKE) security quick
security-all: ; @$(MAKE) security all
security-with-container: ; @$(MAKE) security all
security-full: ; @$(MAKE) security all
security-scan: ; @$(MAKE) security

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

quality-full: quality-gates docs-build  ## Run all quality gates and generate docs

file-sizes: dev-install  ## Check file sizes (developer-friendly alias)
	./dev-tools/scripts/dev_tools_runner.py check-file-sizes --warn-only

file-sizes-report: dev-install  ## Generate detailed file size report
	./dev-tools/scripts/dev_tools_runner.py check-file-sizes

validate-workflow-syntax: dev-install  ## Validate GitHub Actions workflow YAML syntax
	@echo "Validating workflow files..."
	# Use 'uv run' because this script imports PyYAML (third-party package)
	# Other dev-tools scripts use only standard library so work with shebang
	uv run ./dev-tools/scripts/validate_workflows.py

validate-workflow-logic: dev-install  ## Validate GitHub Actions workflows with actionlint
	@echo "Validating workflows with actionlint..."
	@echo "Ensuring actionlint is installed..."
	./dev-tools/scripts/install_dev_tools.py --tool actionlint
	./dev-tools/scripts/validate_actionlint.py

validate-shell-scripts: dev-install  ## Validate shell scripts with shellcheck
	@echo "Validating shell scripts with shellcheck..."
	@echo "Ensuring shellcheck is installed..."
	./dev-tools/scripts/install_dev_tools.py --tool shellcheck
	./dev-tools/scripts/validate_shell_scripts.py

validate-all-workflows: validate-workflow-syntax validate-workflow-logic  ## Run all workflow validation checks

validate-all-files: validate-all-workflows validate-shell-scripts  ## Run all validation checks

detect-secrets: dev-install  ## Detect potential hardcoded secrets in source code
	@echo "Detecting hardcoded secrets..."
	./dev-tools/security/detect_secrets.py

pre-commit-check: dev-install  ## Run all pre-commit validation checks
	@echo "Running pre-commit validation checks..."
	./dev-tools/scripts/workflow_orchestrator.py pre-commit

pre-commit-check-required: dev-install  ## Run only required pre-commit checks (skip warnings)
	@echo "Running required pre-commit validation checks..."
	./dev-tools/scripts/workflow_orchestrator.py pre-commit --required-only

validate-shellcheck: validate-shell-scripts  ## Alias for validate-shell-scripts (backward compatibility)
