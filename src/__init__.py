"""Open Host Factory Plugin - Cloud provider integration for IBM Spectrum Symphony Host Factory.

This plugin provides dynamic provisioning of compute resources with a REST API interface
and structured architecture implementation supporting multiple cloud providers.

Key Features:
- Multi-provider architecture (AWS supported)
- REST API with OpenAPI documentation
- Command-line interface
- Clean Architecture with DDD patterns
- CQRS for scalable operations
- Event-driven architecture
- Comprehensive configuration management

Key Components:
    - api: API layer for external integrations
    - application: Application services and use cases
    - domain: Core business logic and domain models
    - infrastructure: Technical infrastructure and persistence
    - providers: Cloud provider implementations

Usage:
    The plugin is typically used through the command-line interface:

    >>> python run.py getAvailableTemplates
    >>> python run.py requestMachines --data '{"template_id": "basic", "machine_count": 2}'

Note:
    This plugin requires correct configuration of cloud provider credentials
    and Symphony Host Factory environment variables.
"""
