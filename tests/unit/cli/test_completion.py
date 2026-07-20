"""Unit tests for orb.cli.completion.

Covers generate_bash_completion and generate_zsh_completion — these are
pure string-generating functions with no side effects.
"""

from __future__ import annotations

import pytest

from orb.cli.completion import generate_bash_completion, generate_zsh_completion


@pytest.mark.unit
class TestGenerateBashCompletion:
    def test_returns_string(self):
        result = generate_bash_completion()
        assert isinstance(result, str)

    def test_starts_with_shebang(self):
        result = generate_bash_completion()
        assert result.strip().startswith("#!/bin/bash")

    def test_contains_completion_function(self):
        result = generate_bash_completion()
        assert "_orb_completion()" in result

    def test_contains_complete_directive(self):
        result = generate_bash_completion()
        assert "complete -F _orb_completion orb" in result

    def test_resources_string_includes_machines(self):
        result = generate_bash_completion()
        assert "machines" in result

    def test_resources_string_includes_templates(self):
        result = generate_bash_completion()
        assert "templates" in result

    def test_resources_string_includes_requests(self):
        result = generate_bash_completion()
        assert "requests" in result

    def test_format_choices_listed(self):
        result = generate_bash_completion()
        assert "json" in result
        assert "yaml" in result
        assert "table" in result

    def test_log_level_choices_listed(self):
        result = generate_bash_completion()
        assert "DEBUG" in result
        assert "INFO" in result
        assert "ERROR" in result

    def test_idempotent_on_repeated_calls(self):
        assert generate_bash_completion() == generate_bash_completion()


@pytest.mark.unit
class TestGenerateZshCompletion:
    def test_returns_string(self):
        result = generate_zsh_completion()
        assert isinstance(result, str)

    def test_starts_with_compdef(self):
        result = generate_zsh_completion()
        assert "#compdef orb" in result

    def test_contains_orb_function(self):
        result = generate_zsh_completion()
        assert "_orb()" in result

    def test_contains_resource_descriptions(self):
        result = generate_zsh_completion()
        assert "templates" in result
        assert "machines" in result
        assert "requests" in result

    def test_contains_orb_resources_function(self):
        result = generate_zsh_completion()
        assert "_orb_resources()" in result

    def test_contains_orb_actions_function(self):
        result = generate_zsh_completion()
        assert "_orb_actions()" in result

    def test_contains_orb_options_function(self):
        result = generate_zsh_completion()
        assert "_orb_options()" in result

    def test_format_choices_listed(self):
        result = generate_zsh_completion()
        assert "json" in result
        assert "yaml" in result

    def test_idempotent_on_repeated_calls(self):
        assert generate_zsh_completion() == generate_zsh_completion()
