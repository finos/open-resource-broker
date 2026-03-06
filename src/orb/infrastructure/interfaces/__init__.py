"""Infrastructure Interfaces Package.

This package contains technical interface definitions for the infrastructure
layer, including contracts for external systems, technical services, and
infrastructure components.

These interfaces define the technical contracts that enable the infrastructure
layer to provide services to the application layer while maintaining loose
coupling and testability.

Key Components:
    - External system interfaces: Contracts for third-party integrations
    - Technical service interfaces: Infrastructure service contracts
    - Adapter interfaces: External system adapter contracts
    - Port interfaces: Technical port definitions

Architecture:
    These interfaces enable the infrastructure layer to provide technical
    services through well-defined contracts, supporting dependency inversion
    and enabling easy mocking and testing.

Usage:
    These interfaces are implemented by concrete infrastructure classes
    and used by application services through dependency injection.

Note:
    Technical interfaces should focus on technical concerns and avoid
    exposing business logic or domain concepts.
"""
