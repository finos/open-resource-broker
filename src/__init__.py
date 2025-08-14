"""Open Host Factory Plugin - Root Package.

This package provides integration between IBM Spectrum Symphony Host Factory
and cloud providers, enabling dynamic provisioning of compute resources.

The plugin implements a Domain-Driven Design (DDD) architecture with CQRS patterns
and event-driven processing, supporting multiple cloud providers through a
provider-agnostic interface.

Key Components:
    - api: API layer for external integrations
    - application: Application services and use cases
    - domain: Core business logic and domain models
    - infrastructure: Technical infrastructure and persistence
    - providers: Cloud provider implementations

Architecture:
    The system follows Clean Architecture principles with clear separation
    between domain logic, application services, and infrastructure concerns.
"""

from ._package import PACKAGE_NAME

__author__ = "AWS Professional Services"
__email__ = "aws-proserve@amazon.com"
__package_name__ = PACKAGE_NAME

"""
Usage:
    The plugin is typically used through the command-line interface:

    >>> python run.py getAvailableTemplates
    >>> python run.py requestMachines --data '{"template_id": "basic", "machine_count": 2}'

Note:
    This plugin requires correct configuration of cloud provider credentials
    and Symphony Host Factory environment variables.
"""
