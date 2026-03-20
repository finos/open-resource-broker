#!/usr/bin/env python3
"""Simple test to verify import fixes."""

import sys


def test_import_fixes():
    """Test that the import fixes are working."""
    from orb.providers.aws.domain.template.value_objects import ProviderApi

    assert ProviderApi is not None


if __name__ == "__main__":
    success = test_import_fixes()
    sys.exit(0 if success else 1)
