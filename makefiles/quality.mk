# Quality assurance targets
# Security, architecture, validation, and quality gates

# @SECTION Code Quality
quality-check: dev-install  ## Run professional quality checks on modified files
	@echo "Running professional quality checks..."
	$(call run-tool,python,./dev-tools/scripts/quality_check.py --strict)

quality-check-all: dev-install  ## Run professional quality checks on all files
	@echo "Running professional quality checks on all files..."
	$(call run-tool,python,./dev-tools/scripts/quality_check.py --strict --all)

quality-check-fix: dev-install  ## Run quality checks with auto-fix
	@echo "Running professional quality checks with auto-fix..."
	$(call run-tool,python,./dev-tools/scripts/quality_check.py --fix)

quality-check-files: dev-install  ## Run quality checks on specific files (usage: make quality-check-files FILES="file1.py file2.py")
	@if [ -z "$(FILES)" ]; then \
		echo "Error: FILES is required. Usage: make quality-check-files FILES=\"file1.py file2.py\""; \
		exit 1; \
	fi
	@echo "Running professional quality checks on specified files..."
	$(call run-tool,python,./dev-tools/scripts/quality_check.py --strict --files $(FILES))

format-fix: clean-whitespace  ## Auto-fix code formatting with Ruff
	@uv run ruff format --quiet .
	@uv run ruff check --fix --exit-zero --quiet .

container-health-check: dev-install  ## Run container health checks
	./dev-tools/scripts/container_health_check.py

git-config:  ## Configure git for development
	git config --local user.name "GitHub Actions"
	git config --local user.email "github-actions[bot]@users.noreply.github.com"

lint: dev-install  ## Check enforced rules (fail on issues)
	@uv run ruff check --quiet .
	@uv run ruff format --check --quiet .

lint-optional: dev-install  ## Check optional rules (warnings only)
	@uv run ruff check --select=N,UP,B,PL,C90,RUF --quiet . || true

pre-commit: format lint  ## Simulate pre-commit checks locally
	@echo "All checks passed! Safe to commit."

format: dev-install clean-whitespace  ## Format code with Ruff (no auto-fix)
	@uv run ruff format --check --quiet .

hadolint-check: dev-install  ## Check Dockerfile with hadolint
	./dev-tools/scripts/hadolint_check.py

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
	$(call run-tool,python,./dev-tools/scripts/clean_whitespace.py)

# @SECTION Security
security: dev-install  ## Run security checks (bandit, safety)
	./dev-tools/scripts/security_check.py

security-quick: dev-install  ## Run quick security checks only
	./dev-tools/scripts/security_check.py --quick

security-all: dev-install  ## Run all available security tools
	./dev-tools/scripts/security_check.py --all

ci-security-container: dev-install ## Run container security scans (CI)
	./dev-tools/scripts/security_container.py

security-with-container: dev-install  ## Run security checks including container scans
	./dev-tools/scripts/security_check.py --all --container

security-full: security-with-container sbom-generate  ## Run all security scans including container and SBOM

sbom-generate: dev-install ## Generate Software Bill of Materials (SBOM)
	@echo "Generating SBOM files..."
	@echo "Ensuring required tools are installed..."
	./dev-tools/scripts/install_dev_tools.py --tool syft
	./dev-tools/scripts/install_dev_tools.py --tool pip-audit
	@echo "Generating Python dependency SBOM..."
	$(call run-tool,pip-audit,--format=cyclonedx-json --output=python-sbom-cyclonedx.json)
	$(call run-tool,pip-audit,--format=spdx-json --output=python-sbom-spdx.json)
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
	@echo "### SBOM Files" >> security-report.md
	@echo "- Python SBOM (CycloneDX): python-sbom-cyclonedx.json" >> security-report.md
	@echo "- Python SBOM (SPDX): python-sbom-spdx.json" >> security-report.md
	@echo "- Project SBOM (SPDX): project-sbom-spdx.json" >> security-report.md
	@echo "- Project SBOM (CycloneDX): project-sbom-cyclonedx.json" >> security-report.md
	@echo "- Container SBOM (SPDX): container-sbom-spdx.json" >> security-report.md
	@echo "- Container SBOM (CycloneDX): container-sbom-cyclonedx.json" >> security-report.md

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
	./dev-tools/scripts/check_file_sizes.py --warn-only

file-sizes-report: dev-install  ## Generate detailed file size report
	./dev-tools/scripts/check_file_sizes.py --report

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
	./dev-tools/scripts/pre_commit_check.py

pre-commit-check-required: dev-install  ## Run only required pre-commit checks (skip warnings)
	@echo "Running required pre-commit validation checks..."
	./dev-tools/scripts/pre_commit_check.py --required-only

validate-shellcheck: validate-shell-scripts  ## Alias for validate-shell-scripts (backward compatibility)
