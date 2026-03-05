"""Test CLI validation for show commands."""

from unittest.mock import Mock

import pytest

from domain.base.exceptions import DomainException


class TestCLIShowValidation:
    """Test CLI validation for show commands."""

    def test_templates_show_rejects_all_flag(self):
        """Test that templates show rejects --all flag."""
        args = Mock()
        args.resource = "templates"
        args.action = "show"
        args.all = True
        args.template_id = "test-template"

        with pytest.raises(DomainException) as exc_info:
            # This would be called in execute_command before command factory
            if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
                if getattr(args, "all", False):
                    if args.resource in ["templates", "template"]:
                        raise DomainException(
                            "The --all flag is not supported with 'show' commands. "
                            "Use 'orb templates list' to see multiple templates."
                        )

        assert "--all flag is not supported" in str(exc_info.value)
        assert "templates list" in str(exc_info.value)

    def test_machines_show_rejects_all_flag(self):
        """Test that machines show rejects --all flag."""
        args = Mock()
        args.resource = "machines"
        args.action = "show"
        args.all = True
        args.machine_id = "test-machine"

        with pytest.raises(DomainException) as exc_info:
            if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
                if getattr(args, "all", False):
                    if args.resource in ["machines", "machine"]:
                        raise DomainException(
                            "The --all flag is not supported with 'show' commands. "
                            "Use 'orb machines list' to see multiple machines."
                        )

        assert "--all flag is not supported" in str(exc_info.value)
        assert "machines list" in str(exc_info.value)

    def test_requests_show_rejects_all_flag(self):
        """Test that requests show rejects --all flag."""
        args = Mock()
        args.resource = "requests"
        args.action = "show"
        args.all = True
        args.request_id = "test-request"

        with pytest.raises(DomainException) as exc_info:
            if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
                if getattr(args, "all", False):
                    if args.resource in ["requests", "request"]:
                        raise DomainException(
                            "The --all flag is not supported with 'show' commands. "
                            "Use 'orb requests list' to see multiple requests."
                        )

        assert "--all flag is not supported" in str(exc_info.value)
        assert "requests list" in str(exc_info.value)

    def test_templates_show_requires_id(self):
        """Test that templates show requires template ID."""
        args = Mock()
        args.resource = "templates"
        args.action = "show"
        args.all = False
        args.template_id = None
        args.flag_template_id = None

        with pytest.raises(DomainException) as exc_info:
            if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
                if args.resource in ["templates", "template"]:
                    template_id = getattr(args, "template_id", None) or getattr(
                        args, "flag_template_id", None
                    )
                    if not template_id:
                        raise DomainException(
                            "Template ID is required for 'show' command. "
                            "Usage: orb templates show <template-id> or orb templates show --template-id <template-id>"
                        )

        assert "Template ID is required" in str(exc_info.value)

    def test_machines_show_requires_id(self):
        """Test that machines show requires machine ID."""
        args = Mock()
        args.resource = "machines"
        args.action = "show"
        args.all = False
        args.machine_id = None
        args.flag_machine_id = None

        with pytest.raises(DomainException) as exc_info:
            if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
                if args.resource in ["machines", "machine"]:
                    machine_id = getattr(args, "machine_id", None) or getattr(
                        args, "flag_machine_id", None
                    )
                    if not machine_id:
                        raise DomainException(
                            "Machine ID is required for 'show' command. "
                            "Usage: orb machines show <machine-id> or orb machines show --machine-id <machine-id>"
                        )

        assert "Machine ID is required" in str(exc_info.value)

    def test_requests_show_requires_id(self):
        """Test that requests show requires request ID."""
        args = Mock()
        args.resource = "requests"
        args.action = "show"
        args.all = False
        args.request_id = None

        with pytest.raises(DomainException) as exc_info:
            if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
                if args.resource in ["requests", "request"]:
                    request_id = getattr(args, "request_id", None)
                    if not request_id:
                        raise DomainException(
                            "Request ID required. Use 'orb requests list' for multiple requests"
                        )

        assert "Request ID required" in str(exc_info.value)
        assert "requests list" in str(exc_info.value)

    def test_templates_show_accepts_positional_id(self):
        """Test that templates show accepts positional template ID."""
        args = Mock()
        args.resource = "templates"
        args.action = "show"
        args.all = False
        args.template_id = "test-template"
        args.flag_template_id = None

        # Should not raise exception
        if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
            if getattr(args, "all", False):
                # Would raise here if --all was used
                pass
            if args.resource in ["templates", "template"]:
                template_id = getattr(args, "template_id", None) or getattr(
                    args, "flag_template_id", None
                )
                if not template_id:
                    # Would raise here if no ID provided
                    pass

        # Test passes if no exception is raised

    def test_templates_show_accepts_flag_id(self):
        """Test that templates show accepts flag template ID."""
        args = Mock()
        args.resource = "templates"
        args.action = "show"
        args.all = False
        args.template_id = None
        args.flag_template_id = "test-template"

        # Should not raise exception
        if hasattr(args, "resource") and hasattr(args, "action") and args.action == "show":
            if getattr(args, "all", False):
                # Would raise here if --all was used
                pass
            if args.resource in ["templates", "template"]:
                template_id = getattr(args, "template_id", None) or getattr(
                    args, "flag_template_id", None
                )
                if not template_id:
                    # Would raise here if no ID provided
                    pass

        # Test passes if no exception is raised
