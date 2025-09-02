# Common variables and functions for all makefiles
# This file is included by all other makefiles

# Python settings
PYTHON := python3
VENV := .venv
BIN := $(VENV)/bin

# Project configuration (single source of truth)
PROJECT_CONFIG := .project.yml

# Configurable arguments for different environments
TEST_ARGS ?= 
BUILD_ARGS ?=
DOCS_ARGS ?=

# Python version settings (loaded from project config)
PYTHON_VERSIONS := $(shell yq '.python.versions | join(" ")' $(PROJECT_CONFIG))
DEFAULT_PYTHON_VERSION := $(shell yq '.python.default_version' $(PROJECT_CONFIG))

# Package information (loaded from project config, but respect environment VERSION for CI)
PACKAGE_NAME := $(shell yq '.project.name' $(PROJECT_CONFIG))
PACKAGE_NAME_SHORT := $(shell yq '.project.short_name' $(PROJECT_CONFIG))
VERSION ?= $(shell yq '.project.version' $(PROJECT_CONFIG))
AUTHOR := $(shell yq '.project.author' $(PROJECT_CONFIG))
LICENSE := $(shell yq '.project.license' $(PROJECT_CONFIG))

# Repository information (loaded from project config)
REPO_ORG := $(shell yq '.repository.org' $(PROJECT_CONFIG))
REPO_URL := https://github.com/$(REPO_ORG)/$(PACKAGE_NAME)
CONTAINER_REGISTRY := $(shell yq '.repository.registry' $(PROJECT_CONFIG))/$(REPO_ORG)
CONTAINER_IMAGE := $(PACKAGE_NAME)
DOCS_URL := https://$(REPO_ORG).github.io/$(PACKAGE_NAME)

# Project settings
PROJECT := $(PACKAGE_NAME)
PACKAGE := src
TESTS := tests
TESTS_UNIT := $(TESTS)/unit
TESTS_INTEGRATION := $(TESTS)/integration
TESTS_E2E := $(TESTS)/e2e
TESTS_PERFORMANCE := $(TESTS)/performance
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
# Usage: $(call run-tool,tool-name,arguments[,working-dir])
define run-tool
	$(if $(3),cd $(3) && ../dev-tools/scripts/run_tool.sh $(1) $(2),@dev-tools/scripts/run_tool.sh $(1) $(2))
endef

# Virtual environment setup
$(VENV)/bin/activate: uv.lock
	./dev-tools/scripts/venv_setup.py
