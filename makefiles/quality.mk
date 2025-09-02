# Quality assurance targets
# Security, architecture, validation, and quality gates

# @SECTION Code Quality
quality-check: dev-install  ## Run professional quality checks on modified files
	@echo "Running professional quality checks..."
	./dev-tools/scripts/quality_check.py --strict

quality-check-all: dev-install  ## Run professional quality checks on all files
	@echo "Running professional quality checks on all files..."
	./dev-tools/scripts/quality_check.py --strict --all

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

# Architecture Quality Gates
architecture-check: dev-install  ## Run architecture compliance checks
	@echo "Running architecture quality checks..."
	./dev-tools/scripts/validate_cqrs.py --warn-only
	./dev-tools/scripts/check_architecture.py --warn-only

architecture-report: dev-install  ## Generate detailed architecture report
	@echo "Generating architecture dependency report..."
	./dev-tools/scripts/check_architecture.py --report

quality-gates: lint test architecture-check  ## Run all quality gates
	@echo "All quality gates completed successfully!"

quality-full: lint test architecture-check docs-build  ## Run quality gates and generate docs
	@echo "Full quality check and documentation generation completed!"

# @SECTION Security
security: dev-install  ## Run security checks (bandit, safety)
	./dev-tools/scripts/security_check.py

security-quick: dev-install  ## Run quick security checks only
	./dev-tools/scripts/security_check.py --quick

security-all: dev-install  ## Run all available security tools
	./dev-tools/scripts/security_check.py --all

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
	@echo "### Files Generated:" >> security-report.md
	@echo "- bandit-report.json (Security issues)" >> security-report.md
	@echo "- bandit-results.sarif (Security SARIF)" >> security-report.md
	@echo "- trivy-results.json (Container vulnerabilities)" >> security-report.md
	@echo "- trivy-results.sarif (Container SARIF)" >> security-report.md
	@echo "- hadolint-results.sarif (Dockerfile issues)" >> security-report.md
	@echo "- *-sbom-*.json (Software Bill of Materials)" >> security-report.md
	@echo "" >> security-report.md
	@echo "Security report generated in security-report.md"

detect-secrets: dev-install  ## Detect potential hardcoded secrets in source code
	@echo "Detecting hardcoded secrets..."
	./dev-tools/security/detect_secrets.py

# @SECTION Validation
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

validate-shellcheck: validate-shell-scripts  ## Alias for pre-commit compatibility

validate-all-workflows: validate-workflow-syntax validate-workflow-logic  ## Run all workflow validation checks

validate-all-files: validate-all-workflows validate-shell-scripts  ## Run all validation checks

# @SECTION Container Quality
hadolint-check: ## Check Dockerfiles with hadolint
	./dev-tools/scripts/hadolint_check.py

hadolint-check-container: ## Check Dockerfile with hadolint in container (no local install needed)
	./dev-tools/scripts/run_dev_checks.sh all

dev-checks-container: ## Run all pre-commit checks in container (no local tools needed)
	./dev-tools/scripts/run_dev_checks.sh all

dev-checks-container-required: ## Run only required pre-commit checks in container (skip warnings)
	./dev-tools/scripts/run_dev_checks.sh required

container-health-check:  ## Test container health endpoint (supports HEALTH_CHECK_TIMEOUT, HEALTH_CHECK_URL, HEALTH_CHECK_INTERVAL)
	HEALTH_CHECK_TIMEOUT=$${HEALTH_CHECK_TIMEOUT:-60} \
	HEALTH_CHECK_URL=$${HEALTH_CHECK_URL:-http://localhost:8000/health} \
	HEALTH_CHECK_INTERVAL=$${HEALTH_CHECK_INTERVAL:-3} \
	./dev-tools/scripts/container_health_check.py

# @SECTION File Analysis
file-sizes: dev-install  ## Check file sizes (developer-friendly alias)
	./dev-tools/scripts/check_file_sizes.py --warn-only

file-sizes-report: dev-install  ## Generate detailed file size report
	./dev-tools/scripts/check_file_sizes.py --report
