# Makefile for Open Host Factory Plugin
# This is the main Makefile that includes all modular makefiles

.DEFAULT_GOAL := help

# Include all modular makefiles
include makefiles/common.mk
include makefiles/dev.mk
include makefiles/quality.mk
include makefiles/ci.mk
include makefiles/deploy.mk
include makefiles/changelog.mk

.PHONY: help

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@awk 'BEGIN {FS = ":.*## "; section=""} \
		/^# @SECTION / {if(section && length(buffer)>0) {print buffer | "sort"; close("sort"); buffer=""} section=substr($$0,12); print "\n" section ":"} \
		/^[[:alnum:]_-]+:.*## / {if(section) buffer = buffer sprintf("  %-20s %s\n", $$1, $$2)} \
		END {if(section && length(buffer)>0) {print buffer | "sort"; close("sort")}}' $(MAKEFILE_LIST)

# @SECTION Convenience Targets
run: dev-install  ## Run the application in development mode
	@echo "Running application in development mode..."
	uv run python -m $(PACKAGE_NAME_SHORT)

run-dev: dev-install  ## Run application with development settings
	@echo "Running application with development settings..."
	ENVIRONMENT=development uv run python -m $(PACKAGE_NAME_SHORT)

# @SECTION Utility Targets
generate-completions: dev-install  ## Generate shell completions for CLI
	@echo "Generating shell completions..."
	./dev-tools/scripts/install_completions.sh

install-completions: generate-completions  ## Install shell completions
	./dev-tools/scripts/install_completions.sh

install-bash-completions: dev-install  ## Install bash completions only
	./dev-tools/scripts/install_completions.sh bash

install-zsh-completions: dev-install  ## Install zsh completions only
	./dev-tools/scripts/install_completions.sh zsh

uninstall-completions: dev-install  ## Uninstall shell completions
	./dev-tools/scripts/install_completions.sh --uninstall

test-completions: dev-install  ## Test shell completions
	@echo "Testing shell completions..."
	# Add completion testing logic here

# @SECTION Aliases and Shortcuts
lint: format  ## Alias for format (backward compatibility)

security-container: ci-security-container  ## Alias for ci-security-container

validate-workflows: validate-all-workflows  ## Alias for validate-all-workflows

# Make targets that don't correspond to files
.PHONY: help install install-pip dev-install dev-install-pip ci-install requirements-generate test test-unit test-integration test-e2e test-all test-parallel test-quick test-performance test-aws test-cov test-html test-report generate-pyproject deps-add deps-add-dev clean clean-all dev-setup install-package uninstall-package reinstall-package init-db create-config validate-config quick-start dev status uv-lock uv-sync uv-sync-dev uv-check uv-benchmark quality-check quality-check-all quality-check-fix quality-check-files format-fix container-health-check git-config lint lint-optional pre-commit format hadolint-check dev-checks-container dev-checks-container-required format-container hadolint-check-container install-dev-tools install-dev-tools-required install-dev-tools-dry-run clean-whitespace security security-quick security-all ci-security-container security-with-container security-full sbom-generate security-scan security-validate-sarif security-report architecture-check architecture-report quality-gates quality-full file-sizes file-sizes-report validate-workflow-syntax validate-workflow-logic validate-shell-scripts validate-shellcheck validate-all-workflows validate-all-files detect-secrets pre-commit-check pre-commit-check-required ci-quality-ruff ci-quality-ruff-optional ci-quality-radon ci-quality-mypy ci-quality ci-quality-full ci-arch-cqrs ci-arch-clean ci-arch-imports ci-arch-file-sizes ci-architecture ci-security-bandit ci-security-safety ci-security-trivy ci-security-hadolint ci-security-semgrep ci-security-trivy-fs ci-security-trufflehog ci-security ci-build-sbom ci-tests-unit ci-tests-integration ci-tests-e2e ci-tests-matrix ci-tests-performance ci-check ci-check-quick ci-check-verbose ci ci-quick workflow-ci workflow-test-matrix workflow-security docs docs-build ci-docs-build ci-docs-build-for-pages docs-serve docs-deploy ci-docs-deploy docs-deploy-version docs-list-versions docs-delete-version docs-clean version-show get-version version-bump-patch version-bump-minor version-bump-major version-bump build build-test test-install container-build container-build-single container-build-multi container-push-multi container-show-version container-run docker-compose-up docker-compose-down promote-alpha promote-beta promote-rc promote-stable _promote changelog-generate changelog-update changelog-validate changelog-preview changelog-delete changelog-backfill changelog-regenerate changelog-sync-check release-notes-generate release-notes-preview release-backfill git-changelog-since git-unreleased-commits git-last-release run run-dev generate-completions install-completions install-bash-completions install-zsh-completions uninstall-completions test-completions security-container validate-workflows
