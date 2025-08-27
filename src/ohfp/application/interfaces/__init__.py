"""Application Interfaces Package.

This package contains the interface definitions for the application layer,
including command and query interfaces, service contracts, and port definitions.

The interfaces defined here establish the contracts between the application
layer and other layers of the system, enabling dependency inversion and
testability.

Key Components:
    - Command interfaces: Contracts for command handling
    - Query interfaces: Contracts for query processing
    - Service interfaces: Application service contracts
    - Port interfaces: External system port definitions

Architecture:
    These interfaces enable the application layer to depend on abstractions
    rather than concrete implementations, supporting the Dependency Inversion
    Principle and enabling easy testing and extensibility.

Usage:
    These interfaces are implemented by concrete classes in the infrastructure
    layer and used by application services for orchestration.

Note:
    Interfaces should be stable and change infrequently to maintain
    system stability and backward compatibility.
"""
