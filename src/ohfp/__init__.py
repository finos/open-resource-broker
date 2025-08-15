"""
OHFP (Open Host Factory Plugin) - Main package namespace.

This package provides the ohfp namespace for importing package components.
Users can import as: from ohfp.domain import ... or import ohfp.cli
"""

__version__ = "0.1.0"

# Import submodules using absolute imports
import cli
import domain  
import infrastructure
import application
import config
import api
import providers

__all__ = ["cli", "domain", "infrastructure", "application", "config", "api", "providers"]
