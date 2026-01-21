# Build, deployment, and documentation targets

# @SECTION Container
# Container targets
container: dev-install  ## Build containers (usage: make container [single] [multi] [push] [run] [version])
	@./dev-tools/container/container_dispatcher.py $(filter-out $@,$(MAKECMDGOALS))

container-build-single: dev-install  ## Build single-platform container (CRITICAL: used by container.yml)
	@./dev-tools/container/container_dispatcher.py single

container-build-multi: dev-install  ## Build multi-platform container
	@./dev-tools/container/container_dispatcher.py multi

container-push-multi: dev-install  ## Build and push multi-platform container
	@./dev-tools/container/container_dispatcher.py multi push

container-show-version: dev-install  ## Show container version info
	@./dev-tools/container/container_dispatcher.py version

container-run: dev-install  ## Run container build
	@./dev-tools/container/container_dispatcher.py run

# Dummy targets removed (consolidated in quality.mk)

# @SECTION Documentation
# Documentation targets
docs: dev-install  ## Build/manage docs (usage: make docs [serve] [deploy] [version=X.X.X] [list] [delete=X.X.X] [clean])
	@./dev-tools/docs/docs_dispatcher.py $(filter-out $@,$(MAKECMDGOALS))

docs-build: dev-install  ## Build documentation locally with mike (no push)
	@./dev-tools/docs/docs_dispatcher.py

docs-serve: dev-install  ## Serve versioned documentation locally with live reload
	@./dev-tools/docs/docs_dispatcher.py serve

docs-deploy: dev-install  ## Deploy documentation locally (for testing deployment)
	@./dev-tools/docs/docs_dispatcher.py deploy

docs-deploy-version: dev-install  ## Deploy specific version (usage: make docs-deploy-version VERSION=1.0.0)
	@./dev-tools/docs/docs_dispatcher.py deploy version=$(VERSION)

docs-list-versions:  ## List all documentation versions
	@./dev-tools/docs/docs_dispatcher.py list

docs-delete-version:  ## Delete a documentation version (usage: make docs-delete-version VERSION=1.0.0)
	@./dev-tools/docs/docs_dispatcher.py delete=$(VERSION)

docs-clean:  ## Clean documentation build files
	@./dev-tools/docs/docs_dispatcher.py clean

# CI documentation targets (PRESERVE: used by workflows)
ci-docs-build:  ## Build documentation for CI PR testing (matches docs.yml PR builds)
	@dev-tools/scripts/ci_docs_build.sh

ci-docs-build-for-pages:  ## Build documentation for GitHub Pages deployment (no push)
	@dev-tools/scripts/ci_docs_build_for_pages.sh

ci-docs-deploy:  ## Deploy documentation to GitHub Pages (matches docs.yml main branch)
	@dev-tools/scripts/ci_docs_deploy.sh

# Dummy targets removed (consolidated in quality.mk)

# @SECTION Build & Deploy
build: clean dev-install  ## Build package
	VERSION=$${VERSION:-$$(make -s get-version)} $(MAKE) generate-pyproject && \
	VERSION=$${VERSION:-$$(make -s get-version)} BUILD_ARGS="$(BUILD_ARGS)" ./dev-tools/package/build.sh

semantic-release-build:  ## Build package for semantic-release (minimal dependencies)
	./dev-tools/package/build.sh

build-test: build  ## Build and test package installation
	@echo "Testing package installation..."
	make test-install

test-install: build  ## Test package installation in clean environment
	@echo "Testing package installation in clean environment..."
	# Create temporary virtual environment and test installation
	python -m venv /tmp/test-install-env
	/tmp/test-install-env/bin/pip install dist/*.whl
	/tmp/test-install-env/bin/python -c "import $(PYTHON_MODULE); print('Package installed successfully')"
	rm -rf /tmp/test-install-env

# @SECTION Release Management
release: dev-install ## Unified release command (forward/historical/analysis)
	@./dev-tools/release/release_dispatcher.py $(filter-out $@,$(MAKECMDGOALS))

release-historical: ## Historical release (usage: make release-historical COMMIT=abc123 VERSION=0.0.5)
	@if [ -z "$(COMMIT)" ] || [ -z "$(VERSION)" ]; then \
		echo "ERROR: Both COMMIT and VERSION are required"; \
		echo "Usage: make release-historical COMMIT=abc123 VERSION=0.0.5"; \
		exit 1; \
	fi
	@$(MAKE) release COMMIT=$(COMMIT) VERSION=$(VERSION)

release-analysis: ## RC readiness analysis
	@$(MAKE) release MODE=analysis

release-dry-run: dev-install ## Dry run release (replaces old dry_run_release.sh)
	@echo "Running semantic-release dry run..."
	@$(call run-tool,semantic-release,--noop version)

# Scheduled release targets (used by workflows)
release-alpha-if-needed: ## Create alpha release if there are new commits since last alpha
	@./dev-tools/release/conditional_release.sh alpha

release-beta-if-needed: ## Create beta release if there are new alphas to promote
	@./dev-tools/release/conditional_release.sh beta

release-rc-if-needed: ## Create RC release if there are new betas to promote
	@./dev-tools/release/conditional_release.sh rc

# Version management targets
version-show:  ## Show current version from project config
	@echo "Current version: $(VERSION)"

get-version:  ## Generate unified version (works for PyPI, Docker, Git)
	@if [ "$${IS_RELEASE:-false}" = "true" ]; then \
		echo "$(VERSION)"; \
	else \
		commit=$$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown'); \
		dev_int=$$(python3 -c "print(int('$${commit}'[:6], 16))"); \
		echo "$(VERSION).dev$${dev_int}"; \
	fi

get-container-tags:  ## Calculate container tags for current context
	@./dev-tools/container/calculate_tags.sh

version-bump:  ## Show version bump help
	@echo "Version Management Commands:"
	@echo "  make version-show         - Show current version"
	@echo "  make release              - Create new release"
	@echo "  make release-dry-run      - Test release process"
	@echo ""
	@echo "Current version: $(VERSION)"
