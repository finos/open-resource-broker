"""Utility helpers for the modern Kubernetes provider.

Contains pure helper functions that do not depend on the kubernetes SDK
at import time (the SDK is imported lazily inside functions that need it
so the architecture test stays clean for utility consumers).
"""
